import json
import asyncio
import time

import pytest

from mycode.agent import (
    AgentConfig,
    AgentLoop,
    AgentErrorCode,
    AgentEventType,
    AgentMode,
    make_assistant_text_message,
    make_assistant_tool_call_message,
    make_system_message,
    make_tool_result_message,
    make_user_message,
)
from mycode.llm import BaseLLM, ChatMessage, LLMError, StreamEvent, StreamEventType
from mycode.llm import MessageOrigin
from mycode.mcp import MCPToolWrapper, RemoteTool, ToolSearch
from mycode.memory import InMemoryConversationMemory
from mycode.permission.models import (
    ApprovalDecision,
    ApprovalDecisionType,
    PermissionDecision,
    PermissionEffect,
    PermissionMode,
)
from mycode.permission.service import PermissionInterceptor, PermissionService
from mycode.tool import ToolCall, ToolDefinition, ToolExecutor, ToolKind, ToolRegistry, ToolResult


class ScriptedLLM(BaseLLM):
    def __init__(self, scripts):
        self.scripts = list(scripts)
        self.requests: list[list[ChatMessage]] = []
        self.tool_requests = []

    async def stream_chat(self, messages, tools=None):
        self.requests.append(list(messages))
        self.tool_requests.append(tools)
        script = self.scripts.pop(0)
        if isinstance(script, Exception):
            raise script
        for event in script:
            yield event


class HangingLLM(BaseLLM):
    async def stream_chat(self, messages, tools=None):
        await asyncio.sleep(1)
        yield StreamEvent(StreamEventType.DONE)


class CancelledLLM(BaseLLM):
    async def stream_chat(self, messages, tools=None):
        raise asyncio.CancelledError
        yield StreamEvent(StreamEventType.DONE)


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


class EchoTool:
    @property
    def definition(self):
        return ToolDefinition(
            name="echo",
            description="Echo text.",
            parameters={"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]},
            kind=ToolKind.READ,
        )

    def execute(self, arguments):
        return ToolResult(ok=True, tool_name="echo", content={"text": arguments["text"]})


class TimedTool:
    def __init__(self, name: str, kind: ToolKind, records: dict[str, dict[str, float]], delay: float = 0.02) -> None:
        self._definition = ToolDefinition(
            name=name,
            description=f"{name} timed tool.",
            parameters={"type": "object", "properties": {}, "required": []},
            kind=kind,
        )
        self._records = records
        self._delay = delay

    @property
    def definition(self):
        return self._definition

    def execute(self, arguments):
        self._records[self.definition.name] = {"start": time.monotonic()}
        time.sleep(self._delay)
        self._records[self.definition.name]["end"] = time.monotonic()
        return ToolResult(ok=True, tool_name=self.definition.name, content={"name": self.definition.name})


class StaticResultTool:
    def __init__(self, name: str, result: ToolResult, kind: ToolKind = ToolKind.WRITE) -> None:
        self._definition = ToolDefinition(
            name=name,
            description=f"{name} static result tool.",
            parameters={"type": "object", "properties": {}, "required": []},
            kind=kind,
        )
        self.result = result

    @property
    def definition(self):
        return self._definition

    def execute(self, arguments):
        return self.result


class RecordingTool(StaticResultTool):
    def __init__(self, name="write_a", kind=ToolKind.WRITE):
        super().__init__(name, ToolResult(True, name, {"executed": True}), kind=kind)
        self.calls = []

    def execute(self, arguments):
        self.calls.append(arguments)
        return self.result


class FakePermission:
    def __init__(self, effects=None):
        self.effects = effects or {}

    async def before_tool(self, call, definition, *, plan_only, round_index):
        effect = self.effects.get(call.name, PermissionEffect.ALLOW)
        return PermissionDecision(
            effect=effect,
            reason_code=f"fake_{effect.value}",
            message_zh=f"测试权限决定：{effect.value}",
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


class AgentFakeMCPPool:
    def __init__(self, *, available=True):
        self.available = available
        self.calls = []
        self.tools = ()

    def is_available(self, server_name):
        return self.available

    async def ensure_available(self, server_name):
        return self.available

    async def call_tool(self, server_name, remote_name, arguments):
        self.calls.append((server_name, remote_name, arguments))
        return ToolResult(
            ok=True,
            tool_name=f"{server_name}__{remote_name}",
            content={"remote": True},
        )


def make_remote_tool(name="echo", *, kind=ToolKind.WRITE):
    return RemoteTool(
        server_name="server",
        remote_name=name,
        public_name=f"server__{name}",
        description=f"Remote {name} description.",
        parameters={
            "type": "object",
            "properties": {"schema_secret": {"type": "string"}},
        },
        kind=kind,
    )


def make_mcp_loop(llm, memory, remote, pool, permission=None):
    pool.tools = (remote,)
    registry = ToolRegistry()
    registry.register(MCPToolWrapper(remote, pool))
    registry.register(ToolSearch(registry, pool))
    return (
        AgentLoop(
            llm=llm,
            memory=memory,
            tool_executor=ToolExecutor(registry),
            tool_registry=registry,
            permission=permission or FakePermission(),
        ),
        registry,
    )


async def collect_async(async_iterable):
    items = []
    async for item in async_iterable:
        items.append(item)
    return items


def make_loop(llm, memory=None, tools=None, permission=None):
    registry = ToolRegistry(tools or [NoopTool()])
    return AgentLoop(
        llm=llm,
        memory=memory or InMemoryConversationMemory(),
        tool_executor=ToolExecutor(registry),
        tool_registry=registry,
        permission=permission or FakePermission(),
    )


def make_configured_loop(llm, memory=None, tools=None, config=None, permission=None):
    registry = ToolRegistry(tools or [NoopTool()])
    return AgentLoop(
        llm=llm,
        memory=memory or InMemoryConversationMemory(),
        tool_executor=ToolExecutor(registry),
        tool_registry=registry,
        config=config,
        permission=permission or FakePermission(),
    )


def test_agent_history_helpers_create_expected_messages():
    call = ToolCall(
        id="call-1",
        name="read_file",
        arguments={"path": "README.md"},
        raw_arguments='{"path":"README.md"}',
    )
    result = ToolResult(ok=True, tool_name="read_file", content={"text": "hello"})

    assert make_system_message("prompt").role == "system"
    assert make_system_message("prompt").content == "prompt"
    assert make_user_message("hi") == ChatMessage(role="user", content="hi")
    assert make_assistant_text_message("ok") == ChatMessage(role="assistant", content="ok")
    assert make_assistant_tool_call_message(call) == ChatMessage(
        role="assistant",
        content="",
        tool_call_id="call-1",
        tool_name="read_file",
        tool_arguments='{"path":"README.md"}',
    )

    tool_message = make_tool_result_message(call, result)

    assert tool_message.role == "tool"
    assert tool_message.tool_call_id == "call-1"
    assert json.loads(tool_message.content) == {
        "ok": True,
        "tool_name": "read_file",
        "content": {"text": "hello"},
        "error": None,
    }


def test_agent_loop_streams_text_and_final_response():
    memory = InMemoryConversationMemory()
    llm = ScriptedLLM(
        [
            [
                StreamEvent(StreamEventType.TEXT_DELTA, "hi"),
                StreamEvent(StreamEventType.TEXT_DELTA, " there"),
                StreamEvent(StreamEventType.DONE),
            ]
        ]
    )
    loop = make_loop(llm, memory)

    events = asyncio.run(collect_async(loop.run("hello", mode=AgentMode())))

    assert [event.type for event in events] == [
        AgentEventType.USER_MESSAGE,
        AgentEventType.TEXT_DELTA,
        AgentEventType.TEXT_DELTA,
        AgentEventType.FINAL_RESPONSE,
    ]
    assert [event.content for event in events] == ["hello", "hi", " there", "hi there"]
    assert memory.messages() == [
        ChatMessage(role="user", content="hello"),
        ChatMessage(role="assistant", content="hi there"),
    ]
    assert llm.requests[0][0].role == "system"
    assert llm.requests[0][1] == ChatMessage(role="user", content="hello")
    assert llm.requests[0][-1].content.startswith("<environment-context>")


def test_agent_loop_streams_thinking_without_storing_it():
    memory = InMemoryConversationMemory()
    llm = ScriptedLLM(
        [
            [
                StreamEvent(StreamEventType.THINKING_DELTA, "hidden"),
                StreamEvent(StreamEventType.TEXT_DELTA, "visible"),
                StreamEvent(StreamEventType.DONE),
            ]
        ]
    )
    loop = make_loop(llm, memory)

    events = asyncio.run(collect_async(loop.run("hello", mode=AgentMode())))

    assert [event.type for event in events] == [
        AgentEventType.USER_MESSAGE,
        AgentEventType.THINKING_DELTA,
        AgentEventType.TEXT_DELTA,
        AgentEventType.FINAL_RESPONSE,
    ]
    assert events[1].content == "hidden"
    assert memory.messages() == [
        ChatMessage(role="user", content="hello"),
        ChatMessage(role="assistant", content="visible"),
    ]


def test_agent_loop_converts_llm_error_to_agent_error():
    memory = InMemoryConversationMemory()
    llm = ScriptedLLM([LLMError("network failed")])
    loop = make_loop(llm, memory)

    events = asyncio.run(collect_async(loop.run("hello", mode=AgentMode())))

    assert [event.type for event in events] == [AgentEventType.USER_MESSAGE, AgentEventType.ERROR]
    assert events[-1].error_code.value == "llm_error"
    assert "network failed" in events[-1].content
    assert memory.messages() == [ChatMessage(role="user", content="hello")]


def test_agent_loop_finishes_when_model_done_without_tool_calls():
    memory = InMemoryConversationMemory()
    llm = ScriptedLLM([[StreamEvent(StreamEventType.DONE)]])
    loop = make_loop(llm, memory)

    events = asyncio.run(collect_async(loop.run("hello", mode=AgentMode())))

    assert [event.type for event in events] == [AgentEventType.USER_MESSAGE, AgentEventType.FINAL_RESPONSE]
    assert events[-1].content == ""
    assert len(llm.requests) == 1
    assert memory.messages() == [ChatMessage(role="user", content="hello")]


def test_agent_loop_executes_tool_and_continues_to_final_response():
    memory = InMemoryConversationMemory()
    tool_call = ToolCall(
        id="call-1",
        name="echo",
        arguments={"text": "hi"},
        raw_arguments='{"text":"hi"}',
    )
    llm = ScriptedLLM(
        [
            [StreamEvent(StreamEventType.TOOL_CALL, tool_call=tool_call), StreamEvent(StreamEventType.DONE)],
            [StreamEvent(StreamEventType.TEXT_DELTA, "done"), StreamEvent(StreamEventType.DONE)],
        ]
    )
    loop = make_loop(llm, memory, tools=[EchoTool()])

    events = asyncio.run(collect_async(loop.run("hello", mode=AgentMode())))

    assert [event.type for event in events] == [
        AgentEventType.USER_MESSAGE,
        AgentEventType.TOOL_CALL_STARTED,
        AgentEventType.TOOL_RESULT,
        AgentEventType.TEXT_DELTA,
        AgentEventType.FINAL_RESPONSE,
    ]
    assert events[1].tool_call == tool_call
    assert events[2].tool_result == ToolResult(ok=True, tool_name="echo", content={"text": "hi"})
    assert len(llm.requests) == 2
    assert llm.requests[1][0].role == "system"
    assert llm.requests[1][1] == ChatMessage(role="user", content="hello")
    assert llm.requests[1][2] == ChatMessage(
            role="assistant",
            content="",
            tool_call_id="call-1",
            tool_name="echo",
            tool_arguments='{"text":"hi"}',
        )
    assert llm.requests[1][3] == ChatMessage(
            role="tool",
            content=json.dumps(
                {"ok": True, "tool_name": "echo", "content": {"text": "hi"}, "error": None},
                ensure_ascii=False,
            ),
            tool_call_id="call-1",
        )
    assert llm.requests[1][-1].content.startswith("<environment-context>")


def test_agent_loop_errors_when_max_rounds_exceeded():
    memory = InMemoryConversationMemory()
    first_call = ToolCall(id="call-1", name="echo", arguments={"text": "one"}, raw_arguments='{"text":"one"}')
    second_call = ToolCall(id="call-2", name="echo", arguments={"text": "two"}, raw_arguments='{"text":"two"}')
    llm = ScriptedLLM(
        [
            [StreamEvent(StreamEventType.TOOL_CALL, tool_call=first_call), StreamEvent(StreamEventType.DONE)],
            [StreamEvent(StreamEventType.TOOL_CALL, tool_call=second_call), StreamEvent(StreamEventType.DONE)],
        ]
    )
    loop = make_configured_loop(llm, memory, tools=[EchoTool()], config=AgentConfig(max_rounds=2))

    events = asyncio.run(collect_async(loop.run("hello", mode=AgentMode())))

    assert len(llm.requests) == 2
    assert events[-1].type == AgentEventType.ERROR
    assert events[-1].error_code == AgentErrorCode.MAX_ROUNDS_EXCEEDED
    assert "max rounds" in events[-1].content


def test_agent_loop_batches_read_tools_and_serializes_writes():
    records: dict[str, dict[str, float]] = {}
    calls = [
        ToolCall(id="call-read-a", name="read_a", arguments={}, raw_arguments="{}"),
        ToolCall(id="call-read-b", name="read_b", arguments={}, raw_arguments="{}"),
        ToolCall(id="call-write-a", name="write_a", arguments={}, raw_arguments="{}"),
        ToolCall(id="call-read-c", name="read_c", arguments={}, raw_arguments="{}"),
    ]
    llm = ScriptedLLM(
        [
            [*(StreamEvent(StreamEventType.TOOL_CALL, tool_call=tool_call) for tool_call in calls), StreamEvent(StreamEventType.DONE)],
            [StreamEvent(StreamEventType.TEXT_DELTA, "done"), StreamEvent(StreamEventType.DONE)],
        ]
    )
    loop = make_loop(
        llm,
        tools=[
            TimedTool("read_a", ToolKind.READ, records),
            TimedTool("read_b", ToolKind.READ, records),
            TimedTool("write_a", ToolKind.WRITE, records),
            TimedTool("read_c", ToolKind.READ, records),
        ],
    )

    events = asyncio.run(collect_async(loop.run("hello", mode=AgentMode())))

    started = [event.tool_call.name for event in events if event.type == AgentEventType.TOOL_CALL_STARTED]
    results = [event.tool_result.tool_name for event in events if event.type == AgentEventType.TOOL_RESULT]
    assert started == ["read_a", "read_b", "write_a", "read_c"]
    assert results == ["read_a", "read_b", "write_a", "read_c"]
    assert records["read_b"]["start"] < records["read_a"]["end"]
    assert records["write_a"]["start"] >= max(records["read_a"]["end"], records["read_b"]["end"])
    assert records["read_c"]["start"] >= records["write_a"]["end"]


def test_agent_loop_serializes_read_approvals_then_runs_approved_reads_concurrently(tmp_path):
    records: dict[str, dict[str, float]] = {}
    calls = [
        ToolCall(id="call-a", name="read_a", arguments={}, raw_arguments="{}"),
        ToolCall(id="call-b", name="read_b", arguments={}, raw_arguments="{}"),
    ]
    llm = ScriptedLLM(
        [
            [*(StreamEvent(StreamEventType.TOOL_CALL, tool_call=call) for call in calls), StreamEvent(StreamEventType.DONE)],
            [StreamEvent(StreamEventType.TEXT_DELTA, "done"), StreamEvent(StreamEventType.DONE)],
        ]
    )
    service = PermissionService.create(tmp_path, home=tmp_path / "home")
    service.set_session_mode(PermissionMode.STRICT)
    loop = make_loop(
        llm,
        tools=[
            TimedTool("read_a", ToolKind.READ, records),
            TimedTool("read_b", ToolKind.READ, records),
        ],
        permission=PermissionInterceptor(service),
    )
    approval_order = []
    active = 0
    max_active = 0

    async def approve(request):
        nonlocal active, max_active
        approval_order.append(request.tool_call.name)
        active += 1
        max_active = max(max_active, active)
        await asyncio.sleep(0.01)
        active -= 1
        return ApprovalDecision(ApprovalDecisionType.APPROVE_ONCE)

    events = asyncio.run(collect_async(loop.run("hello", mode=AgentMode(), approval_provider=approve)))

    assert approval_order == ["read_a", "read_b"]
    assert max_active == 1
    assert records["read_b"]["start"] < records["read_a"]["end"]
    assert events[-1].content == "done"


def test_agent_loop_reports_unknown_tool_as_error():
    llm = ScriptedLLM(
        [
            [
                StreamEvent(
                    StreamEventType.TOOL_CALL,
                    tool_call=ToolCall(id="call-1", name="missing", arguments={}, raw_arguments="{}"),
                ),
                StreamEvent(StreamEventType.DONE),
            ]
        ]
    )
    loop = make_loop(llm, tools=[EchoTool()])

    events = asyncio.run(collect_async(loop.run("hello", mode=AgentMode())))

    assert events[-1].type == AgentEventType.ERROR
    assert events[-1].error_code == AgentErrorCode.UNKNOWN_TOOL


@pytest.mark.parametrize("effect", [PermissionEffect.DENY, PermissionEffect.FORBIDDEN])
def test_agent_loop_never_executes_denied_or_forbidden_tool_and_continues(effect):
    tool = RecordingTool()
    call = ToolCall(id="call-1", name="write_a", arguments={}, raw_arguments="{}")
    llm = ScriptedLLM(
        [
            [StreamEvent(StreamEventType.TOOL_CALL, tool_call=call), StreamEvent(StreamEventType.DONE)],
            [StreamEvent(StreamEventType.TEXT_DELTA, "adjusted"), StreamEvent(StreamEventType.DONE)],
        ]
    )
    loop = make_loop(
        llm,
        tools=[tool],
        permission=FakePermission({"write_a": effect}),
    )

    events = asyncio.run(collect_async(loop.run("hello", mode=AgentMode())))

    denied = next(event for event in events if event.type is AgentEventType.TOOL_RESULT)
    assert tool.calls == []
    assert denied.tool_result.content["reason_code"] == f"fake_{effect.value}"
    assert events[-1].content == "adjusted"


def test_agent_loop_records_failed_write_tool_result_and_continues():
    failed_result = ToolResult(
        ok=False,
        tool_name="write_a",
        content={"path": "README.md"},
        error="write failed",
    )
    call = ToolCall(id="call-1", name="write_a", arguments={}, raw_arguments="{}")
    llm = ScriptedLLM(
        [
            [StreamEvent(StreamEventType.TOOL_CALL, tool_call=call), StreamEvent(StreamEventType.DONE)],
            [StreamEvent(StreamEventType.TEXT_DELTA, "handled"), StreamEvent(StreamEventType.DONE)],
        ]
    )
    loop = make_loop(llm, tools=[StaticResultTool("write_a", failed_result)])

    events = asyncio.run(collect_async(loop.run("hello", mode=AgentMode())))

    assert events[2].type == AgentEventType.TOOL_RESULT
    assert events[2].tool_result == failed_result
    assert len(llm.requests) == 2
    assert json.loads(llm.requests[1][3].content)["error"] == "write failed"
    assert events[-1].content == "handled"


def test_agent_loop_surfaces_tool_timeout_result():
    timeout_result = ToolResult(
        ok=False,
        tool_name="slow",
        content={"tool_call_id": "call-1", "timed_out": True},
        error="tool execution timeout after 0.01 seconds",
    )
    call = ToolCall(id="call-1", name="slow", arguments={}, raw_arguments="{}")
    llm = ScriptedLLM(
        [
            [StreamEvent(StreamEventType.TOOL_CALL, tool_call=call), StreamEvent(StreamEventType.DONE)],
            [StreamEvent(StreamEventType.TEXT_DELTA, "timeout noted"), StreamEvent(StreamEventType.DONE)],
        ]
    )
    loop = make_loop(llm, tools=[StaticResultTool("slow", timeout_result, kind=ToolKind.READ)])

    events = asyncio.run(collect_async(loop.run("hello", mode=AgentMode())))

    timeout_event = next(event for event in events if event.type == AgentEventType.TOOL_RESULT)
    assert timeout_event.tool_result.content["timed_out"] is True
    assert events[-1].content == "timeout noted"


def test_agent_loop_yields_cancelled_when_cancelled():
    loop = make_configured_loop(CancelledLLM())

    events = asyncio.run(collect_async(loop.run("hello", mode=AgentMode())))

    assert [event.type for event in events] == [AgentEventType.USER_MESSAGE, AgentEventType.CANCELLED]


def test_agent_loop_reports_model_timeout():
    loop = make_configured_loop(HangingLLM(), config=AgentConfig(model_timeout_seconds=0.01))

    events = asyncio.run(collect_async(loop.run("hello", mode=AgentMode())))

    assert events[-1].type == AgentEventType.ERROR
    assert events[-1].error_code == AgentErrorCode.MODEL_TIMEOUT


def test_agent_loop_reports_run_timeout():
    loop = make_configured_loop(HangingLLM(), config=AgentConfig(run_timeout_seconds=0.01))

    events = asyncio.run(collect_async(loop.run("hello", mode=AgentMode())))

    assert events[-1].type == AgentEventType.ERROR
    assert events[-1].error_code == AgentErrorCode.RUN_TIMEOUT


def test_agent_loop_reports_run_timeout_during_tool_execution():
    records: dict[str, dict[str, float]] = {}
    call = ToolCall(id="call-1", name="slow_read", arguments={}, raw_arguments="{}")
    llm = ScriptedLLM([[StreamEvent(StreamEventType.TOOL_CALL, tool_call=call), StreamEvent(StreamEventType.DONE)]])
    loop = make_configured_loop(
        llm,
        tools=[TimedTool("slow_read", ToolKind.READ, records, delay=0.1)],
        config=AgentConfig(run_timeout_seconds=0.01),
    )

    events = asyncio.run(collect_async(loop.run("hello", mode=AgentMode())))

    assert events[-1].type == AgentEventType.ERROR
    assert events[-1].error_code == AgentErrorCode.RUN_TIMEOUT
    assert AgentEventType.TOOL_RESULT not in [event.type for event in events]


def test_agent_first_round_lists_deferred_name_and_description_without_schema():
    memory = InMemoryConversationMemory()
    llm = ScriptedLLM([[StreamEvent(StreamEventType.DONE)]])
    remote = make_remote_tool()
    loop, _ = make_mcp_loop(llm, memory, remote, AgentFakeMCPPool())

    asyncio.run(collect_async(loop.run("hello", mode=AgentMode())))

    assert [definition.name for definition in llm.tool_requests[0]] == ["tool_search"]
    reminders = [
        message
        for message in llm.requests[0]
        if message.origin is MessageOrigin.SYSTEM_REMINDER
    ]
    assert len(reminders) == 1
    assert "server__echo" in reminders[0].content
    assert "Remote echo description." in reminders[0].content
    assert "schema_secret" not in reminders[0].content
    assert all(message.origin is not MessageOrigin.SYSTEM_REMINDER for message in memory.messages())


def test_agent_injects_discovered_tool_schema_starting_next_round_only():
    memory = InMemoryConversationMemory()
    search_call = ToolCall(
        id="search-1",
        name="tool_search",
        arguments={"name": "server__echo"},
        raw_arguments='{"name":"server__echo"}',
    )
    llm = ScriptedLLM(
        [
            [StreamEvent(StreamEventType.TOOL_CALL, tool_call=search_call), StreamEvent(StreamEventType.DONE)],
            [StreamEvent(StreamEventType.TEXT_DELTA, "done"), StreamEvent(StreamEventType.DONE)],
        ]
    )
    remote = make_remote_tool()
    loop, registry = make_mcp_loop(llm, memory, remote, AgentFakeMCPPool())

    events = asyncio.run(collect_async(loop.run("hello", mode=AgentMode())))

    assert [definition.name for definition in llm.tool_requests[0]] == ["tool_search"]
    assert [definition.name for definition in llm.tool_requests[1]] == [
        "server__echo",
        "tool_search",
    ]
    assert registry.deferred_summaries() == []
    assert not any(
        message.origin is MessageOrigin.SYSTEM_REMINDER for message in llm.requests[1]
    )
    assert events[-1].content == "done"


def test_agent_failed_search_keeps_schema_hidden_and_reminder_present():
    memory = InMemoryConversationMemory()
    search_call = ToolCall(
        id="search-1",
        name="tool_search",
        arguments={"name": "missing__tool"},
        raw_arguments='{"name":"missing__tool"}',
    )
    llm = ScriptedLLM(
        [
            [StreamEvent(StreamEventType.TOOL_CALL, tool_call=search_call), StreamEvent(StreamEventType.DONE)],
            [StreamEvent(StreamEventType.TEXT_DELTA, "adjusted"), StreamEvent(StreamEventType.DONE)],
        ]
    )
    remote = make_remote_tool()
    loop, registry = make_mcp_loop(llm, memory, remote, AgentFakeMCPPool())

    asyncio.run(collect_async(loop.run("hello", mode=AgentMode())))

    assert [definition.name for definition in llm.tool_requests[0]] == ["tool_search"]
    assert [definition.name for definition in llm.tool_requests[1]] == ["tool_search"]
    assert [summary.name for summary in registry.deferred_summaries()] == ["server__echo"]
    second_reminders = [
        message
        for message in llm.requests[1]
        if message.origin is MessageOrigin.SYSTEM_REMINDER
    ]
    assert len(second_reminders) == 1
    assert "server__echo" in second_reminders[0].content
    assert "schema_secret" not in second_reminders[0].content


def test_discovered_default_write_mcp_tool_uses_existing_approval_flow(tmp_path):
    memory = InMemoryConversationMemory()
    remote_call = ToolCall(
        id="remote-1",
        name="server__write",
        arguments={"value": "approved"},
        raw_arguments='{"value":"approved"}',
    )
    llm = ScriptedLLM(
        [
            [StreamEvent(StreamEventType.TOOL_CALL, tool_call=remote_call), StreamEvent(StreamEventType.DONE)],
            [StreamEvent(StreamEventType.TEXT_DELTA, "done"), StreamEvent(StreamEventType.DONE)],
        ]
    )
    remote = make_remote_tool("write", kind=ToolKind.WRITE)
    pool = AgentFakeMCPPool()
    permission_service = PermissionService.create(tmp_path, home=tmp_path / "home")
    loop, registry = make_mcp_loop(
        llm,
        memory,
        remote,
        pool,
        permission=PermissionInterceptor(permission_service),
    )
    assert registry.mark_discovered("server__write") is True
    approvals = []

    async def approve(request):
        approvals.append(request.tool_call.name)
        assert pool.calls == []
        return ApprovalDecision(ApprovalDecisionType.APPROVE_ONCE)

    events = asyncio.run(
        collect_async(loop.run("hello", mode=AgentMode(), approval_provider=approve))
    )

    assert approvals == ["server__write"]
    assert pool.calls == [("server", "write", {"value": "approved"})]
    assert AgentEventType.APPROVAL_REQUIRED in [event.type for event in events]
    assert events[-1].content == "done"
