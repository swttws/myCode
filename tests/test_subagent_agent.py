import asyncio
from dataclasses import dataclass, replace

from mycode.agent import AgentConfig, AgentLoop, AgentMode
from mycode.agent.events import AgentEventType
from mycode.llm import BaseLLM, ChatMessage, LLMError, MessageOrigin, StreamEvent, StreamEventType
from mycode.permission.models import PermissionDecision, PermissionEffect, PermissionMode
from mycode.prompt import PromptBuildError
from mycode.prompt.models import PromptBuildMetadata, PromptBuildResult
from mycode.subagent.context import ParentAgentSnapshotStore
from mycode.subagent.models import SubAgentNotification, SubAgentTaskState, SubAgentUsage
from mycode.subagent.notifications import SubAgentNotificationInbox
from mycode.tool import ToolDefinition, ToolExecutor, ToolKind, ToolRegistry, ToolResult
from tests.helpers import PassthroughContextManager
from mycode.memory import InMemoryConversationMemory


class ScriptedLLM(BaseLLM):
    def __init__(self, scripts):
        self.scripts = list(scripts)
        self.requests = []
        self.tool_requests = []
        self.on_stream = None

    async def stream_chat(self, messages, tools=None):
        self.requests.append(tuple(messages))
        self.tool_requests.append(tuple(tools or ()))
        if self.on_stream is not None:
            self.on_stream()
        script = self.scripts.pop(0)
        if isinstance(script, BaseException):
            raise script
        for event in script:
            yield event


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


class AllowPermission:
    async def before_tool(self, call, definition, *, plan_only, round_index):
        return PermissionDecision(
            effect=PermissionEffect.ALLOW,
            reason_code="allow",
            message_zh="允许执行。",
            mode=PermissionMode.DEFAULT,
            display_arguments={},
        )

    def denied_result(self, call, decision):
        return ToolResult(ok=False, tool_name=call.name, content={}, error=decision.message_zh)

    async def after_tool(self, call, result):
        return result


@dataclass(frozen=True)
class Turn:
    framework_blocks: tuple = ()
    reminders: tuple = ()


class RecordingPromptBuilder:
    def __init__(self, *, fail=False):
        self.fail = fail
        self.framework_blocks_seen = []

    def begin_turn(self, *, turn_id, plan_only, reminders=(), framework_blocks=()):
        return Turn(framework_blocks=tuple(framework_blocks), reminders=tuple(reminders))

    def build(self, *, history, tools, turn, round_index):
        if self.fail:
            raise PromptBuildError("prompt failed")
        self.framework_blocks_seen.append(tuple(turn.framework_blocks))
        messages = list(history)
        if turn.framework_blocks:
            messages.append(
                ChatMessage(
                    role="user",
                    content="\n".join(block.content for block in turn.framework_blocks),
                    origin=MessageOrigin.FRAMEWORK_CONTEXT,
                )
            )
        return PromptBuildResult(
            messages=tuple(messages),
            tools=tuple(tools),
            metadata=PromptBuildMetadata(("test",), "sha", ()),
        )


async def collect(async_iterable):
    return [item async for item in async_iterable]


def make_loop(
    *,
    llm,
    prompt_builder=None,
    snapshot_store=None,
    inbox=None,
    main_model_id="parent-model",
    permission_mode_provider=lambda: PermissionMode.DEFAULT,
):
    memory = InMemoryConversationMemory()
    registry = ToolRegistry([NoopTool()])
    return (
        AgentLoop(
            llm=llm,
            memory=memory,
            tool_executor=ToolExecutor(registry),
            tool_registry=registry,
            permission=AllowPermission(),
            context_manager=PassthroughContextManager(memory),
            config=AgentConfig(max_rounds=3),
            prompt_builder=prompt_builder or RecordingPromptBuilder(),
            parent_snapshot_store=snapshot_store,
            notification_inbox=inbox,
            main_model_id=main_model_id,
            permission_mode_provider=permission_mode_provider,
        ),
        memory,
        registry,
    )


def enqueue_notification(inbox, *, task_id="task-000001", sequence=1):
    inbox.enqueue(
        sequence=sequence,
        notification=SubAgentNotification(
            task_id=task_id,
            state=SubAgentTaskState.COMPLETED,
            summary="后台任务完成",
            summary_truncated=False,
            usage=SubAgentUsage(input_tokens=1),
        ),
    )


def test_agent_loop_updates_parent_snapshot_after_successful_prompt_prepare():
    store = ParentAgentSnapshotStore()
    llm = ScriptedLLM([[StreamEvent(StreamEventType.TEXT_DELTA, "ok"), StreamEvent(StreamEventType.DONE)]])
    captured = []
    llm.on_stream = lambda: captured.append(store.current())
    loop, _memory, registry = make_loop(
        llm=llm,
        snapshot_store=store,
        main_model_id="parent-real-model",
        permission_mode_provider=lambda: PermissionMode.PERMISSIVE,
    )

    events = asyncio.run(collect(loop.run("hello", mode=AgentMode())))
    snapshot = captured[0]

    assert events[-1].type is AgentEventType.FINAL_RESPONSE
    assert snapshot.messages == llm.requests[0]
    assert [tool.name for tool in snapshot.tools] == [tool.name for tool in registry.definitions()]
    assert snapshot.model_id == "parent-real-model"
    assert snapshot.max_rounds == 3
    assert snapshot.permission_mode is PermissionMode.PERMISSIVE


def test_agent_loop_injects_subagent_notifications_once_and_does_not_store_them_in_memory():
    inbox = SubAgentNotificationInbox()
    enqueue_notification(inbox)
    prompt_builder = RecordingPromptBuilder()
    llm = ScriptedLLM(
        [
            [StreamEvent(StreamEventType.TEXT_DELTA, "first"), StreamEvent(StreamEventType.DONE)],
            [StreamEvent(StreamEventType.TEXT_DELTA, "second"), StreamEvent(StreamEventType.DONE)],
        ]
    )
    loop, memory, _registry = make_loop(llm=llm, prompt_builder=prompt_builder, inbox=inbox)

    asyncio.run(collect(loop.run("hello", mode=AgentMode())))
    asyncio.run(collect(loop.run("again", mode=AgentMode())))

    first_framework_messages = [
        message for message in llm.requests[0] if message.origin is MessageOrigin.FRAMEWORK_CONTEXT
    ]
    second_framework_messages = [
        message for message in llm.requests[1] if message.origin is MessageOrigin.FRAMEWORK_CONTEXT
    ]
    assert len(first_framework_messages) == 1
    assert "task-000001" in first_framework_messages[0].content
    assert second_framework_messages == []
    assert inbox.reserve() is None
    assert all(message.origin is not MessageOrigin.FRAMEWORK_CONTEXT for message in memory.messages())


def test_agent_loop_releases_reserved_notification_when_prompt_build_fails():
    inbox = SubAgentNotificationInbox()
    enqueue_notification(inbox)
    loop, _memory, _registry = make_loop(
        llm=ScriptedLLM([]),
        prompt_builder=RecordingPromptBuilder(fail=True),
        inbox=inbox,
    )

    events = asyncio.run(collect(loop.run("hello", mode=AgentMode())))

    assert events[-1].type is AgentEventType.ERROR
    reservation = inbox.reserve()
    assert reservation is not None
    assert reservation.notifications[0].task_id == "task-000001"


def test_agent_loop_commits_reserved_notification_before_model_error():
    inbox = SubAgentNotificationInbox()
    enqueue_notification(inbox)
    loop, _memory, _registry = make_loop(
        llm=ScriptedLLM([LLMError("network failed")]),
        inbox=inbox,
    )

    events = asyncio.run(collect(loop.run("hello", mode=AgentMode())))

    assert events[-1].type is AgentEventType.ERROR
    assert inbox.reserve() is None
