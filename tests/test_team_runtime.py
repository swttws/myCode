from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

import pytest

from mycode.dev_logging import configure_dev_logging
from mycode.agent import AgentEventType
from mycode.llm import ChatMessage, MessageOrigin
from mycode.team import (
    ApprovalState,
    BatchRecord,
    BatchState,
    MemberBackend,
    MemberRecord,
    MemberState,
    MessageProtocol,
    ResolvedBackend,
    TeamError,
    TeamMessage,
    TeamRecord,
    TeamState,
    TeamTask,
    TeamTaskState,
    TaskKind,
    WakeEndpoint,
)
from mycode.team.infrastructure.config import TeamConfig
from mycode.team.infrastructure.context import JsonConversationMemory
from mycode.team.infrastructure.events import TeamEventStore
from mycode.team.execution.notifier import TeamEventNotifier
from mycode.team.execution.runtime import TeamMemberRuntime
from mycode.team.infrastructure.storage import TeamStore
from mycode.team.infrastructure.requests import TeamRequest, TeamRequestKind, TeamRequestState, TeamRequestStore
from mycode.team.tooling.tool_names import MEMBER_TEAM_TOOL_NAMES


def _read_payload(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def test_context_json_conversation_memory_appends_replaces_and_clears(tmp_path: Path):
    path = tmp_path / "context.json"
    memory = JsonConversationMemory(path=path, max_bytes=4096)

    memory.append(ChatMessage(role="user", content="hello"))
    memory.append(
        ChatMessage(
            role="assistant",
            content="ready",
            tool_call_id="call-1",
            tool_name="tool",
            tool_arguments="{\"x\":1}",
            origin=MessageOrigin.SYSTEM_REMINDER,
        )
    )

    assert memory.messages() == [
        ChatMessage(role="user", content="hello"),
        ChatMessage(
            role="assistant",
            content="ready",
            tool_call_id="call-1",
            tool_name="tool",
            tool_arguments="{\"x\":1}",
            origin=MessageOrigin.SYSTEM_REMINDER,
        ),
    ]
    payload = _read_payload(path)
    assert payload["version"] == 1
    assert payload["schema_version"] == 1
    assert len(payload["messages"]) == 2
    assert payload["messages"][1]["origin"] == MessageOrigin.SYSTEM_REMINDER.value

    memory.replace([ChatMessage(role="user", content="replaced")])
    assert memory.messages() == [ChatMessage(role="user", content="replaced")]

    memory.clear()
    assert memory.messages() == []
    assert _read_payload(path)["messages"] == []


def test_context_json_conversation_memory_reload_picks_up_external_changes(tmp_path: Path):
    path = tmp_path / "context.json"
    memory = JsonConversationMemory(path=path, max_bytes=4096)
    memory.append(ChatMessage(role="user", content="initial"))

    path.write_text(
        json.dumps(
            {
                "version": 1,
                "schema_version": 1,
                "messages": [
                    {
                        "role": "assistant",
                        "content": "external",
                        "origin": "conversation",
                    }
                ],
            },
            ensure_ascii=False,
            sort_keys=True,
        ),
        encoding="utf-8",
    )

    memory.reload()

    assert memory.messages() == [ChatMessage(role="assistant", content="external")]


def test_context_json_conversation_memory_rejects_corrupt_files_without_overwriting(tmp_path: Path):
    path = tmp_path / "context.json"
    memory = JsonConversationMemory(path=path, max_bytes=4096)
    memory.append(ChatMessage(role="user", content="keep me"))
    original = path.read_text(encoding="utf-8")
    path.write_text("{not-json", encoding="utf-8")

    with pytest.raises(TeamError, match="corrupt"):
        memory.reload()

    assert memory.messages() == [ChatMessage(role="user", content="keep me")]
    assert path.read_text(encoding="utf-8") == "{not-json"
    assert original != path.read_text(encoding="utf-8")


def test_context_json_conversation_memory_enforces_size_limit(tmp_path: Path):
    path = tmp_path / "context.json"
    memory = JsonConversationMemory(path=path, max_bytes=220)
    memory.append(ChatMessage(role="user", content="small"))

    with pytest.raises(TeamError, match="exceeds"):
        memory.append(ChatMessage(role="assistant", content="x" * 300))

    assert memory.messages() == [ChatMessage(role="user", content="small")]
    assert len(path.read_text(encoding="utf-8").encode("utf-8")) <= 220


NOW = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)


class FakeAgentLoop:
    def __init__(self) -> None:
        self.prompts = []
        self.clear_count = 0

    async def run(self, user_text, *, mode, approval_provider=None):
        self.prompts.append(user_text)
        yield type("Event", (), {"type": "final_response", "content": "done"})()

    def clear_memory(self):
        self.clear_count += 1


def make_member(store: TeamStore, tmp_path: Path) -> MemberRecord:
    endpoint = WakeEndpoint(
        member_name="dev",
        backend=ResolvedBackend.IN_PROCESS,
        endpoint="in-process:dev",
        revision=1,
    )
    return MemberRecord(
        member_name="dev",
        role_name="general",
        role_revision=1,
        requested_backend=MemberBackend.IN_PROCESS,
        resolved_backend=ResolvedBackend.IN_PROCESS,
        state=MemberState.RUNNING,
        worktree_root=tmp_path / "worktree",
        branch_name="mycode/team-a/dev",
        context_path=store.context_path("team-a", "dev"),
        wake_endpoint=endpoint,
        task_id="task-1",
        batch_id="batch-1",
        created_at=NOW,
        updated_at=NOW,
    )


def make_runtime(tmp_path: Path):
    store = TeamStore(home=tmp_path / "home")
    store.create(
        TeamRecord(
            team_name="team-a",
            repository_root=tmp_path,
            repository_id="repo-123",
            target_branch="main",
            state=TeamState.ACTIVE,
            created_at=NOW,
            updated_at=NOW,
        )
    )
    member = make_member(store, tmp_path)
    store.write_member("team-a", member)
    event_store = TeamEventStore("team-a", store=store, config=TeamConfig())
    notifier = TeamEventNotifier()
    memory = JsonConversationMemory(path=member.context_path)
    agent = FakeAgentLoop()
    runtime = TeamMemberRuntime(
        team_name="team-a",
        member_name="dev",
        store=store,
        event_store=event_store,
        notifier=notifier,
        memory=memory,
        agent=agent,
    )
    return runtime, store, event_store, notifier, memory, agent, member


def _send_event(event_store, notifier, sender, target, protocol, body, message_id, summary="", **kwargs):
    """Helper to write an event to the event store."""
    msg = TeamMessage(
        message_id=message_id,
        protocol=protocol,
        sender=sender,
        target_name=target,
        broadcast=False,
        body=body,
        summary=summary or body,
        timestamp=NOW,
        **kwargs,
    )
    event_store.append_message(msg, recipients=(target,))


def _events_for(events: TeamEventStore, member_name: str) -> tuple:
    return events.events_for_role(member_name)


def _no_unread(events: TeamEventStore, member_name: str) -> bool:
    """Check there are no pending events for the role."""
    return events.next_event(member_name) is None


def test_member_runtime_injects_unread_messages_then_acks_after_checkpoint(tmp_path: Path):
    runtime, store, event_store, notifier, memory, agent, member = make_runtime(tmp_path)
    _send_event(
        event_store, notifier,
        sender="lead", target="dev",
        protocol=MessageProtocol.MESSAGE,
        message_id="msg-1",
        body="please continue",
    )

    asyncio.run(runtime.run_until_idle())

    assert agent.prompts == ["please continue"]
    assert memory.applied_message_ids == ("msg-1",)
    assert memory.checkpoint["last_message_id"] == "msg-1"
    assert _no_unread(event_store, "dev")
    assert store.load("team-a").members[0].state is MemberState.IDLE
    lead_messages = _events_for(event_store, "lead")
    status_updates = [m for m in lead_messages if m.message.protocol is MessageProtocol.STATUS_UPDATE]
    assert len(status_updates) >= 1
    assert status_updates[-1].message.sender == "dev"


def test_member_runtime_marks_running_while_agent_is_processing(tmp_path: Path):
    runtime, store, event_store, notifier, _memory, _agent, _member = make_runtime(tmp_path)

    class TrackingAgent:
        def __init__(self):
            self.states = []

        async def run(self, user_text, *, mode, approval_provider=None):
            self.states.append(store.load("team-a").members[0].state)
            yield type("Event", (), {"type": AgentEventType.FINAL_RESPONSE, "content": "done"})()
            self.states.append(store.load("team-a").members[0].state)

    runtime = TeamMemberRuntime(
        team_name="team-a",
        member_name="dev",
        store=store,
        event_store=event_store,
        notifier=notifier,
        memory=JsonConversationMemory(path=store.context_path("team-a", "dev")),
        agent=TrackingAgent(),
    )
    _send_event(
        event_store, notifier,
        sender="lead", target="dev",
        protocol=MessageProtocol.MESSAGE,
        message_id="running-1",
        body="run",
    )

    asyncio.run(runtime.run_until_idle())

    assert runtime._agent.states == [MemberState.RUNNING, MemberState.RUNNING]
    assert store.load("team-a").members[0].state is MemberState.IDLE


def test_member_runtime_keeps_message_unread_when_agent_returns_error_event(tmp_path: Path):
    runtime, store, event_store, notifier, _memory, _agent, _member = make_runtime(tmp_path)

    class ErrorAgent:
        def __init__(self):
            self.calls = 0

        async def run(self, user_text, *, mode, approval_provider=None):
            self.calls += 1
            yield type("Event", (), {"type": AgentEventType.ERROR, "content": "model failed"})()

    agent = ErrorAgent()
    runtime = TeamMemberRuntime(
        team_name="team-a",
        member_name="dev",
        store=store,
        event_store=event_store,
        notifier=notifier,
        memory=JsonConversationMemory(path=store.context_path("team-a", "dev")),
        agent=agent,
    )
    _send_event(
        event_store, notifier,
        sender="lead", target="dev",
        protocol=MessageProtocol.MESSAGE,
        message_id="error-event-1",
        body="run",
    )

    asyncio.run(runtime.run_until_idle())

    events = event_store.events_for_role("dev")
    error_events = [e for e in events if e.message.message_id == "error-event-1"]
    assert len(error_events) == 1
    # 事件层重试 3 次后终态失败（不再就地 ACK），message 保持 unread，member 置 FAILED
    assert agent.calls == 3
    assert error_events[0].state.value == "failed"
    assert error_events[0].attempts == 3
    assert store.load("team-a").members[0].state is MemberState.FAILED


def test_member_runtime_deduplicates_replayed_messages(tmp_path: Path):
    runtime, _store, _event_store, _notifier, memory, agent, _member = make_runtime(tmp_path)
    memory.set_checkpoint({"last_message_id": "msg-1"})
    memory.set_applied_message_ids(("msg-1",))

    asyncio.run(runtime.run_until_idle())

    assert agent.prompts == []
    assert memory.applied_message_ids == ("msg-1",)


def test_member_runtime_checkpoint_helpers_are_stable(tmp_path: Path):
    runtime, _store, event_store, notifier, memory, agent, _member = make_runtime(tmp_path)
    memory.append(ChatMessage(role="user", content="old"))

    asyncio.run(runtime.graceful_stop())
    asyncio.run(runtime.resume_from_checkpoint())

    assert memory.checkpoint["member_state"] == "stopped"
    assert memory.messages() == [ChatMessage(role="user", content="old")]
    assert agent.clear_count == 0
    lead_messages = _events_for(event_store, "lead")
    shutdown_responses = [m for m in lead_messages if m.message.protocol is MessageProtocol.SHUTDOWN_RESPONSE]
    assert len(shutdown_responses) >= 1


def test_member_runtime_shutdown_request_triggers_shutdown_response(tmp_path: Path):
    runtime, store, event_store, notifier, memory, agent, member = make_runtime(tmp_path)
    _send_event(
        event_store, notifier,
        sender="lead", target="dev",
        protocol=MessageProtocol.SHUTDOWN_REQUEST,
        message_id="shutdown-dev-1",
        body="shutdown now",
    )

    asyncio.run(runtime.run_until_idle())

    assert memory.checkpoint["member_state"] == "stopped"
    assert memory.checkpoint["shutdown_request_id"] == "shutdown-dev-1"
    responses = _events_for(event_store, "lead")
    shutdown_responses = [m for m in responses if m.message.protocol is MessageProtocol.SHUTDOWN_RESPONSE]
    assert len(shutdown_responses) >= 1
    assert shutdown_responses[0].message.message_id.startswith("shutdown-response-dev-shutdown-dev-1")
    assert agent.prompts == []


def test_member_runtime_registers_team_member_tool_when_registry_is_provided(tmp_path: Path):
    class Registry:
        def __init__(self):
            self.registered = []

        def register(self, tool):
            self.registered.append(tool)

    _runtime, store, event_store, notifier, memory, agent, _member = make_runtime(tmp_path)
    registry = Registry()

    TeamMemberRuntime(
        team_name="team-a",
        member_name="dev",
        store=store,
        event_store=event_store,
        notifier=notifier,
        memory=memory,
        agent=agent,
        tool_registry=registry,
    )

    assert {tool.definition.name for tool in registry.registered} == MEMBER_TEAM_TOOL_NAMES


def test_member_runtime_marks_member_failed_after_agent_failure(tmp_path: Path):
    _runtime, store, event_store, notifier, memory, _agent, member = make_runtime(tmp_path)

    class FailingAgent:
        def __init__(self):
            self.calls = 0

        async def run(self, user_text, *, mode, approval_provider=None):
            self.calls += 1
            raise RuntimeError("agent failed")
            yield None

    agent = FailingAgent()
    runtime = TeamMemberRuntime(
        team_name="team-a",
        member_name="dev",
        store=store,
        event_store=event_store,
        notifier=notifier,
        memory=memory,
        agent=agent,
    )
    _send_event(
        event_store, notifier,
        sender="lead", target="dev",
        protocol=MessageProtocol.MESSAGE,
        message_id="work-fails",
        body="run",
    )

    asyncio.run(runtime.run_until_idle())

    assert agent.calls == 3
    assert store.load("team-a").members[0].state is MemberState.FAILED
    lead_messages = _events_for(event_store, "lead")
    status_updates = [m for m in lead_messages if m.message.protocol is MessageProtocol.STATUS_UPDATE]
    assert len(status_updates) >= 1


def test_member_runtime_logs_message_execution_and_idle(tmp_path: Path, caplog):
    runtime, _store, event_store, notifier, _memory, _agent, _member = make_runtime(tmp_path)
    _send_event(
        event_store, notifier,
        sender="lead", target="dev",
        protocol=MessageProtocol.MESSAGE,
        message_id="msg-1",
        body="please continue",
    )

    with caplog.at_level(logging.INFO, logger="mycode.team.runtime"):
        asyncio.run(runtime.run_until_idle())

    messages = [record.message for record in caplog.records if record.name == "mycode.team.runtime"]
    assert "team.runtime.started" in messages
    assert "team.runtime.message.started" in messages
    assert "team.runtime.message.completed" in messages
    assert "team.runtime.idle" in messages
    assert any(getattr(record, "message_id", None) == "msg-1" for record in caplog.records if record.name == "mycode.team.runtime")


def test_member_runtime_logs_task_result_summary(tmp_path: Path, caplog):
    runtime, _store, event_store, notifier, _memory, _agent, _member = make_runtime(tmp_path)
    _send_event(
        event_store, notifier,
        sender="lead", target="dev",
        protocol=MessageProtocol.MESSAGE,
        message_id="msg-result",
        body="please continue",
        task_id="task-1",
        batch_id="batch-1",
    )

    with caplog.at_level(logging.INFO, logger="mycode.team.runtime"):
        asyncio.run(runtime.run_until_idle())

    result_record = next(
        record
        for record in caplog.records
        if record.name == "mycode.team.runtime" and record.message == "team.task.result"
    )
    assert result_record.task_id == "task-1"
    assert result_record.event_id
    assert result_record.result_summary == "done"

    started_record = next(
        record
        for record in caplog.records
        if record.name == "mycode.team.runtime" and record.message == "team.task.started"
    )
    assert started_record.task_summary == "please continue"


def test_member_runtime_agent_logs_include_message_task_identity(tmp_path: Path):
    runtime, _store, event_store, notifier, _memory, _agent, _member = make_runtime(tmp_path)

    class LoggingAgent:
        async def run(self, user_text, *, mode, approval_provider=None):
            logging.getLogger("mycode.agent.loop").info("member deep event")
            yield type("Event", (), {"type": "final_response", "content": "done"})()

    runtime = TeamMemberRuntime(
        team_name="team-a",
        member_name="dev",
        store=_store,
        event_store=event_store,
        notifier=notifier,
        memory=_memory,
        agent=LoggingAgent(),
    )
    _send_event(
        event_store, notifier,
        sender="lead", target="dev",
        protocol=MessageProtocol.MESSAGE,
        message_id="msg-task",
        body="run",
        task_id="task-1",
        batch_id="batch-1",
    )

    log_file = tmp_path / "dev.log"
    configure_dev_logging(log_file)
    asyncio.run(runtime.run_until_idle())

    event_line = next(
        line
        for line in log_file.read_text(encoding="utf-8").splitlines()
        if "member deep event" in line
    )
    assert "角色=member" in event_line
    assert "团队=team-a" in event_line
    assert "成员=dev" in event_line
    assert "任务=task-1" in event_line
    assert "批次=batch-1" in event_line


def test_member_runtime_logs_agent_failure_and_fails_member(tmp_path: Path, caplog):
    _runtime, store, event_store, notifier, memory, _agent, member = make_runtime(tmp_path)

    class FailingAgent:
        def __init__(self):
            self.calls = 0

        async def run(self, user_text, *, mode, approval_provider=None):
            self.calls += 1
            raise RuntimeError("agent failed")
            yield None

    agent = FailingAgent()
    runtime = TeamMemberRuntime(
        team_name="team-a",
        member_name="dev",
        store=store,
        event_store=event_store,
        notifier=notifier,
        memory=memory,
        agent=agent,
    )
    _send_event(
        event_store, notifier,
        sender="lead", target="dev",
        protocol=MessageProtocol.MESSAGE,
        message_id="work-fails",
        body="run",
    )

    with caplog.at_level(logging.INFO, logger="mycode.team.runtime"):
        asyncio.run(runtime.run_until_idle())

    assert agent.calls == 3

    messages = [record.message for record in caplog.records if record.name == "mycode.team.runtime"]
    assert "team.runtime.message.failed" in messages
    error_record = next(record for record in caplog.records if record.message == "team.runtime.message.failed")
    assert error_record.exc_info is not None
    assert store.load("team-a").members[0].state is MemberState.FAILED


def test_member_runtime_team_member_tool_can_send_status_to_lead(tmp_path: Path):
    class Registry:
        def __init__(self):
            self.registered = []

        def register(self, tool):
            self.registered.append(tool)

    _runtime, store, event_store, notifier, _memory, agent, _member = make_runtime(tmp_path)
    registry = Registry()
    TeamMemberRuntime(
        team_name="team-a",
        member_name="dev",
        store=store,
        event_store=event_store,
        notifier=notifier,
        memory=JsonConversationMemory(path=store.context_path("team-a", "dev")),
        agent=agent,
        tool_registry=registry,
    )

    tool = next(tool for tool in registry.registered if tool.definition.name == "team_status_update")
    result = asyncio.run(
        tool.execute_async(
            {
                "message_id": "status-1",
                "body": "idle",
            }
        )
    )

    assert result.ok is True
    messages = _events_for(event_store, "lead")
    status_updates = [m for m in messages if m.message.protocol is MessageProtocol.STATUS_UPDATE]
    assert len(status_updates) >= 1
    assert status_updates[-1].message.sender == "dev"


def test_member_runtime_resumes_after_lead_clarification_response(tmp_path: Path):
    runtime, store, event_store, notifier, memory, agent, member = make_runtime(tmp_path)
    store.write_batch(
        "team-a",
        BatchRecord(
            batch_id="batch-1",
            goal="goal",
            baseline_commit="a" * 40,
            state=BatchState.ACTIVE,
            created_at=NOW,
            updated_at=NOW,
        ),
    )
    store.write_task(
        "team-a",
        "batch-1",
        TeamTask(
            task_id="task-1",
            batch_id="batch-1",
            title="task",
            description="task",
            dependency_ids=(),
            kind=TaskKind.CODE,
            owner="dev",
            state=TeamTaskState.AWAITING_INPUT,
            approval_state=ApprovalState.APPROVED,
            revision=1,
            created_at=NOW,
            updated_at=NOW,
        ),
    )
    TeamRequestStore(store).create(
        TeamRequest(
            request_id="request-1",
            team_name="team-a",
            batch_id="batch-1",
            task_id="task-1",
            member_name="dev",
            kind=TeamRequestKind.CLARIFICATION,
            question="Which API?",
            options=("v1", "v2"),
            context_summary="Need a choice.",
            state=TeamRequestState.PENDING,
            created_at=NOW,
        )
    )
    _send_event(
        event_store, notifier,
        sender="lead", target="dev",
        protocol=MessageProtocol.CLARIFICATION_RESPONSE,
        message_id="response-1",
        body='{"request_id":"request-1","resolution":"v2"}',
        task_id="task-1",
        batch_id="batch-1",
    )

    asyncio.run(runtime.run_until_idle())

    assert agent.prompts == ['{"request_id":"request-1","resolution":"v2"}']
    assert store.load("team-a").members[0].state is MemberState.IDLE
    assert store.read_task("team-a", "batch-1", "task-1").state is TeamTaskState.RUNNING
    assert memory.applied_message_ids == ("response-1",)


def test_member_runtime_reports_unrecoverable_failure_as_failed_event(tmp_path: Path):
    _runtime, store, event_store, notifier, memory, _agent, member = make_runtime(tmp_path)

    class FailingAgent:
        def __init__(self):
            self.calls = 0

        async def run(self, user_text, *, mode, approval_provider=None):
            self.calls += 1
            raise RuntimeError("agent failed")
            yield None

    agent = FailingAgent()
    runtime = TeamMemberRuntime(
        team_name="team-a",
        member_name="dev",
        store=store,
        event_store=event_store,
        notifier=notifier,
        memory=memory,
        agent=agent,
    )
    _send_event(
        event_store, notifier,
        sender="lead", target="dev",
        protocol=MessageProtocol.MESSAGE,
        message_id="work-fails-new",
        body="run",
    )

    asyncio.run(runtime.run_until_idle())

    assert agent.calls == 3
    assert store.load("team-a").members[0].state is MemberState.FAILED
    failed_events = [e for e in event_store.events_for_role("dev") if e.message.message_id == "work-fails-new"]
    assert failed_events[0].state.value == "failed"
    assert failed_events[0].attempts == 3
    lead_messages = _events_for(event_store, "lead")
    status_updates = [m for m in lead_messages if m.message.protocol is MessageProtocol.STATUS_UPDATE]
    assert len(status_updates) >= 1
    assert '"event":"member_failed"' in status_updates[-1].message.body
