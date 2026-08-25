from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass
from typing import Iterator


@dataclass(frozen=True)
class LogIdentity:
    agent_role: str | None = None
    team_name: str | None = None
    member_name: str | None = None
    task_id: str | None = None
    batch_id: str | None = None
    event_id: str | None = None


_current_identity: ContextVar[LogIdentity] = ContextVar(
    "mycode_log_identity",
    default=LogIdentity(),
)


def current_log_identity() -> LogIdentity:
    return _current_identity.get()


@contextmanager
def use_log_identity(
    *,
    agent_role: str | None = None,
    team_name: str | None = None,
    member_name: str | None = None,
    task_id: str | None = None,
    batch_id: str | None = None,
    event_id: str | None = None,
) -> Iterator[LogIdentity]:
    identity = LogIdentity(
        agent_role=agent_role,
        team_name=team_name,
        member_name=member_name,
        task_id=task_id,
        batch_id=batch_id,
        event_id=event_id,
    )
    token: Token[LogIdentity] = _current_identity.set(identity)
    try:
        yield identity
    finally:
        _current_identity.reset(token)


__all__ = ["LogIdentity", "current_log_identity", "use_log_identity"]
