from __future__ import annotations

import asyncio
import json
from pathlib import Path

from mycode.agent import AgentConfig, AgentEventType, AgentLoop, AgentMode
from mycode.compact.models import (
    CompactAction,
    CompactReport,
    CompactStatus,
    PreparedContext,
    RequestSnapshot,
    TokenEstimate,
)
from mycode.hook.models import HookContext, HookEvent, HookTriggerResult
from mycode.llm import BaseLLM, ChatMessage, LLMError, MessageOrigin, StreamEvent, StreamEventType
from mycode.memory import InMemoryConversationMemory
from mycode.prompt.models import (
    EnvironmentSnapshot,
    PromptBuildMetadata,
    PromptBuildResult,
    PromptContextBlock,
    TurnPromptContext,
)
from mycode.permission.models import PermissionDecision, PermissionEffect, PermissionMode
from mycode.tool import ToolCall, ToolDefinition, ToolExecutor, ToolKind, ToolRegistry, ToolResult


async def collect_async(async_iterable):
    return [item async for item in async_iterable]


class ScriptedLLM(BaseLLM):
    def __init__(self, scripts):
        self.scripts = list(scripts)
        self.requests: list[list[ChatMessage]] = []
        self.tool_requests = []

    async def stream_chat(self, messages, tools=None):
        self.requests.append(list(messages))
        self.tool_requests.append(list(tools or ()))
        script = self.scripts.pop(0)
        if isinstance(script, Exception):
            raise script
        for event in script:
            yield event


class PassthroughContextManager:
    def __init__(self, memory):
        self.memory = memory
        self.report = CompactReport(
            status=CompactStatus.SAFE,
            actions=(CompactAction.NONE,),
            before_tokens=0,
            after_tokens=0,
            archived_count=0,
            attempts=0,
            circuit_open=False,
        )

    async def prepare_auto(self, *, build_request, run_deadline):
        request = build_request(tuple(self.memory.messages()))
        return PreparedContext(
            request=request,
            snapshot=RequestSnapshot(ascii_chars=0, non_ascii_chars=0, fingerprint="test"),
            estimate=TokenEstimate(tokens=0, source="full_chars", delta_tokens=0),
            report=self.report,
        )

    def record_usage(self, snapshot, usage):
        return None

    def clear(self):
        self.memory.clear()


class SimplePromptBuilder:
    def __init__(self) -> None:
        self.begin_calls: list[tuple[PromptContextBlock, ...]] = []

    def begin_turn(self, *, turn_id, plan_only, framework_blocks=()):
        blocks = tuple(framework_blocks)
        self.begin_calls.append(blocks)
        return TurnPromptContext(
            turn_id=turn_id,
            environment=EnvironmentSnapshot("workspace", "TestOS", "time", "UTC", "main", "", ()),
            plan_only=plan_only,
            reminders=(),
            framework_blocks=blocks,
        )

    def build(self, *, history, tools, turn, round_index):
        framework_messages = tuple(
            ChatMessage(
                role="system",
                content=block.content,
                origin=MessageOrigin.FRAMEWORK_CONTEXT,
            )
            for block in turn.framework_blocks
        )
        return PromptBuildResult(
            messages=framework_messages + tuple(history),
            tools=tuple(tools),
            metadata=PromptBuildMetadata((), "test", ()),
        )


class NoopTool:
    @property
    def definition(self):
        return ToolDefinition(
            name="noop",
            description="No operation.",
            parameters={"type": "object", "properties": {}, "required": []},
            kind=ToolKind.READ,
        )

    def execute(self, arguments):
        return ToolResult(ok=True, tool_name="noop", content={})


class FakePermission:
    def __init__(self, effects: dict[str, PermissionEffect] | None = None) -> None:
        self.effects = effects or {}

    async def before_tool(self, call, definition, *, plan_only, round_index):
        effect = self.effects.get(call.name, PermissionEffect.ALLOW)
        return PermissionDecision(
            effect=effect,
            reason_code=f"fake_{effect.value}",
            message_zh=f"permission {effect.value}",
            mode=PermissionMode.DEFAULT,
            display_arguments={},
        )

    def denied_result(self, call, decision):
        return ToolResult(
            ok=False,
            tool_name=call.name,
            content={"tool_call_id": call.id, "reason_code": decision.reason_code},
            error=decision.message_zh,
        )

    async def after_tool(self, call, result):
        return result


class RecordingHookRuntime:
    def __init__(
        self,
        blocks: tuple[PromptContextBlock, ...] = (),
        blocked_tool_result: ToolResult | None = None,
    ) -> None:
        self.contexts: list[HookContext] = []
        self.blocks = blocks
        self.blocked_tool_result = blocked_tool_result
        self.before_calls = []
        self.after_calls = []
        self.clear_count = 0

    async def trigger(self, context: HookContext) -> HookTriggerResult:
        self.contexts.append(context)
        return HookTriggerResult(actions=())

    async def before_tool(self, **kwargs) -> HookTriggerResult:
        self.before_calls.append(kwargs)
        return HookTriggerResult(actions=(), blocked_tool_result=self.blocked_tool_result)

    async def after_tool(self, **kwargs) -> HookTriggerResult:
        self.after_calls.append(kwargs)
        return HookTriggerResult(actions=())

    def prompt_blocks(self) -> tuple[PromptContextBlock, ...]:
        return self.blocks

    def clear_request_state(self) -> None:
        self.clear_count += 1


class RecordingTool:
    def __init__(self, name: str = "write_a", kind: ToolKind = ToolKind.WRITE) -> None:
        self.calls = []
        self._definition = ToolDefinition(
            name=name,
            description="recording tool",
            parameters={"type": "object", "properties": {}, "required": []},
            kind=kind,
        )

    @property
    def definition(self):
        return self._definition

    def execute(self, arguments):
        self.calls.append(arguments)
        return ToolResult(True, self.definition.name, {"executed": True})


def make_loop(llm, memory=None, hook_runtime=None, tools=None, permission=None):
    memory = memory or InMemoryConversationMemory()
    registry = ToolRegistry(tools or [NoopTool()])
    return AgentLoop(
        llm=llm,
        memory=memory,
        tool_executor=ToolExecutor(registry),
        tool_registry=registry,
        permission=permission or FakePermission(),
        context_manager=PassthroughContextManager(memory),
        prompt_builder=SimplePromptBuilder(),
        config=AgentConfig(max_rounds=2),
        hook_runtime=hook_runtime,
    )


def test_agent_loop_triggers_lifecycle_events_for_successful_request() -> None:
    hook_runtime = RecordingHookRuntime()
    llm = ScriptedLLM(
        [[StreamEvent(StreamEventType.TEXT_DELTA, "done"), StreamEvent(StreamEventType.DONE)]]
    )
    loop = make_loop(llm, hook_runtime=hook_runtime)

    events = asyncio.run(collect_async(loop.run("hello", mode=AgentMode())))

    assert events[-1].type is AgentEventType.FINAL_RESPONSE
    assert [context.event for context in hook_runtime.contexts] == [
        HookEvent.USER_REQUEST_START,
        HookEvent.USER_MESSAGE,
        HookEvent.MODEL_ROUND_START,
        HookEvent.MODEL_ROUND_END,
        HookEvent.ASSISTANT_MESSAGE,
        HookEvent.USER_REQUEST_END,
    ]
    assert hook_runtime.contexts[0].user_text == "hello"
    assert hook_runtime.contexts[1].message is not None
    assert hook_runtime.contexts[1].message.role == "user"
    assert hook_runtime.contexts[2].round_index == 1
    assert hook_runtime.clear_count == 1


def test_hook_prompt_blocks_enter_model_request_not_memory() -> None:
    memory = InMemoryConversationMemory()
    hook_runtime = RecordingHookRuntime(
        (
            PromptContextBlock(
                id="hook:test",
                kind="hook",
                priority=-150,
                content="hook reminder",
            ),
        )
    )
    llm = ScriptedLLM([[StreamEvent(StreamEventType.DONE)]])
    loop = make_loop(llm, memory=memory, hook_runtime=hook_runtime)

    asyncio.run(collect_async(loop.run("hello", mode=AgentMode())))

    assert any(message.content == "hook reminder" for message in llm.requests[0])
    assert all(message.content != "hook reminder" for message in memory.messages())


def test_hook_request_state_is_cleared_when_llm_errors() -> None:
    hook_runtime = RecordingHookRuntime()
    loop = make_loop(ScriptedLLM([LLMError("network failed")]), hook_runtime=hook_runtime)

    events = asyncio.run(collect_async(loop.run("hello", mode=AgentMode())))

    assert events[-1].type is AgentEventType.ERROR
    assert hook_runtime.clear_count == 1
    assert HookEvent.RUNTIME_ERROR in [context.event for context in hook_runtime.contexts]
    assert HookEvent.USER_REQUEST_END in [context.event for context in hook_runtime.contexts]


def test_agent_loop_without_hook_runtime_still_works() -> None:
    llm = ScriptedLLM(
        [[StreamEvent(StreamEventType.TEXT_DELTA, "ok"), StreamEvent(StreamEventType.DONE)]]
    )
    loop = make_loop(llm)

    events = asyncio.run(collect_async(loop.run("hello", mode=AgentMode())))

    assert [event.type for event in events] == [
        AgentEventType.USER_MESSAGE,
        AgentEventType.TEXT_DELTA,
        AgentEventType.FINAL_RESPONSE,
    ]


def test_tool_before_hook_blocks_allowed_tool_and_model_sees_result() -> None:
    tool = RecordingTool()
    tool_call = ToolCall("call-1", "write_a", {}, raw_arguments="{}")
    blocked = ToolResult(
        ok=False,
        tool_name="write_a",
        content={
            "tool_call_id": "call-1",
            "reason_code": "hook_blocked",
            "hook_rule_id": "block-write",
        },
        error="Hook 拒绝执行。",
    )
    hook_runtime = RecordingHookRuntime(blocked_tool_result=blocked)
    llm = ScriptedLLM(
        [
            [StreamEvent(StreamEventType.TOOL_CALL, tool_call=tool_call), StreamEvent(StreamEventType.DONE)],
            [StreamEvent(StreamEventType.TEXT_DELTA, "adjusted"), StreamEvent(StreamEventType.DONE)],
        ]
    )
    memory = InMemoryConversationMemory()
    loop = make_loop(llm, memory=memory, tools=[tool], hook_runtime=hook_runtime)

    events = asyncio.run(collect_async(loop.run("hello", mode=AgentMode())))

    assert tool.calls == []
    assert len(hook_runtime.before_calls) == 1
    tool_result_event = next(event for event in events if event.type is AgentEventType.TOOL_RESULT)
    assert tool_result_event.tool_result == blocked
    assert any("hook_blocked" in message.content for message in llm.requests[1])
    assert events[-1].content == "adjusted"


def test_permission_denied_tool_does_not_reach_hook_or_executor() -> None:
    tool = RecordingTool()
    tool_call = ToolCall("call-1", "write_a", {}, raw_arguments="{}")
    hook_runtime = RecordingHookRuntime()
    llm = ScriptedLLM(
        [
            [StreamEvent(StreamEventType.TOOL_CALL, tool_call=tool_call), StreamEvent(StreamEventType.DONE)],
            [StreamEvent(StreamEventType.TEXT_DELTA, "adjusted"), StreamEvent(StreamEventType.DONE)],
        ]
    )
    loop = make_loop(
        llm,
        tools=[tool],
        hook_runtime=hook_runtime,
        permission=FakePermission({"write_a": PermissionEffect.DENY}),
    )

    events = asyncio.run(collect_async(loop.run("hello", mode=AgentMode())))

    assert tool.calls == []
    assert hook_runtime.before_calls == []
    denied = next(event for event in events if event.type is AgentEventType.TOOL_RESULT)
    assert denied.tool_result.content["reason_code"] == "fake_deny"


def test_successful_tool_triggers_after_tool_and_tool_result_message() -> None:
    tool = RecordingTool("read_a", kind=ToolKind.READ)
    tool_call = ToolCall("call-1", "read_a", {}, raw_arguments="{}")
    hook_runtime = RecordingHookRuntime()
    llm = ScriptedLLM(
        [
            [StreamEvent(StreamEventType.TOOL_CALL, tool_call=tool_call), StreamEvent(StreamEventType.DONE)],
            [StreamEvent(StreamEventType.TEXT_DELTA, "done"), StreamEvent(StreamEventType.DONE)],
        ]
    )
    memory = InMemoryConversationMemory()
    loop = make_loop(llm, memory=memory, tools=[tool], hook_runtime=hook_runtime)

    events = asyncio.run(collect_async(loop.run("hello", mode=AgentMode())))

    assert tool.calls == [{}]
    assert len(hook_runtime.after_calls) == 1
    tool_result = next(event.tool_result for event in events if event.type is AgentEventType.TOOL_RESULT)
    assert hook_runtime.after_calls[0]["result"] == tool_result
    assert HookEvent.TOOL_RESULT_MESSAGE in [context.event for context in hook_runtime.contexts]
    tool_messages = [message for message in memory.messages() if message.role == "tool"]
    assert json.loads(tool_messages[0].content)["content"] == {"executed": True}
