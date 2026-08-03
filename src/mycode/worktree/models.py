from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path

from mycode.workspace import WorkspaceTaskIdentity


class WorktreeRuleType(str, Enum):
    COPY = "copy"
    IGNORED_COPY = "ignored_copy"
    SYMLINK = "symlink"
    HOOKS = "hooks"


class WorktreePhase(str, Enum):
    CREATING = "creating"
    READY = "ready"
    RETAINED = "retained"


class WorktreeDisposition(str, Enum):
    DELETED = "deleted"
    RETAINED = "retained"
    SKIPPED = "skipped"
    FAILED = "failed"


class WorktreeError(RuntimeError):
    def __init__(
        self,
        *,
        code: str,
        phase: str,
        message: str,
        path: Path | None = None,
        branch_name: str | None = None,
        git_exit_code: int | None = None,
    ) -> None:
        _require_non_empty_string("code", code)
        _require_non_empty_string("phase", phase)
        _require_non_empty_string("message", message)
        if path is not None:
            _require_absolute_path("path", path)
        if branch_name is not None:
            _require_non_empty_string("branch_name", branch_name)
        if git_exit_code is not None:
            _require_non_negative_int("git_exit_code", git_exit_code)

        super().__init__(message)
        self.code = code
        self.phase = phase
        self.message = message
        self.path = path
        self.branch_name = branch_name
        self.git_exit_code = git_exit_code


@dataclass(frozen=True)
class WorktreeInitRule:
    type: WorktreeRuleType
    source: str
    target: str

    def __post_init__(self) -> None:
        if not isinstance(self.type, WorktreeRuleType):
            raise ValueError("type must be a WorktreeRuleType")
        _require_non_empty_string("source", self.source)
        _require_non_empty_string("target", self.target)


@dataclass(frozen=True)
class WorktreeConfig:
    rules: tuple[WorktreeInitRule, ...] = ()
    git_timeout_seconds: float = 30.0
    cleanup_interval_seconds: float = 3600.0
    expire_after_seconds: float = 604800.0
    scan_batch_size: int = 64
    digest: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.rules, tuple):
            object.__setattr__(self, "rules", tuple(self.rules))
        for rule in self.rules:
            if not isinstance(rule, WorktreeInitRule):
                raise ValueError("rules must contain WorktreeInitRule values")
        if len(self.rules) > 128:
            raise ValueError("rules must contain at most 128 entries")
        _require_positive_number(
            "git_timeout_seconds",
            self.git_timeout_seconds,
            maximum=120.0,
        )
        _require_positive_number("cleanup_interval_seconds", self.cleanup_interval_seconds)
        _require_positive_number("expire_after_seconds", self.expire_after_seconds)
        _require_int_in_range("scan_batch_size", self.scan_batch_size, minimum=1, maximum=64)
        _require_non_empty_string("digest", self.digest)


@dataclass(frozen=True)
class RepositoryIdentity:
    root: Path
    common_dir: Path
    repository_id: str

    def __post_init__(self) -> None:
        _require_absolute_path("root", self.root)
        _require_absolute_path("common_dir", self.common_dir)
        _require_non_empty_string("repository_id", self.repository_id)


@dataclass(frozen=True)
class GitWorktreeEntry:
    path: Path
    head: str
    branch: str | None
    locked: bool
    prunable: bool

    def __post_init__(self) -> None:
        _require_absolute_path("path", self.path)
        _require_non_empty_string("head", self.head)
        if self.branch is not None:
            _require_non_empty_string("branch", self.branch)
        _require_bool("locked", self.locked)
        _require_bool("prunable", self.prunable)


@dataclass(frozen=True)
class GitStatus:
    has_staged_changes: bool
    has_unstaged_changes: bool
    untracked_paths: tuple[str, ...]

    def __post_init__(self) -> None:
        _require_bool("has_staged_changes", self.has_staged_changes)
        _require_bool("has_unstaged_changes", self.has_unstaged_changes)
        _normalize_string_tuple(self, "untracked_paths")


@dataclass(frozen=True)
class WorktreeMetadata:
    schema_version: int
    phase: WorktreePhase
    repository_id: str
    identity: WorkspaceTaskIdentity
    workspace_root: Path
    config_digest: str
    created_at: datetime
    last_active_at: datetime
    initialized_rules: tuple[str, ...]
    retained_reasons: tuple[str, ...]

    def __post_init__(self) -> None:
        _require_positive_int("schema_version", self.schema_version)
        if not isinstance(self.phase, WorktreePhase):
            raise ValueError("phase must be a WorktreePhase")
        _require_non_empty_string("repository_id", self.repository_id)
        if not isinstance(self.identity, WorkspaceTaskIdentity):
            raise ValueError("identity must be a WorkspaceTaskIdentity")
        if self.repository_id != self.identity.repository_id:
            raise ValueError("repository_id must match identity.repository_id")
        _require_absolute_path("workspace_root", self.workspace_root)
        _require_non_empty_string("config_digest", self.config_digest)
        _require_utc_datetime("created_at", self.created_at)
        _require_utc_datetime("last_active_at", self.last_active_at)
        _normalize_string_tuple(self, "initialized_rules", allow_empty_items=False)
        _normalize_string_tuple(self, "retained_reasons", allow_empty_items=False)


@dataclass(frozen=True)
class InitializationResult:
    completed_rules: tuple[str, ...]
    hooks_path: Path | None

    def __post_init__(self) -> None:
        _normalize_string_tuple(self, "completed_rules", allow_empty_items=False)
        if self.hooks_path is not None:
            _require_absolute_path("hooks_path", self.hooks_path)


@dataclass(frozen=True)
class WorktreeProtectionStatus:
    has_uncommitted_changes: bool
    has_unpushed_commits: bool
    branch_tip: str | None
    upstream: str | None
    reasons: tuple[str, ...]

    def __post_init__(self) -> None:
        _require_bool("has_uncommitted_changes", self.has_uncommitted_changes)
        _require_bool("has_unpushed_commits", self.has_unpushed_commits)
        if self.branch_tip is not None:
            _require_non_empty_string("branch_tip", self.branch_tip)
        if self.upstream is not None:
            _require_non_empty_string("upstream", self.upstream)
        _normalize_string_tuple(self, "reasons", allow_empty_items=False)


@dataclass(frozen=True)
class WorktreeDispositionResult:
    disposition: WorktreeDisposition
    workspace_root: Path
    branch_name: str
    reasons: tuple[str, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.disposition, WorktreeDisposition):
            raise ValueError("disposition must be a WorktreeDisposition")
        _require_absolute_path("workspace_root", self.workspace_root)
        _require_non_empty_string("branch_name", self.branch_name)
        _normalize_string_tuple(self, "reasons", allow_empty_items=False)


@dataclass(frozen=True)
class WorktreeDiagnostic:
    code: str
    phase: str
    message: str
    path: Path | None
    branch_name: str | None

    def __post_init__(self) -> None:
        _require_non_empty_string("code", self.code)
        _require_non_empty_string("phase", self.phase)
        _require_non_empty_string("message", self.message)
        if self.path is not None:
            _require_absolute_path("path", self.path)
        if self.branch_name is not None:
            _require_non_empty_string("branch_name", self.branch_name)


@dataclass(frozen=True)
class CleanupBatchResult:
    scanned: int
    deleted: int
    retained: int
    skipped: int
    failed: int
    has_more: bool
    diagnostics: tuple[WorktreeDiagnostic, ...]

    def __post_init__(self) -> None:
        for field_name in ("scanned", "deleted", "retained", "skipped", "failed"):
            _require_non_negative_int(field_name, getattr(self, field_name))
        _require_bool("has_more", self.has_more)
        if not isinstance(self.diagnostics, tuple):
            object.__setattr__(self, "diagnostics", tuple(self.diagnostics))
        for diagnostic in self.diagnostics:
            if not isinstance(diagnostic, WorktreeDiagnostic):
                raise ValueError("diagnostics must contain WorktreeDiagnostic values")


def _require_non_empty_string(field_name: str, value: str) -> None:
    if type(value) is not str or not value:
        raise ValueError(f"{field_name} must be a non-empty string")


def _require_absolute_path(field_name: str, value: Path) -> None:
    if not isinstance(value, Path):
        raise ValueError(f"{field_name} must be a Path")
    if not value.is_absolute():
        raise ValueError(f"{field_name} must be an absolute path")


def _require_bool(field_name: str, value: bool) -> None:
    if type(value) is not bool:
        raise ValueError(f"{field_name} must be a bool")


def _require_positive_int(field_name: str, value: int) -> None:
    _require_int_in_range(field_name, value, minimum=1)


def _require_non_negative_int(field_name: str, value: int) -> None:
    _require_int_in_range(field_name, value, minimum=0)


def _require_int_in_range(
    field_name: str,
    value: int,
    *,
    minimum: int,
    maximum: int | None = None,
) -> None:
    if type(value) is not int:
        raise ValueError(f"{field_name} must be an integer")
    if value < minimum:
        raise ValueError(f"{field_name} must be at least {minimum}")
    if maximum is not None and value > maximum:
        raise ValueError(f"{field_name} must be at most {maximum}")


def _require_positive_number(
    field_name: str,
    value: float,
    *,
    maximum: float | None = None,
) -> None:
    if type(value) not in (int, float):
        raise ValueError(f"{field_name} must be a number")
    if value <= 0:
        raise ValueError(f"{field_name} must be positive")
    if maximum is not None and value > maximum:
        raise ValueError(f"{field_name} must be at most {maximum:g}")


def _require_utc_datetime(field_name: str, value: datetime) -> None:
    if not isinstance(value, datetime):
        raise ValueError(f"{field_name} must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must include UTC timezone")
    if value.utcoffset() != timezone.utc.utcoffset(value):
        raise ValueError(f"{field_name} must use UTC timezone")


def _normalize_string_tuple(
    instance: object,
    field_name: str,
    *,
    allow_empty_items: bool = True,
) -> None:
    value = getattr(instance, field_name)
    if not isinstance(value, tuple):
        value = tuple(value)
        object.__setattr__(instance, field_name, value)
    for item in value:
        if type(item) is not str:
            raise ValueError(f"{field_name} must contain strings")
        if not allow_empty_items and not item:
            raise ValueError(f"{field_name} must not contain empty strings")
