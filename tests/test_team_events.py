import asyncio
from datetime import datetime, timezone
from pathlib import Path

from mycode.team import MessageProtocol, TeamMessage, TeamRecord, TeamState
from mycode.team.infrastructure.events import TeamEventStore
from mycode.team.infrastructure.storage import TeamStore


def make_event_store(tmp_path: Path) -> TeamEventStore:
    store = TeamStore(home=tmp_path / "home")
    store.create(
        TeamRecord(
            team_name="team-a",
            repository_root=tmp_path,
            repository_id="repo-123",
            target_branch="main",
            state=TeamState.ACTIVE,
        )
    )
    return TeamEventStore("team-a", store=store)


def make_message(message_id: str, *, target_name: str = "dev") -> TeamMessage:
    return TeamMessage(
        message_id=message_id,
        protocol=MessageProtocol.MESSAGE,
        sender="lead",
        target_name=target_name,
        broadcast=False,
        body="implement the change",
        summary="implement",
        timestamp=datetime.now(timezone.utc),
    )


def test_event_store_appends_direct_message_before_consumption(tmp_path: Path):
    events = make_event_store(tmp_path)
    events.register_role("lead")
    events.register_role("dev")

    receipt = events.append_message(make_message("msg-1"), recipients=("dev",))

    assert receipt.message_id == "msg-1"
    assert receipt.recipient_names == ("dev",)
    event = events.next_event("dev")
    assert event is not None
    assert event.sequence == 1
    assert event.message.message_id == "msg-1"


def test_role_cursors_ack_independently(tmp_path: Path):
    events = make_event_store(tmp_path)
    events.register_role("lead")
    events.register_role("dev")
    events.append_message(make_message("msg-dev"), recipients=("dev",))
    events.append_message(make_message("msg-lead", target_name="lead"), recipients=("lead",))

    dev_event = events.next_event("dev")
    assert dev_event is not None
    events.ack_event("dev", dev_event.event_id)

    assert events.next_event("dev") is None
    lead_event = events.next_event("lead")
    assert lead_event is not None
    assert lead_event.message.message_id == "msg-lead"


def test_event_store_records_terminal_failure_after_three_attempts(tmp_path: Path):
    events = make_event_store(tmp_path)
    events.register_role("dev")
    events.append_message(make_message("msg-1"), recipients=("dev",))
    event = events.next_event("dev")
    assert event is not None

    assert events.fail_event("dev", event.event_id, "first failure") is None
    assert events.fail_event("dev", event.event_id, "second failure") is None
    failure = events.fail_event("dev", event.event_id, "third failure")

    assert failure is not None
    assert failure.attempts == 3
    assert failure.reason == "third failure"
    assert events.next_event("dev") is None


def test_unacked_event_replays_after_store_reload(tmp_path: Path):
    events = make_event_store(tmp_path)
    events.register_role("dev")
    events.append_message(make_message("msg-1"), recipients=("dev",))

    reloaded = TeamEventStore("team-a", store=events.store)
    reloaded.register_role("dev")
    event = reloaded.next_event("dev")

    assert event is not None
    assert event.message.message_id == "msg-1"
