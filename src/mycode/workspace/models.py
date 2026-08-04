from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path


class WorkspaceKind(str, Enum):
    SHARED = "shared"
    WORKTREE = "worktree"


class WorkspacePreparation(str, Enum):
    SHARED = "shared"
    CREATED = "created"
    RECOVERED = "recovered"


@dataclass(frozen=True)
class WorkspaceTaskIdentity:
    repository_id: str
    task_id: str
    role_name: str
    task_token: str
    relative_name: str
    branch_name: str
    base_commit: str

    def __post_init__(self) -> None:
        for field_name in (
            "repository_id",
            "task_id",
            "role_name",
            "task_token",
            "relative_name",
            "branch_name",
            "base_commit",
        ):
            _require_non_empty_string(field_name, getattr(self, field_name))


@dataclass(frozen=True)
class WorkspaceContext:
    kind: WorkspaceKind
    root: Path
    repository_root: Path
    repository_id: str
    task_identity: WorkspaceTaskIdentity | None
    branch_name: str | None
    hooks_path: Path | None

    def __post_init__(self) -> None:
        _require_absolute_path("root", self.root)
        _require_absolute_path("repository_root", self.repository_root)
        _require_non_empty_string("repository_id", self.repository_id)
        if self.hooks_path is not None:
            _require_absolute_path("hooks_path", self.hooks_path)

        if self.kind is WorkspaceKind.SHARED:
            if self.task_identity is not None:
                raise ValueError("shared workspace cannot include task_identity")
            if self.branch_name is not None:
                raise ValueError("shared workspace cannot include branch_name")
            if self.hooks_path is not None:
                raise ValueError("shared workspace cannot include hooks_path")
            return

        if self.kind is WorkspaceKind.WORKTREE:
            if self.task_identity is None:
                raise ValueError("worktree workspace requires task_identity")
            if self.branch_name is None:
                raise ValueError("worktree workspace requires branch_name")
            _require_non_empty_string("branch_name", self.branch_name)
            if self.branch_name != self.task_identity.branch_name:
                raise ValueError("branch_name must match task_identity.branch_name")
            if self.repository_id != self.task_identity.repository_id:
                raise ValueError("repository_id must match task_identity.repository_id")
            return

        raise ValueError("invalid workspace kind")


@dataclass(frozen=True)
class WorkspaceLease:
    context: WorkspaceContext
    preparation: WorkspacePreparation
    metadata_path: Path | None
    initialized_rules: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.metadata_path is not None:
            _require_absolute_path("metadata_path", self.metadata_path)
        if not isinstance(self.initialized_rules, tuple):
            object.__setattr__(self, "initialized_rules", tuple(self.initialized_rules))
        for rule in self.initialized_rules:
            _require_non_empty_string("initialized_rules", rule)

        if self.context.kind is WorkspaceKind.SHARED:
            if self.preparation is not WorkspacePreparation.SHARED:
                raise ValueError("shared workspace lease requires shared preparation")
            if self.metadata_path is not None:
                raise ValueError("shared workspace lease cannot include metadata_path")
        elif self.preparation is WorkspacePreparation.SHARED:
            raise ValueError("worktree workspace lease cannot use shared preparation")


def _require_non_empty_string(field_name: str, value: str) -> None:
    if type(value) is not str or not value:
        raise ValueError(f"{field_name} must be a non-empty string")


def _require_absolute_path(field_name: str, value: Path) -> None:
    if not isinstance(value, Path):
        raise ValueError(f"{field_name} must be a Path")
    if not value.is_absolute():
        raise ValueError(f"{field_name} must be an absolute path")
