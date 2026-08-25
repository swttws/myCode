from __future__ import annotations

import ast
import asyncio
from datetime import datetime, timezone
from pathlib import Path

import pytest

from mycode.team import (
    EventRecipientType,
    MessageProtocol,
    TeamError,
    TeamEventState,
    TeamMessage,
    TeamRecord,
    TeamState,
)
from mycode.team.infrastructure.events import TeamEventStore
from mycode.team.execution.notifier import TeamEventNotifier
from mycode.team.execution.runtime import _MemberRuntimeToolService
from mycode.team.infrastructure.storage import TeamStore
from mycode.team.execution.backends import BackendSelector
from mycode.team.application.service import TeamService
from tests.test_team_service import FakeBackend, FakeWorktreeService, make_service_with_backend, spawn_service_member


def _event_store(root: Path) -> TeamEventStore:
    root = root.resolve()
    store = TeamStore(home=root / "home")
    store.create(
        TeamRecord(
            team_name="team-a",
            repository_root=root,
            repository_id="repo-123",
            target_branch="main",
            state=TeamState.ACTIVE,
        )
    )
    return TeamEventStore("team-a", store=store)


def _message(message_id: str, *, sender: str = "lead", target: str = "dev") -> TeamMessage:
    return TeamMessage(
        message_id=message_id,
        protocol=MessageProtocol.MESSAGE,
        sender=sender,
        target_name=target,
        broadcast=False,
        body=message_id,
        summary=message_id,
        timestamp=datetime.now(timezone.utc),
    )


def test_event_store_rejects_unregistered_recipient(tmp_path: Path):
    events = _event_store(tmp_path)
    events.register_role("dev")

    with pytest.raises(TeamError, match="recipient"):
        events.append_message(_message("unknown", target="ghost"), recipients=("ghost",))


def test_event_store_rejects_out_of_order_ack(tmp_path: Path):
    events = _event_store(tmp_path)
    events.register_role("dev")
    events.append_message(_message("one"), recipients=("dev",))
    events.append_message(_message("two"), recipients=("dev",))
    first, second = events.events_for_role("dev")

    with pytest.raises(TeamError, match="sequence"):
        events.ack_event("dev", second.event_id)

    assert events.next_event("dev").event_id == first.event_id


def test_terminal_failure_contains_message_context(tmp_path: Path):
    events = _event_store(tmp_path)
    events.register_role("dev")
    events.append_message(_message("failed", target="dev"), recipients=("dev",))
    event = events.next_event("dev")

    assert event is not None
    events.fail_event("dev", event.event_id, "first")
    events.fail_event("dev", event.event_id, "second")
    failure = events.fail_event("dev", event.event_id, "third")

    assert failure is not None
    assert failure.team_name == "team-a"
    assert failure.message_id == "failed"
    assert failure.protocol is MessageProtocol.MESSAGE
    assert failure.recipient_type is EventRecipientType.MEMBER
    assert failure.reason_code == "handler_error"
    assert failure.final_state == TeamEventState.FAILED


def test_member_runtime_tool_routes_to_requested_member(tmp_path: Path):
    events = _event_store(tmp_path)
    events.register_role("lead")
    events.register_role("alpha")
    events.register_role("beta")
    notifier = TeamEventNotifier()
    service = _MemberRuntimeToolService(
        team_name="team-a",
        member_name="alpha",
        store=events.store,
        event_store=events,
        notifier=notifier,
        config=None,
    )

    receipt = asyncio.run(service.send_message(_message("alpha-to-beta", sender="alpha", target="beta")))

    assert receipt.recipient_names == ("beta",)
    assert [event.message.message_id for event in events.events_for_role("beta")] == ["alpha-to-beta"]
    assert events.events_for_role("lead") == ()


def test_consumer_has_no_timeout_polling():
    source = Path(__file__).resolve().parents[1] / "src" / "mycode" / "team" / "execution" / "consumer.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "wait_for"
    ]
    assert calls == []


def test_service_rejects_unknown_sender_before_event_append(tmp_path: Path):
    async def scenario():
        service = make_service_with_backend(
            tmp_path,
            backend_selector=BackendSelector(capability_probe=lambda backend, environment: True),
            backend=FakeBackend(),
        )
        await service.create_or_attach("team-a")
        with pytest.raises(TeamError, match="sender"):
            await service.send_message(_message("spoofed", sender="ghost", target="lead"))
        assert service.event_store.events_for_role("lead") == ()

    asyncio.run(scenario())


def test_attach_restores_persisted_in_process_member(tmp_path: Path):
    async def scenario():
        backend = FakeBackend()
        first = make_service_with_backend(
            tmp_path,
            backend_selector=BackendSelector(capability_probe=lambda backend, environment: True),
            backend=backend,
        )
        await spawn_service_member(first)
        await first.clear_session()

        second = make_service_with_backend(
            tmp_path,
            backend_selector=BackendSelector(capability_probe=lambda backend, environment: True),
            backend=backend,
        )
        await second.create_or_attach("team-a")
        assert len(backend.started) == 2
        assert "dev" in second._backend_handles
        await second.clear_session()

    asyncio.run(scenario())
