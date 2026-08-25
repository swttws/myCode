from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from pathlib import Path

from mycode.team import (
    MemberBackend,
    MemberRecord,
    MemberState,
    MessageProtocol,
    ResolvedBackend,
    TaskKind,
    TeamEventState,
    TeamMessage,
    TeamRecord,
    TeamState,
    TeamTask,
    TeamTaskState,
    WakeEndpoint,
)
from mycode.team.execution.backends import BackendSelector
from mycode.team.infrastructure.config import TeamConfig
from mycode.team.infrastructure.events import LEAD_ROLE_NAME
from mycode.team.application.service import TeamService
from mycode.team.infrastructure.storage import TeamStore
from tests.test_team_service import FakeBackend, FakeWorktreeService


def test_stage_14_success_path_uses_persistent_team_state_without_push(tmp_path: Path):
    async def scenario():
        repository_root = tmp_path / "repo"
        repository_root.mkdir()
        store = TeamStore(home=tmp_path / "home")
        service = TeamService(
            store=store,
            repository_root=repository_root,
            repository_id="repo-123",
            target_branch="main",
            lead_owner="lead-1",
            config=TeamConfig(
                lock_retry_interval_seconds=0.01,
                lock_timeout_seconds=0.1,
                lock_stale_after_seconds=0.2,
            ),
            worktree_service=FakeWorktreeService(repository_root),
            backend_selector=BackendSelector(capability_probe=lambda backend, env: True),
            backend=FakeBackend(),
        )
        await service.create_or_attach("team-a")
        batch = await service.start_batch("ship")
        first = service.task_board.create(
            TeamTask(
                task_id="task-a",
                batch_id=batch.batch_id,
                title="A",
                description="A",
                dependency_ids=(),
                kind=TaskKind.CODE,
            )
        )
        second = service.task_board.create(
            TeamTask(
                task_id="task-b",
                batch_id=batch.batch_id,
                title="B",
                description="B",
                dependency_ids=("task-a",),
                kind=TaskKind.CODE,
            )
        )
        await service.spawn_member(
            member_name="alpha",
            role_name="general",
            role_revision=1,
            requested_backend=MemberBackend.IN_PROCESS,
            task_id=first.task_id,
            batch_id=batch.batch_id,
            goal="ship",
            read_only=False,
            approval_required=False,
        )
        await service.spawn_member(
            member_name="beta",
            role_name="general",
            role_revision=1,
            requested_backend=MemberBackend.IN_PROCESS,
            task_id=second.task_id,
            batch_id=batch.batch_id,
            goal="ship",
            read_only=False,
            approval_required=False,
        )
        receipt = await service.send_message(
            TeamMessage(
                message_id="msg-1",
                protocol=MessageProtocol.MESSAGE,
                sender="lead",
                target_name="alpha",
                broadcast=False,
                body="resume",
                summary="resume",
                timestamp=first.created_at,
            )
        )

        assert receipt.recipient_names == ("alpha",)
        assert store.load("team-a").members[0].member_name == "alpha"

    asyncio.run(scenario())


def test_team_worker_main_runs_named_member_runtime_factory(tmp_path: Path, caplog):
    from mycode.team.execution.worker import main

    calls = []

    class Runtime:
        async def resume_from_checkpoint(self):
            calls.append("resume")

        async def run_event_consumer(self):
            calls.append("run")

    def runtime_factory(request):
        calls.append((request.team_name, request.member_name, request.home))
        return Runtime()

    with caplog.at_level(logging.INFO, logger="mycode.team.worker"):
        exit_code = main(
            ["team-a/dev", "--home", str(tmp_path / "home")],
            runtime_factory=runtime_factory,
        )

    assert exit_code == 0
    assert calls == [("team-a", "dev", tmp_path / "home"), "resume", "run"]
    messages = [record.message for record in caplog.records if record.name == "mycode.team.worker"]
    assert "team.worker.started" in messages
    assert "team.worker.runtime.created" in messages
    assert "team.worker.runtime.started" in messages
    assert "team.worker.runtime.completed" in messages
    assert "team.worker.completed" in messages


def test_team_worker_default_runtime_builds_real_agent_loop(tmp_path: Path, monkeypatch):
    from types import SimpleNamespace

    from mycode.team.execution import worker
    from mycode.team.execution.worker import TeamWorkerRequest

    home = tmp_path / "home"
    repository_root = tmp_path / "repo"
    worktree_root = repository_root / ".worktrees" / "team-a" / "dev"
    repository_root.mkdir()
    worktree_root.mkdir(parents=True)
    store = TeamStore(home=home)
    store.create(
        TeamRecord(
            team_name="team-a",
            repository_root=repository_root,
            repository_id="repo-123",
            target_branch="main",
            state=TeamState.ACTIVE,
        )
    )
    store.write_member(
        "team-a",
        MemberRecord(
            member_name="dev",
            role_name="general",
            role_revision=1,
            requested_backend=MemberBackend.IN_PROCESS,
            resolved_backend=ResolvedBackend.IN_PROCESS,
            state=MemberState.RUNNING,
            worktree_root=worktree_root,
            branch_name="mycode/team/team-a/dev",
            context_path=store.context_path("team-a", "dev"),
            wake_endpoint=WakeEndpoint(
                member_name="dev",
                backend=ResolvedBackend.IN_PROCESS,
                endpoint="in-process:dev",
                revision=1,
            ),
        )
    )
    captured = {}

    class FakeAgentLoop:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        async def run(self, user_text, *, mode, approval_provider=None):
            if False:
                yield None

    monkeypatch.setattr(worker, "load_config", lambda *args, **kwargs: SimpleNamespace(model="test-model"), raising=False)
    monkeypatch.setattr(worker, "create_llm", lambda config: object(), raising=False)
    monkeypatch.setattr(worker, "AgentLoop", FakeAgentLoop, raising=False)

    runtime = worker.create_worker_runtime(
        TeamWorkerRequest(team_name="team-a", member_name="dev", home=home)
    )

    assert isinstance(runtime._agent, FakeAgentLoop)
    assert captured["workspace"].root == worktree_root
    tool_names = {definition.name for definition in captured["tool_registry"].definitions()}
    assert "team_clarification_request" in tool_names or "team_task_create" in tool_names


# ──────────────────────────────────────────────────────────────────────
# T15: Event-driven end-to-end scenarios
# ──────────────────────────────────────────────────────────────────────

NOW = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)


def _make_e2e_service(tmp_path: Path) -> TeamService:
    repository_root = tmp_path / "repo"
    repository_root.mkdir(exist_ok=True)
    return TeamService(
        store=TeamStore(home=tmp_path / "home"),
        repository_root=repository_root,
        repository_id="repo-123",
        target_branch="main",
        lead_owner="lead-1",
        config=TeamConfig(
            lock_retry_interval_seconds=0.01,
            lock_timeout_seconds=0.1,
            lock_stale_after_seconds=0.2,
        ),
        worktree_service=FakeWorktreeService(repository_root),
        backend_selector=BackendSelector(capability_probe=lambda backend, env: True),
        backend=FakeBackend(),
        clock=lambda: NOW,
    )


async def _activate_and_spawn(service: TeamService, member_name: str) -> None:
    await service.create_or_attach("team-a")
    batch = await service.start_batch("e2e")
    task = service.task_board.create(
        TeamTask(
            task_id=f"task-{member_name}",
            batch_id=batch.batch_id,
            title="E2E task",
            description="E2E communication test",
            dependency_ids=(),
            kind=TaskKind.CODE,
            state=TeamTaskState.PENDING,
        )
    )
    await service.spawn_member(
        member_name=member_name,
        role_name="general",
        role_revision=7,
        requested_backend=MemberBackend.IN_PROCESS,
        task_id=task.task_id,
        batch_id=batch.batch_id,
        goal="e2e test",
        read_only=False,
        approval_required=False,
    )


def _make_e2e_message(
    *,
    message_id: str,
    sender: str,
    target_name: str,
    body: str = "hello",
    broadcast: bool = False,
) -> TeamMessage:
    return TeamMessage(
        message_id=message_id,
        protocol=MessageProtocol.BROADCAST if broadcast else MessageProtocol.MESSAGE,
        sender=sender,
        target_name=None if broadcast else target_name,
        broadcast=broadcast,
        body=body,
        summary=body,
        timestamp=NOW,
    )


# ── Scenario 1: Lead → member ─────────────────────────────────────────


def test_event_driven_lead_to_member_message_is_consumed_and_acked(tmp_path: Path):
    """Lead sends a task message to a member; event is written, notified, consumed, acked."""

    async def scenario():
        service = _make_e2e_service(tmp_path)
        await _activate_and_spawn(service, "alpha")

        receipt = await service.send_message(
            _make_e2e_message(message_id="msg-1", sender="lead", target_name="alpha", body="implement feature")
        )

        assert receipt.recipient_names == ("alpha",)
        assert receipt.fanout_count == 1

        # Event was written to event store (may also include TASK_ASSIGNMENT from spawn)
        events = service.event_store.events_for_role("alpha")
        my_events = [e for e in events if e.message.message_id == "msg-1"]
        assert len(my_events) == 1
        event = my_events[0]
        assert event.recipient_name == "alpha"
        assert event.state == TeamEventState.PENDING

        # Notifier was notified
        queue = service.event_notifier.queue_for("alpha")
        assert queue is not None
        assert not queue.empty()
        queue.get_nowait()

        # Event can be consumed and acked
        next_ev = service.event_store.next_event("alpha")
        assert next_ev is not None
        # May get the TASK_ASSIGNMENT first; skip it
        while next_ev.event_id != event.event_id:
            service.event_store.begin_event("alpha", next_ev.event_id)
            service.event_store.ack_event("alpha", next_ev.event_id)
            next_ev = service.event_store.next_event("alpha")
            assert next_ev is not None
        service.event_store.begin_event("alpha", event.event_id)
        acked = service.event_store.ack_event("alpha", event.event_id)
        assert acked.state == TeamEventState.ACKED

    asyncio.run(scenario())


# ── Scenario 2: Member → Lead ─────────────────────────────────────────


def test_event_driven_member_to_lead_message_is_consumed_and_acked(tmp_path: Path):
    """Member sends a status message to Lead; event is written, Lead notified, consumed, acked."""

    async def scenario():
        service = _make_e2e_service(tmp_path)
        await _activate_and_spawn(service, "alpha")

        receipt = await service.send_message(
            _make_e2e_message(message_id="msg-1", sender="alpha", target_name="lead", body="task done")
        )

        assert receipt.recipient_names == ("lead",)

        events = service.event_store.events_for_role(LEAD_ROLE_NAME)
        assert len(events) == 1
        event = events[0]
        assert event.message.message_id == "msg-1"
        assert event.recipient_name == LEAD_ROLE_NAME
        assert event.state == TeamEventState.PENDING

        queue = service.event_notifier.queue_for(LEAD_ROLE_NAME)
        assert queue is not None
        assert not queue.empty()
        queue.get_nowait()

        next_ev = service.event_store.next_event(LEAD_ROLE_NAME)
        assert next_ev is not None
        service.event_store.begin_event(LEAD_ROLE_NAME, event.event_id)
        acked = service.event_store.ack_event(LEAD_ROLE_NAME, event.event_id)
        assert acked.state == TeamEventState.ACKED
        assert service.event_store.next_event(LEAD_ROLE_NAME) is None

    asyncio.run(scenario())


# ── Scenario 3: Member → member ───────────────────────────────────────


def test_event_driven_member_to_member_message_is_consumed_in_order(tmp_path: Path):
    """Member A sends to member B; events consumed in sequence order."""

    async def scenario():
        service = _make_e2e_service(tmp_path)
        await _activate_and_spawn(service, "alpha")
        await _activate_and_spawn(service, "beta")

        r1 = await service.send_message(
            _make_e2e_message(message_id="msg-1", sender="alpha", target_name="beta", body="from alpha")
        )
        r2 = await service.send_message(
            _make_e2e_message(message_id="msg-2", sender="lead", target_name="beta", body="from lead")
        )

        assert r1.recipient_names == ("beta",)
        assert r2.recipient_names == ("beta",)

        events = service.event_store.events_for_role("beta")
        my_events = [e for e in events if e.message.message_id in ("msg-1", "msg-2")]
        assert len(my_events) == 2
        assert my_events[0].message.message_id == "msg-1"
        assert my_events[1].message.message_id == "msg-2"
        assert my_events[0].sequence < my_events[1].sequence

        queue = service.event_notifier.queue_for("beta")
        assert queue is not None
        assert not queue.empty()
        queue.get_nowait()

        # Consume first (skip TASK_ASSIGNMENT if present)
        next_ev = service.event_store.next_event("beta")
        assert next_ev is not None
        while next_ev.event_id != my_events[0].event_id:
            service.event_store.begin_event("beta", next_ev.event_id)
            service.event_store.ack_event("beta", next_ev.event_id)
            next_ev = service.event_store.next_event("beta")
            assert next_ev is not None
        service.event_store.begin_event("beta", my_events[0].event_id)
        service.event_store.ack_event("beta", my_events[0].event_id)

        # Second still pending
        next_ev = service.event_store.next_event("beta")
        assert next_ev is not None
        assert next_ev.event_id == my_events[1].event_id
        service.event_store.begin_event("beta", my_events[1].event_id)
        service.event_store.ack_event("beta", my_events[1].event_id)

        assert service.event_store.next_event("beta") is None

    asyncio.run(scenario())


# ── Scenario 4: Full round trip ───────────────────────────────────────


def test_event_driven_team_round_trip_uses_shared_consumer(tmp_path: Path):
    """Lead → alpha → beta → Lead: all events use the same event mechanism."""

    async def scenario():
        service = _make_e2e_service(tmp_path)
        await _activate_and_spawn(service, "alpha")
        await _activate_and_spawn(service, "beta")

        # Lead → alpha
        await service.send_message(
            _make_e2e_message(message_id="round-1", sender="lead", target_name="alpha", body="start task")
        )
        # alpha → beta
        await service.send_message(
            _make_e2e_message(message_id="round-2", sender="alpha", target_name="beta", body="handoff")
        )
        # beta → Lead
        await service.send_message(
            _make_e2e_message(message_id="round-3", sender="beta", target_name="lead", body="all done")
        )

        alpha_events = service.event_store.events_for_role("alpha")
        beta_events = service.event_store.events_for_role("beta")
        lead_events = service.event_store.events_for_role(LEAD_ROLE_NAME)

        # Filter to only our test messages
        alpha_my = [e for e in alpha_events if e.message.message_id == "round-1"]
        beta_my = [e for e in beta_events if e.message.message_id == "round-2"]
        lead_my = [e for e in lead_events if e.message.message_id == "round-3"]

        assert len(alpha_my) == 1
        assert alpha_my[0].message.message_id == "round-1"
        assert len(beta_my) == 1
        assert beta_my[0].message.message_id == "round-2"
        assert len(lead_my) == 1
        assert lead_my[0].message.message_id == "round-3"

        for ev in (*alpha_my, *beta_my, *lead_my):
            assert ev.state == TeamEventState.PENDING

        for role in ("alpha", "beta", LEAD_ROLE_NAME):
            q = service.event_notifier.queue_for(role)
            assert q is not None
            assert not q.empty()
            q.get_nowait()

        # Consume and ack all (skip TASK_ASSIGNMENT events)
        for role in ("alpha", "beta", LEAD_ROLE_NAME):
            ev = service.event_store.next_event(role)
            while ev is not None:
                service.event_store.begin_event(role, ev.event_id)
                service.event_store.ack_event(role, ev.event_id)
                ev = service.event_store.next_event(role)

    asyncio.run(scenario())


# ── Scenario 5: Member failure after 3 attempts ───────────────────────


def test_event_driven_member_failure_after_three_attempts_is_recorded(tmp_path: Path):
    """A member event that fails 3 times is recorded as terminal failure."""

    async def scenario():
        service = _make_e2e_service(tmp_path)
        await _activate_and_spawn(service, "alpha")

        await service.send_message(
            _make_e2e_message(message_id="fail-1", sender="lead", target_name="alpha", body="risky task")
        )

        events = service.event_store.events_for_role("alpha")
        my_events = [e for e in events if e.message.message_id == "fail-1"]
        assert len(my_events) == 1
        event = my_events[0]

        for attempt in range(3):
            service.event_store.begin_event("alpha", event.event_id)
            result = service.event_store.fail_event(
                "alpha", event.event_id, reason=f"attempt {attempt + 1} failed"
            )
            # Reload to check current state
            current = [e for e in service.event_store.events_for_role("alpha") if e.message.message_id == "fail-1"][0]
            assert current.attempts == attempt + 1
            if attempt < 2:
                assert result is None
                assert current.state == TeamEventState.PENDING
            else:
                assert result is not None
                assert current.state == TeamEventState.FAILED

        terminal = [e for e in service.event_store.events_for_role("alpha") if e.message.message_id == "fail-1"][0]
        assert terminal.state == TeamEventState.FAILED
        assert terminal.attempts == 3

        # Failed event is not returned by next_event (only TASK_ASSIGNMENT may remain)
        remaining = service.event_store.next_event("alpha")
        if remaining is not None:
            assert remaining.message.message_id != "fail-1"

    asyncio.run(scenario())


# ── Scenario 6: Broadcast ─────────────────────────────────────────────


def test_event_driven_broadcast_expands_recipients_at_append_time(tmp_path: Path):
    """Broadcast delivers to all registered members at append time, not later."""

    async def scenario():
        service = _make_e2e_service(tmp_path)
        await _activate_and_spawn(service, "alpha")
        await _activate_and_spawn(service, "beta")

        receipt = await service.send_message(
            _make_e2e_message(
                message_id="broadcast-1",
                sender="lead",
                target_name="",
                body="announcement",
                broadcast=True,
            )
        )

        assert receipt.recipient_names == ("alpha", "beta")

        # Both alpha and beta have our broadcast event (may also have TASK_ASSIGNMENT)
        alpha_my = [e for e in service.event_store.events_for_role("alpha") if e.message.message_id == "broadcast-1"]
        beta_my = [e for e in service.event_store.events_for_role("beta") if e.message.message_id == "broadcast-1"]
        assert len(alpha_my) == 1
        assert len(beta_my) == 1

        # Lead does NOT receive own broadcast
        assert len(service.event_store.events_for_role(LEAD_ROLE_NAME)) == 0

    asyncio.run(scenario())
