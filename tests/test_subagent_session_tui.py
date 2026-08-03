import asyncio
from io import StringIO

from rich.console import Console

from mycode.agent import AgentEvent, AgentEventType
from mycode.permission.models import PermissionMode
from mycode.session import ChatSession
from mycode.subagent.models import (
    SubAgentKind,
    SubAgentTaskSnapshot,
    SubAgentTaskState,
    SubAgentUsage,
)
from mycode.tui import ChatTUI
from mycode.tool import ToolCall, ToolResult
from tests.test_session import FakeAgent, FakePermissions, RecordingMode


def task_snapshot(task_id="task-000001"):
    return SubAgentTaskSnapshot(
        id=task_id,
        sequence=int(task_id.rsplit("-", 1)[-1]),
        kind=SubAgentKind.DEFINED,
        role_name="general",
        state=SubAgentTaskState.RUNNING,
        detached=True,
        rounds=0,
        result=None,
        error_code=None,
        error_message=None,
        usage=SubAgentUsage(),
    )


class FakeSubAgentService:
    def __init__(self, operations=None, detach_result=None):
        self.operations = operations if operations is not None else []
        self.detach_result = detach_result
        self.clear_count = 0
        self.close_count = 0
        self.detach_count = 0

    async def detach_active(self):
        self.detach_count += 1
        self.operations.append("service.detach")
        return self.detach_result

    async def clear(self):
        self.clear_count += 1
        self.operations.append("service.clear")

    async def close(self):
        self.close_count += 1
        self.operations.append("service.close")


class RecordingHook:
    def __init__(self, operations):
        self.operations = operations

    async def trigger(self, context):
        self.operations.append(f"hook.{context.event.value}")


class StreamingSession:
    def __init__(self):
        self.detach_count = 0
        self.release = asyncio.Event()

    async def send(self, text, **kwargs):
        yield AgentEvent(AgentEventType.TEXT_DELTA, "before")
        await self.release.wait()
        yield AgentEvent(AgentEventType.TEXT_DELTA, "after")
        yield AgentEvent(AgentEventType.FINAL_RESPONSE, "done")

    async def detach_active_subagent(self):
        self.detach_count += 1
        return task_snapshot()


class RenderSession:
    def __init__(self):
        self.calls = []

    async def render(self, text, **kwargs):
        self.calls.append((text, kwargs))
        call = ToolCall(id="call-1", name="Agent", arguments={}, raw_arguments="{}")
        child_call = ToolCall(id="call-2", name="find_files", arguments={"pattern": "README"}, raw_arguments='{"pattern":"README"}')
        yield AgentEvent(
            AgentEventType.TOOL_CALL_STARTED,
            content="",
            tool_call=call,
            agent_type="parent",
            sequence=1,
        )
        yield AgentEvent(
            AgentEventType.SUBAGENT_TASK_STARTED,
            content="任务开始",
            agent_type="subagent",
            role_name="explore",
            task_id="task-000001",
            sequence=2,
        )
        yield AgentEvent(
            AgentEventType.TOOL_CALL_STARTED,
            content="",
            tool_call=child_call,
            agent_type="subagent",
            role_name="explore",
            task_id="task-000001",
            sequence=3,
        )
        yield AgentEvent(
            AgentEventType.TOOL_RESULT,
            content="",
            tool_call=child_call,
            tool_result=ToolResult(ok=False, tool_name="find_files", content={}, error="文件不存在"),
            agent_type="subagent",
            role_name="explore",
            task_id="task-000001",
            sequence=4,
        )
        yield AgentEvent(
            AgentEventType.SUBAGENT_TASK_FAILED,
            content="第一行\n第二行",
            agent_type="subagent",
            role_name="explore",
            task_id="task-000001",
            sequence=5,
        )
        yield AgentEvent(
            AgentEventType.TOOL_RESULT,
            content="",
            tool_call=call,
            tool_result=ToolResult(ok=True, tool_name="Agent", content={}),
            agent_type="parent",
            sequence=6,
        )
        yield AgentEvent(
            AgentEventType.FINAL_RESPONSE,
            content="已整理子 Agent 的查找结果……",
            agent_type="parent",
            sequence=7,
        )


def make_console():
    output = StringIO()
    return Console(file=output, force_terminal=False, color_system=None, width=120), output


def test_chat_session_detach_active_subagent_forwards_to_service():
    service = FakeSubAgentService(detach_result=task_snapshot())
    session = ChatSession(
        agent=FakeAgent(),
        permissions=FakePermissions(),
        subagent_service=service,
    )

    result = asyncio.run(session.detach_active_subagent())

    assert result.id == "task-000001"
    assert service.detach_count == 1


def test_chat_session_clear_and_close_wait_for_service_before_existing_lifecycle():
    operations = []
    service = FakeSubAgentService(operations)
    agent = FakeAgent(operations=operations)
    permissions = FakePermissions(operations)
    mode = RecordingMode(operations)
    hook = RecordingHook(operations)
    session = ChatSession(
        agent=agent,
        permissions=permissions,
        mode=mode,
        hook_runtime=hook,
        subagent_service=service,
    )
    session.set_plan_only(True)

    asyncio.run(session.clear_async())
    asyncio.run(session.close())

    assert operations == [
        "hook.session_clear",
        "service.clear",
        "agent",
        "mode",
        "permissions",
        "service.close",
        "hook.session_end",
    ]
    assert session.is_plan_only() is False
    assert permissions.effective_mode() == (PermissionMode.DEFAULT, None)


def test_tui_detach_active_subagent_outputs_task_id_and_stream_continues():
    async def scenario():
        console, output = make_console()
        session = StreamingSession()
        tui = ChatTUI(session=session, console=console)

        render_task = asyncio.create_task(tui._render_stream(session.send("hello")))
        await asyncio.sleep(0)
        detached = await tui.detach_active_subagent()
        session.release.set()
        await asyncio.wait_for(render_task, timeout=1)
        return detached, output.getvalue(), session.detach_count

    detached, output, detach_count = asyncio.run(scenario())

    assert detached.id == "task-000001"
    assert detach_count == 1
    assert "task-000001" in output
    assert "before" in output
    assert "after" in output


def test_tui_detach_active_subagent_is_silent_when_no_active_task():
    class NoActiveSession(StreamingSession):
        async def detach_active_subagent(self):
            self.detach_count += 1
            return None

    async def scenario():
        console, output = make_console()
        session = NoActiveSession()
        tui = ChatTUI(session=session, console=console)
        result = await tui.detach_active_subagent()
        return result, output.getvalue(), session.detach_count

    result, output, detach_count = asyncio.run(scenario())

    assert result is None
    assert output == ""
    assert detach_count == 1


def test_tui_renders_structured_prefixes_for_parent_and_subagent_events():
    async def scenario():
        console, output = make_console()
        session = RenderSession()
        tui = ChatTUI(session=session, console=console)

        await tui.send_user_message("hello")
        return output.getvalue(), session.calls

    output, calls = asyncio.run(scenario())

    assert calls and calls[0][0] == "hello"
    assert "[父Agent] 工具请求：Agent" in output
    assert "[子Agent:explore#000001] 任务开始" in output
    assert "[子Agent:explore#000001] 工具请求：find_files" in output
    assert "[子Agent:explore#000001] 工具失败：find_files - 文件不存在" in output
    assert "[子Agent:explore#000001] 第一行" in output
    assert "[子Agent:explore#000001] 第二行" in output
    assert "[父Agent] 工具完成：Agent" in output
    assert "[父Agent] assistant> 已整理子 Agent 的查找结果……" in output
