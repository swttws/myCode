import asyncio
import json

from mycode.agent import (
    AgentErrorCode,
    AgentEventType,
    AgentLoop,
    AgentMode,
    ApprovalDecision,
    ApprovalDecisionType,
)
from mycode.llm import BaseLLM, StreamEvent, StreamEventType
from mycode.memory import InMemoryConversationMemory
from mycode.tool import ToolCall, ToolDefinition, ToolExecutor, ToolKind, ToolRegistry, ToolResult


class ScriptedLLM(BaseLLM):
    def __init__(self, scripts):
        self.scripts = list(scripts)
        self.requests = []

    async def stream_chat(self, messages, tools=None):
        self.requests.append(list(messages))
        for event in self.scripts.pop(0):
            yield event


class RecordingWriteTool:
    def __init__(self) -> None:
        self.calls = []

    @property
    def definition(self):
        return ToolDefinition(
            name="write_a",
            description="Write test tool.",
            parameters={"type": "object", "properties": {}, "required": []},
            kind=ToolKind.WRITE,
        )

    def execute(self, arguments):
        self.calls.append(arguments)
        return ToolResult(ok=True, tool_name="write_a", content={"written": True})


async def collect_async(async_iterable):
    items = []
    async for item in async_iterable:
        items.append(item)
    return items


def make_loop(llm, tools):
    registry = ToolRegistry(tools)
    return AgentLoop(
        llm=llm,
        memory=InMemoryConversationMemory(),
        tool_executor=ToolExecutor(registry),
        tool_registry=registry,
    )


def test_plan_only_approval_approves_one_write_tool():
    tool = RecordingWriteTool()
    call = ToolCall(id="call-1", name="write_a", arguments={"path": "README.md"}, raw_arguments='{"path":"README.md"}')
    llm = ScriptedLLM(
        [
            [StreamEvent(StreamEventType.TOOL_CALL, tool_call=call), StreamEvent(StreamEventType.DONE)],
            [StreamEvent(StreamEventType.TEXT_DELTA, "done"), StreamEvent(StreamEventType.DONE)],
        ]
    )
    loop = make_loop(llm, [tool])
    mode = AgentMode(plan_only=True)
    approval_requests = []

    async def approve_once(request):
        approval_requests.append(request)
        return ApprovalDecision(ApprovalDecisionType.APPROVE_ONCE)

    events = asyncio.run(collect_async(loop.run("hello", mode=mode, approval_provider=approve_once)))

    assert [event.type for event in events] == [
        AgentEventType.USER_MESSAGE,
        AgentEventType.TOOL_CALL_STARTED,
        AgentEventType.APPROVAL_REQUIRED,
        AgentEventType.TOOL_RESULT,
        AgentEventType.TEXT_DELTA,
        AgentEventType.FINAL_RESPONSE,
    ]
    assert approval_requests[0].tool_call == call
    assert tool.calls == [{"path": "README.md"}]
    assert mode.plan_only is True


def test_plan_only_rejects_write_tool_and_continues_with_rejection_result():
    tool = RecordingWriteTool()
    call = ToolCall(id="call-1", name="write_a", arguments={"path": "README.md"}, raw_arguments='{"path":"README.md"}')
    llm = ScriptedLLM(
        [
            [StreamEvent(StreamEventType.TOOL_CALL, tool_call=call), StreamEvent(StreamEventType.DONE)],
            [StreamEvent(StreamEventType.TEXT_DELTA, "plan"), StreamEvent(StreamEventType.DONE)],
        ]
    )
    loop = make_loop(llm, [tool])

    async def reject(request):
        return ApprovalDecision(ApprovalDecisionType.REJECT)

    events = asyncio.run(
        collect_async(loop.run("hello", mode=AgentMode(plan_only=True), approval_provider=reject))
    )

    rejected_event = next(event for event in events if event.type == AgentEventType.TOOL_RESULT)
    assert tool.calls == []
    assert rejected_event.tool_result.ok is False
    assert "rejected" in rejected_event.tool_result.error
    assert json.loads(llm.requests[1][3].content)["error"] == "tool rejected by user"
    assert events[-1].content == "plan"


def test_plan_only_cancel_stops_current_turn():
    tool = RecordingWriteTool()
    call = ToolCall(id="call-1", name="write_a", arguments={}, raw_arguments="{}")
    llm = ScriptedLLM([[StreamEvent(StreamEventType.TOOL_CALL, tool_call=call), StreamEvent(StreamEventType.DONE)]])
    loop = make_loop(llm, [tool])

    async def cancel(request):
        return ApprovalDecision(ApprovalDecisionType.CANCEL)

    events = asyncio.run(
        collect_async(loop.run("hello", mode=AgentMode(plan_only=True), approval_provider=cancel))
    )

    assert tool.calls == []
    assert events[-1].type == AgentEventType.CANCELLED
    assert len(llm.requests) == 1


def test_plan_only_write_without_approval_provider_errors():
    tool = RecordingWriteTool()
    call = ToolCall(id="call-1", name="write_a", arguments={}, raw_arguments="{}")
    llm = ScriptedLLM([[StreamEvent(StreamEventType.TOOL_CALL, tool_call=call), StreamEvent(StreamEventType.DONE)]])
    loop = make_loop(llm, [tool])

    events = asyncio.run(collect_async(loop.run("hello", mode=AgentMode(plan_only=True))))

    assert tool.calls == []
    assert events[-1].type == AgentEventType.ERROR
    assert events[-1].error_code == AgentErrorCode.APPROVAL_CANCELLED
