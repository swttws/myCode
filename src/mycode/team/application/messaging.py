"""Message recipient and sender rules for Agent Team."""

from __future__ import annotations

from collections.abc import Callable

from ..domain import MemberRecord, ResolvedBackend, TeamError, TeamMessage, TeamSnapshot
from ..domain.roles import LEAD_ROLE_NAME


def is_event_driven_backend(member: MemberRecord | None) -> bool:
    """Return whether a member can participate in the event consumer path."""

    return member is not None and member.resolved_backend in (
        None,
        ResolvedBackend.IN_PROCESS,
    )


def event_recipients(
    message: TeamMessage,
    snapshot: TeamSnapshot,
    *,
    backend_supported: Callable[[MemberRecord | None], bool] = is_event_driven_backend,
) -> tuple[str, ...]:
    """Resolve and validate the fixed recipient set for a team message."""

    members_by_name = {member.member_name: member for member in snapshot.members}
    known = {LEAD_ROLE_NAME, *(member.member_name for member in snapshot.members)}
    if message.broadcast:
        recipients = sorted(known - {message.sender})
        supported = [
            name
            for name in recipients
            if name == LEAD_ROLE_NAME or backend_supported(members_by_name.get(name))
        ]
        return tuple(supported)
    if message.target_name not in known:
        raise TeamError(
            code="unknown_member",
            phase="send",
            message="unknown event recipient",
            member_name=message.target_name,
        )
    if message.target_name != LEAD_ROLE_NAME:
        member = members_by_name.get(message.target_name)
        if not backend_supported(member):
            raise TeamError(
                code="unsupported_backend",
                phase="send",
                message="event-driven consumption not supported for this backend",
                member_name=message.target_name,
            )
    return (message.target_name,)


def validate_message_sender(
    message: TeamMessage,
    snapshot: TeamSnapshot,
    *,
    backend_supported: Callable[[MemberRecord | None], bool] = is_event_driven_backend,
) -> None:
    """Validate that the sender is a registered event-capable team role."""

    members = {member.member_name: member for member in snapshot.members}
    known = {LEAD_ROLE_NAME, *members}
    if message.sender not in known:
        raise TeamError(
            code="unknown_sender",
            phase="send",
            message="event sender is not a registered team role",
            team_name=snapshot.team.team_name,
            member_name=message.sender,
        )
    if message.sender != LEAD_ROLE_NAME and not backend_supported(members[message.sender]):
        raise TeamError(
            code="unsupported_backend",
            phase="send",
            message="event-driven consumption not supported for this sender backend",
            team_name=snapshot.team.team_name,
            member_name=message.sender,
        )


__all__ = ["event_recipients", "is_event_driven_backend", "validate_message_sender"]
