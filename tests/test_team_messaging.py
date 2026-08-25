from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from mycode.team.application.messaging import event_recipients, validate_message_sender
from mycode.team.domain.models import MessageProtocol, ResolvedBackend, TeamError, TeamMessage


def _message(*, sender: str, target_name: str | None, broadcast: bool = False) -> TeamMessage:
    return TeamMessage(
        message_id="msg-1",
        protocol=MessageProtocol.BROADCAST if broadcast else MessageProtocol.MESSAGE,
        sender=sender,
        target_name=target_name,
        broadcast=broadcast,
        body="hello",
        summary="hello",
        timestamp=datetime.now(timezone.utc),
    )


def _snapshot(*members: tuple[str, ResolvedBackend]) -> SimpleNamespace:
    return SimpleNamespace(
        team=SimpleNamespace(team_name="team-a"),
        members=tuple(
            SimpleNamespace(member_name=name, resolved_backend=backend)
            for name, backend in members
        ),
    )


def test_broadcast_expands_only_event_driven_recipients() -> None:
    snapshot = _snapshot(
        ("fast", ResolvedBackend.IN_PROCESS),
        ("external", ResolvedBackend.TMUX),
    )

    assert event_recipients(
        _message(sender="lead", target_name=None, broadcast=True), snapshot
    ) == ("fast",)


def test_unknown_recipient_is_rejected_before_event_write() -> None:
    with pytest.raises(TeamError) as exc_info:
        event_recipients(
            _message(sender="lead", target_name="missing"),
            _snapshot(("member", ResolvedBackend.IN_PROCESS)),
        )

    assert exc_info.value.code == "unknown_member"


def test_sender_validation_rejects_non_event_driven_member() -> None:
    with pytest.raises(TeamError) as exc_info:
        validate_message_sender(
            _message(sender="external", target_name="lead"),
            _snapshot(("external", ResolvedBackend.TMUX)),
        )

    assert exc_info.value.code == "unsupported_backend"
