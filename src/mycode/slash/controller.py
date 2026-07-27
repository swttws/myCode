from __future__ import annotations

from typing import TYPE_CHECKING, Awaitable, Protocol, runtime_checkable

from mycode.permission.models import PermissionMode
from mycode.slash.models import (
    ApplicationStatusSnapshot,
    PermissionStatusSnapshot,
    SlashMode,
)

if TYPE_CHECKING:
    from mycode.compact.models import ContextTokenStatus
    from mycode.memory.models import MemoryStatusSnapshot, SessionStatusSnapshot


@runtime_checkable
class SlashCommandController(Protocol):
    def show_message(self, text: str, *, error: bool = False) -> None:
        ...

    def send_user_message(self, text: str) -> Awaitable[None]:
        ...

    def execute_skill(self, name: str, arguments: str) -> Awaitable[None]:
        ...

    def compact_context(self) -> Awaitable[None]:
        ...

    def clear_session(self) -> None:
        ...

    def current_mode(self) -> SlashMode:
        ...

    def set_mode(self, mode: SlashMode) -> None:
        ...

    def permission_status(self) -> PermissionStatusSnapshot:
        ...

    def set_permission_mode(self, mode: PermissionMode) -> None:
        ...

    def token_status(self) -> Awaitable[ContextTokenStatus]:
        ...

    def session_status(self) -> Awaitable[SessionStatusSnapshot]:
        ...

    def memory_status(self) -> Awaitable[MemoryStatusSnapshot]:
        ...

    def application_status(self) -> Awaitable[ApplicationStatusSnapshot]:
        ...
