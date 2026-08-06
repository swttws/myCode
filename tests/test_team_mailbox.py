from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from mycode.team import (
    MemberBackend,
    MemberRecord,
    MemberState,
    MessageProtocol,
    ResolvedBackend,
    TeamError,
    TeamMessage,
    TeamRecord,
    TeamState,
    WakeEndpoint,
)
from mycode.team.config import TeamConfig
from mycode.team.context import JsonConversationMemory
from mycode.team.mailbox import MailboxStore
from mycode.team.storage import TeamStore


FIXED_NOW = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)


class FrozenDateTime(datetime):
    @classmethod
    def now(cls, tz=None):  # type: ignore[override]
        return FIXED_NOW


def make_team(root: Path) -> TeamRecord:
    return TeamRecord(
        team_name="team-a",
        repository_root=root,
        repository_id="repo-123",
        target_branch="main",
        state=TeamState.ACTIVE,
        revision=0,
        max_members=16,
        max_active_members=4,
        created_at=FIXED_NOW,
        updated_at=FIXED_NOW,
    )


def make_member(store: TeamStore, root: Path, member_name: str) -> MemberRecord:
    wake_endpoint = WakeEndpoint(
        member_name=member_name,
        backend=ResolvedBackend.IN_PROCESS,
        endpoint=f"in-process:{member_name}",
        revision=1,
    )
    return MemberRecord(
        member_name=member_name,
        role_name="general",
        role_revision=1,
        requested_backend=MemberBackend.IN_PROCESS,
        resolved_backend=ResolvedBackend.IN_PROCESS,
        state=MemberState.RUNNING,
        approval_required=False,
        worktree_root=root / "worktrees" / member_name,
        branch_name=f"mycode/team-a/{member_name}",
        mailbox_path=store.mailbox_path("team-a", member_name),
        context_path=store.context_path("team-a", member_name),
        wake_endpoint=wake_endpoint,
        task_id=f"task-{member_name}",
        batch_id="batch-1",
        revision=1,
        created_at=FIXED_NOW,
        updated_at=FIXED_NOW,
        last_seen_at=FIXED_NOW,
    )


def make_mailbox(tmp_path: Path, *, summary_max_bytes: int = 16) -> tuple[MailboxStore, TeamStore, MemberRecord, MemberRecord, MemberRecord]:
    home = tmp_path / "home"
    home.mkdir()
    store = TeamStore(home=home)
    store.create(make_team(tmp_path))
    lead = make_member(store, tmp_path, "lead")
    alpha = make_member(store, tmp_path, "alpha")
    beta = make_member(store, tmp_path, "beta")
    for member in (lead, alpha, beta):
        store.write_member("team-a", member)
    mailbox = MailboxStore(
        team_name="team-a",
        store=store,
        config=TeamConfig(
            mailbox_message_max_bytes=1024,
            mailbox_summary_max_bytes=summary_max_bytes,
            lock_retry_interval_seconds=0.01,
            lock_timeout_seconds=0.05,
            lock_stale_after_seconds=0.1,
        ),
    )
    for member in (lead, alpha, beta):
        mailbox.register(member)
    return mailbox, store, lead, alpha, beta


def make_message(*, message_id: str = "msg-1", broadcast: bool = False, target_name: str | None = "alpha") -> TeamMessage:
    return TeamMessage(
        message_id=message_id,
        protocol=MessageProtocol.BROADCAST if broadcast else MessageProtocol.MESSAGE,
        sender="lead",
        target_name=target_name,
        broadcast=broadcast,
        body="hello there",
        summary="x" * 32,
        timestamp=FIXED_NOW,
        read=True,
        delivered=False,
        task_id="task-1",
        batch_id="batch-1",
    )


def read_mailbox(path: Path) -> list[dict[str, object]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def test_mailbox_store_rejects_messages_missing_required_fields(tmp_path: Path):
    mailbox, _, _, _, _ = make_mailbox(tmp_path)
    message = object.__new__(TeamMessage)
    for name, value in {
        "message_id": "msg-1",
        "protocol": MessageProtocol.MESSAGE,
        "sender": "lead",
        "target_name": "alpha",
        "broadcast": False,
        "body": "",
        "summary": "summary",
        "timestamp": FIXED_NOW,
        "read": False,
        "delivered": False,
        "task_id": None,
        "batch_id": None,
    }.items():
        object.__setattr__(message, name, value)

    with pytest.raises(ValueError, match="body"):
        mailbox.send(message)


def test_mailbox_store_truncates_summary_and_stamps_utc_timestamp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    mailbox, store, _, alpha, _ = make_mailbox(tmp_path, summary_max_bytes=16)
    monkeypatch.setattr("mycode.team.mailbox.datetime", FrozenDateTime)

    receipt = mailbox.send(make_message())

    assert receipt.message_id == "msg-1"
    assert receipt.recipient_names == ("alpha",)
    assert receipt.fanout_count == 1
    assert receipt.duplicate_count == 0

    messages = mailbox.receive("alpha")
    assert len(messages) == 1
    message = messages[0]
    assert message.timestamp == FIXED_NOW
    assert message.timestamp.tzinfo == timezone.utc
    assert message.summary == "x" * 16
    assert message.read is False
    assert message.delivered is True
    assert mailbox.unread("alpha") == messages
    assert read_mailbox(store.mailbox_path("team-a", "alpha"))[0]["summary"] == "x" * 16


def test_mailbox_store_broadcasts_to_each_registered_member_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    mailbox, store, lead, alpha, beta = make_mailbox(tmp_path)
    monkeypatch.setattr("mycode.team.mailbox.datetime", FrozenDateTime)

    receipt = mailbox.send(make_message(broadcast=True, target_name=None))

    assert receipt.message_id == "msg-1"
    assert receipt.recipient_names == ("alpha", "beta")
    assert receipt.fanout_count == 2
    assert receipt.duplicate_count == 0

    alpha_messages = mailbox.receive("alpha")
    beta_messages = mailbox.receive("beta")
    lead_messages = mailbox.receive("lead")

    assert len(alpha_messages) == 1
    assert len(beta_messages) == 1
    assert lead_messages == ()
    assert alpha_messages[0].message_id == "msg-1"
    assert beta_messages[0].message_id == "msg-1"
    assert alpha_messages[0].broadcast is True
    assert beta_messages[0].broadcast is True
    assert alpha_messages[0].target_name is None
    assert beta_messages[0].target_name is None
    assert mailbox.unread("alpha") == alpha_messages
    assert mailbox.unread("beta") == beta_messages
    assert read_mailbox(store.mailbox_path("team-a", "alpha"))[0]["message_id"] == "msg-1"
    assert read_mailbox(store.mailbox_path("team-a", "beta"))[0]["message_id"] == "msg-1"


def test_mailbox_store_uses_stable_ids_for_replayed_sends(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    mailbox, store, _, _, _ = make_mailbox(tmp_path)
    monkeypatch.setattr("mycode.team.mailbox.datetime", FrozenDateTime)

    first = mailbox.send(make_message())
    second = mailbox.send(make_message())

    assert first.duplicate_count == 0
    assert second.recipient_names == ()
    assert second.fanout_count == 0
    assert second.duplicate_count == 1
    assert len(mailbox.receive("alpha")) == 1
    assert len(read_mailbox(store.mailbox_path("team-a", "alpha"))) == 1


def test_mailbox_store_acknowledge_requires_checkpoint_and_marks_read(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    mailbox, store, _, alpha, _ = make_mailbox(tmp_path)
    monkeypatch.setattr("mycode.team.mailbox.datetime", FrozenDateTime)

    mailbox.send(make_message())

    with pytest.raises(TeamError, match="checkpoint"):
        mailbox.acknowledge("alpha", "msg-1")

    context = JsonConversationMemory(path=store.context_path("team-a", "alpha"))
    context.set_checkpoint({"turn": 1})
    context.set_applied_message_ids(("msg-1",))

    mailbox.acknowledge("alpha", "msg-1")

    messages = mailbox.receive("alpha")
    assert messages[0].read is True
    assert mailbox.unread("alpha") == ()
