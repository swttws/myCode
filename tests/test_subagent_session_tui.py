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
