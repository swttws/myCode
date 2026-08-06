from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from types import MappingProxyType
from typing import Mapping


class TeamState(str, Enum):
    ACTIVE = "active"
    ARCHIVED = "archived"
    RECOVERY_REQUIRED = "recovery_required"


class MemberState(str, Enum):
    PROVISIONING = "provisioning"
    RUNNING = "running"
    IDLE = "idle"
    AWAITING_APPROVAL = "awaiting_approval"
    BLOCKED = "blocked"
    STOPPING = "stopping"
    STOPPED = "stopped"
    FAILED = "failed"


class MemberBackend(str, Enum):
    AUTO = "auto"
    TMUX = "tmux"
    TERMINAL = "terminal"
    IN_PROCESS = "in_process"


class ResolvedBackend(str, Enum):
    TMUX = "tmux"
    WINDOWS_TERMINAL = "windows_terminal"
    IN_PROCESS = "in_process"


class TaskKind(str, Enum):
    CODE = "code"
    READ_ONLY = "read_only"


class TeamTaskState(str, Enum):
    PENDING = "pending"
    CLAIMED = "claimed"
    AWAITING_APPROVAL = "awaiting_approval"
    RUNNING = "running"
    BLOCKED = "blocked"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class BatchState(str, Enum):
    PENDING = "pending"
    ACTIVE = "active"
    BLOCKED = "blocked"
    INTEGRATING = "integrating"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ApprovalState(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    CANCELLED = "cancelled"


class MessageProtocol(str, Enum):
    MESSAGE = "message"
    BROADCAST = "broadcast"
    PLAN_SUBMIT = "plan_submit"
    PLAN_DECISION = "plan_decision"
    STATUS_UPDATE = "status_update"
    SHUTDOWN_REQUEST = "shutdown_request"
    SHUTDOWN_RESPONSE = "shutdown_response"


@dataclass(frozen=True)
class TeamRecord:
    team_name: str
    repository_root: Path
    repository_id: str
    target_branch: str
    state: TeamState
    revision: int = 0
    lead_owner: str | None = None
    max_members: int = 16
    max_active_members: int = 4
    created_at: datetime | None = None
    updated_at: datetime | None = None

    def __post_init__(self) -> None:
        _require_non_empty_string("team_name", self.team_name)
        _require_absolute_path("repository_root", self.repository_root)
        _require_non_empty_string("repository_id", self.repository_id)
        _require_non_empty_string("target_branch", self.target_branch)
        _require_enum("state", self.state, TeamState)
        _require_non_negative_int("revision", self.revision)
        if self.lead_owner is not None:
            _require_non_empty_string("lead_owner", self.lead_owner)
        _require_positive_int("max_members", self.max_members)
        _require_positive_int("max_active_members", self.max_active_members)
        if self.max_active_members > self.max_members:
            raise ValueError("max_active_members must be less than or equal to max_members")
        _require_optional_utc_datetime("created_at", self.created_at)
        _require_optional_utc_datetime("updated_at", self.updated_at)


@dataclass(frozen=True)
class MemberRecord:
    member_name: str
    role_name: str
    role_revision: int
    requested_backend: MemberBackend
    resolved_backend: ResolvedBackend | None = None
    state: MemberState = MemberState.PROVISIONING
    approval_required: bool = False
    worktree_root: Path | None = None
    branch_name: str | None = None
    mailbox_path: Path | None = None
    context_path: Path | None = None
    wake_endpoint: WakeEndpoint | None = None
    task_id: str | None = None
    batch_id: str | None = None
    revision: int = 0
    created_at: datetime | None = None
    updated_at: datetime | None = None
    last_seen_at: datetime | None = None

    def __post_init__(self) -> None:
        _require_non_empty_string("member_name", self.member_name)
        _require_non_empty_string("role_name", self.role_name)
        _require_non_negative_int("role_revision", self.role_revision)
        _require_enum("requested_backend", self.requested_backend, MemberBackend)
        if self.resolved_backend is not None:
            _require_enum("resolved_backend", self.resolved_backend, ResolvedBackend)
        _require_enum("state", self.state, MemberState)
        _require_bool("approval_required", self.approval_required)
        _require_optional_absolute_path("worktree_root", self.worktree_root)
        if self.branch_name is not None:
            _require_non_empty_string("branch_name", self.branch_name)
        _require_optional_absolute_path("mailbox_path", self.mailbox_path)
        _require_optional_absolute_path("context_path", self.context_path)
        if self.wake_endpoint is not None:
            _require_instance("wake_endpoint", self.wake_endpoint, WakeEndpoint)
            if self.wake_endpoint.member_name != self.member_name:
                raise ValueError("wake_endpoint member_name must match member_name")
        if self.task_id is not None:
            _require_non_empty_string("task_id", self.task_id)
        if self.batch_id is not None:
            _require_non_empty_string("batch_id", self.batch_id)
        _require_non_negative_int("revision", self.revision)
        _require_optional_utc_datetime("created_at", self.created_at)
        _require_optional_utc_datetime("updated_at", self.updated_at)
        _require_optional_utc_datetime("last_seen_at", self.last_seen_at)
        if self.state is MemberState.RUNNING:
            if self.resolved_backend is None:
                raise ValueError("resolved_backend must be present when member is running")
            if self.wake_endpoint is None:
                raise ValueError("wake_endpoint must be present when member is running")


@dataclass(frozen=True)
class BatchRecord:
    batch_id: str
    goal: str
    baseline_commit: str
    state: BatchState
    task_id: str | None = None
    revision: int = 0
    integration_diagnostics: tuple[str, ...] = ()
    created_at: datetime | None = None
    updated_at: datetime | None = None
    completed_at: datetime | None = None
    result_commit_id: str | None = None

    def __post_init__(self) -> None:
        _require_non_empty_string("batch_id", self.batch_id)
        _require_non_empty_string("goal", self.goal)
        _require_git_commit_id("baseline_commit", self.baseline_commit)
        _require_enum("state", self.state, BatchState)
        if self.task_id is not None:
            _require_non_empty_string("task_id", self.task_id)
        _require_non_negative_int("revision", self.revision)
        _normalize_string_tuple(self, "integration_diagnostics")
        _require_optional_utc_datetime("created_at", self.created_at)
        _require_optional_utc_datetime("updated_at", self.updated_at)
        _require_optional_utc_datetime("completed_at", self.completed_at)
        if self.state is BatchState.COMPLETED:
            if self.completed_at is None:
                raise ValueError("completed_at must be present when batch is completed")
            _require_git_commit_id("result_commit_id", self.result_commit_id)
        elif self.completed_at is not None or self.result_commit_id is not None:
            raise ValueError("completed_at and result_commit_id are only valid for completed batches")


@dataclass(frozen=True)
class TeamTask:
    task_id: str
    batch_id: str
    title: str
    description: str
    dependency_ids: tuple[str, ...]
    kind: TaskKind
    owner: str | None = None
    state: TeamTaskState = TeamTaskState.PENDING
    plan_revision: int = 0
    approval_state: ApprovalState = ApprovalState.PENDING
    result: TaskResult | None = None
    error: str | None = None
    revision: int = 0
    created_at: datetime | None = None
    updated_at: datetime | None = None

    def __post_init__(self) -> None:
        _require_non_empty_string("task_id", self.task_id)
        _require_non_empty_string("batch_id", self.batch_id)
        _require_non_empty_string("title", self.title)
        _require_non_empty_string("description", self.description)
        _normalize_string_tuple(self, "dependency_ids", allow_empty_items=False, unique=True)
        _require_enum("kind", self.kind, TaskKind)
        if self.owner is not None:
            _require_non_empty_string("owner", self.owner)
        _require_enum("state", self.state, TeamTaskState)
        _require_non_negative_int("plan_revision", self.plan_revision)
        _require_enum("approval_state", self.approval_state, ApprovalState)
        if self.result is not None:
            _require_instance("result", self.result, TaskResult)
        if self.error is not None:
            _require_non_empty_string("error", self.error)
        _require_non_negative_int("revision", self.revision)
        _require_optional_utc_datetime("created_at", self.created_at)
        _require_optional_utc_datetime("updated_at", self.updated_at)
        if self.state is TeamTaskState.COMPLETED:
            if self.result is None:
                raise ValueError("result must be present when task is completed")
            if self.error is not None:
                raise ValueError("error must be empty when task is completed")
        if self.state is TeamTaskState.FAILED:
            if self.error is None:
                raise ValueError("error must be present when task has failed")
            if self.result is not None:
                raise ValueError("error state cannot include result")


@dataclass(frozen=True)
class TeamMessage:
    message_id: str
    protocol: MessageProtocol
    sender: str
    target_name: str | None
    broadcast: bool
    body: str
    summary: str
    timestamp: datetime
    read: bool = False
    delivered: bool = False
    task_id: str | None = None
    batch_id: str | None = None

    def __post_init__(self) -> None:
        _require_non_empty_string("message_id", self.message_id)
        _require_enum("protocol", self.protocol, MessageProtocol)
        _require_non_empty_string("sender", self.sender)
        if self.target_name is None:
            if not self.broadcast:
                raise ValueError("target_name must be present for non-broadcast messages")
        else:
            _require_non_empty_string("target_name", self.target_name)
        _require_bool("broadcast", self.broadcast)
        _require_non_empty_string("body", self.body)
        _require_non_empty_string("summary", self.summary)
        _require_utc_datetime("timestamp", self.timestamp)
        _require_bool("read", self.read)
        _require_bool("delivered", self.delivered)
        if self.task_id is not None:
            _require_non_empty_string("task_id", self.task_id)
        if self.batch_id is not None:
            _require_non_empty_string("batch_id", self.batch_id)
        if self.protocol is MessageProtocol.BROADCAST:
            if not self.broadcast:
                raise ValueError("broadcast messages must set broadcast=True")
            if self.target_name is not None:
                raise ValueError("broadcast messages must not set target_name")
        elif self.broadcast and self.target_name is not None:
            raise ValueError("broadcast messages must not set target_name")


@dataclass(frozen=True)
class TeamSnapshot:
    team: TeamRecord
    members: tuple[MemberRecord, ...]
    batches: tuple[BatchRecord, ...]
    registry: Mapping[str, WakeEndpoint]
    lead_lease: LeadLease | None = None

    def __post_init__(self) -> None:
        _require_instance("team", self.team, TeamRecord)
        _normalize_dataclass_tuple(self, "members", MemberRecord)
        _normalize_dataclass_tuple(self, "batches", BatchRecord)
        _normalize_mapping(self, "registry", WakeEndpoint)
        if self.lead_lease is not None:
            _require_instance("lead_lease", self.lead_lease, LeadLease)
            if self.lead_lease.team_name != self.team.team_name:
                raise ValueError("lead_lease team_name must match team.team_name")


@dataclass(frozen=True)
class TaskPatch:
    title: str | None = None
    description: str | None = None
    dependency_ids: tuple[str, ...] | None = None
    kind: TaskKind | None = None
    owner: str | None = None
    plan_revision: int | None = None
    approval_state: ApprovalState | None = None

    def __post_init__(self) -> None:
        if self.title is not None:
            _require_non_empty_string("title", self.title)
        if self.description is not None:
            _require_non_empty_string("description", self.description)
        if self.dependency_ids is not None:
            _normalize_string_tuple(self, "dependency_ids", allow_empty_items=False, unique=True)
        if self.kind is not None:
            _require_enum("kind", self.kind, TaskKind)
        if self.owner is not None:
            _require_non_empty_string("owner", self.owner)
        if self.plan_revision is not None:
            _require_non_negative_int("plan_revision", self.plan_revision)
        if self.approval_state is not None:
            _require_enum("approval_state", self.approval_state, ApprovalState)


@dataclass(frozen=True)
class TaskResult:
    summary: str
    commit_id: str | None = None
    verification_summary: str | None = None
    details: str | None = None
    artifact_paths: tuple[Path, ...] = ()
    diagnostics: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _require_non_empty_string("summary", self.summary)
        if self.commit_id is not None:
            _require_git_commit_id("commit_id", self.commit_id)
        if self.verification_summary is not None:
            _require_non_empty_string("verification_summary", self.verification_summary)
        if self.details is not None:
            _require_non_empty_string("details", self.details)
        _normalize_path_tuple(self, "artifact_paths")
        _normalize_string_tuple(self, "diagnostics")


@dataclass(frozen=True)
class MemberSpec:
    member_name: str
    role_name: str
    role_revision: int
    requested_backend: MemberBackend
    goal: str
    batch_id: str
    task_id: str
    read_only: bool
    approval_required: bool

    def __post_init__(self) -> None:
        _require_non_empty_string("member_name", self.member_name)
        _require_non_empty_string("role_name", self.role_name)
        _require_non_negative_int("role_revision", self.role_revision)
        _require_enum("requested_backend", self.requested_backend, MemberBackend)
        _require_non_empty_string("goal", self.goal)
        _require_non_empty_string("batch_id", self.batch_id)
        _require_non_empty_string("task_id", self.task_id)
        _require_bool("read_only", self.read_only)
        _require_bool("approval_required", self.approval_required)


@dataclass(frozen=True)
class MemberLaunchSpec:
    team_name: str
    member_name: str
    role_name: str
    role_revision: int
    requested_backend: MemberBackend
    resolved_backend: ResolvedBackend
    argv: tuple[str, ...]
    environment: Mapping[str, str]
    workspace_root: Path
    repository_root: Path
    repository_id: str
    branch_name: str
    mailbox_path: Path
    context_path: Path
    wake_endpoint: WakeEndpoint
    task_id: str
    batch_id: str
    goal: str
    approval_required: bool
    read_only: bool
    revision: int

    def __post_init__(self) -> None:
        _require_non_empty_string("team_name", self.team_name)
        _require_non_empty_string("member_name", self.member_name)
        _require_non_empty_string("role_name", self.role_name)
        _require_non_negative_int("role_revision", self.role_revision)
        _require_enum("requested_backend", self.requested_backend, MemberBackend)
        _require_enum("resolved_backend", self.resolved_backend, ResolvedBackend)
        _normalize_string_tuple(self, "argv", allow_empty_items=False)
        _normalize_string_mapping(self, "environment")
        _require_absolute_path("workspace_root", self.workspace_root)
        _require_absolute_path("repository_root", self.repository_root)
        _require_non_empty_string("repository_id", self.repository_id)
        _require_non_empty_string("branch_name", self.branch_name)
        _require_absolute_path("mailbox_path", self.mailbox_path)
        _require_absolute_path("context_path", self.context_path)
        _require_instance("wake_endpoint", self.wake_endpoint, WakeEndpoint)
        if self.wake_endpoint.member_name != self.member_name:
            raise ValueError("wake_endpoint member_name must match member_name")
        _require_non_empty_string("task_id", self.task_id)
        _require_non_empty_string("batch_id", self.batch_id)
        _require_non_empty_string("goal", self.goal)
        _require_bool("approval_required", self.approval_required)
        _require_bool("read_only", self.read_only)
        _require_non_negative_int("revision", self.revision)


@dataclass(frozen=True)
class BackendEnvironment:
    requested_backend: MemberBackend
    platform: str
    shell_name: str
    tmux_available: bool
    terminal_available: bool
    in_process_available: bool
    coordinator_enabled: bool
    workspace_root: Path
    repository_root: Path
    member_name: str
    diagnostics: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _require_enum("requested_backend", self.requested_backend, MemberBackend)
        _require_non_empty_string("platform", self.platform)
        _require_non_empty_string("shell_name", self.shell_name)
        _require_bool("tmux_available", self.tmux_available)
        _require_bool("terminal_available", self.terminal_available)
        _require_bool("in_process_available", self.in_process_available)
        _require_bool("coordinator_enabled", self.coordinator_enabled)
        _require_absolute_path("workspace_root", self.workspace_root)
        _require_absolute_path("repository_root", self.repository_root)
        _require_non_empty_string("member_name", self.member_name)
        _normalize_string_tuple(self, "diagnostics")


@dataclass(frozen=True)
class BackendSelection:
    requested_backend: MemberBackend
    resolved_backend: ResolvedBackend | None
    available: bool
    reason_code: str | None = None
    reason: str | None = None
    environment: BackendEnvironment | None = None
    fallback_chain: tuple[ResolvedBackend, ...] = ()

    def __post_init__(self) -> None:
        _require_enum("requested_backend", self.requested_backend, MemberBackend)
        if self.resolved_backend is not None:
            _require_enum("resolved_backend", self.resolved_backend, ResolvedBackend)
        _require_bool("available", self.available)
        if self.available and self.resolved_backend is None:
            raise ValueError("available selections must include resolved_backend")
        if not self.available and self.resolved_backend is not None:
            raise ValueError("unavailable selections must not include resolved_backend")
        if self.reason_code is not None:
            _require_non_empty_string("reason_code", self.reason_code)
        if self.reason is not None:
            _require_non_empty_string("reason", self.reason)
        if self.environment is not None:
            _require_instance("environment", self.environment, BackendEnvironment)
        _normalize_enum_tuple(self, "fallback_chain", ResolvedBackend)


@dataclass(frozen=True)
class BackendHandle:
    wake_endpoint: WakeEndpoint
    process_id: int
    started_at: datetime
    token: str

    def __post_init__(self) -> None:
        _require_instance("wake_endpoint", self.wake_endpoint, WakeEndpoint)
        _require_non_negative_int("process_id", self.process_id)
        _require_utc_datetime("started_at", self.started_at)
        _require_non_empty_string("token", self.token)


@dataclass(frozen=True)
class LeadLease:
    team_name: str
    owner: str
    lock_path: Path
    token: str
    acquired_at: datetime
    process_id: int
    revision: int
    expires_at: datetime

    def __post_init__(self) -> None:
        _require_non_empty_string("team_name", self.team_name)
        _require_non_empty_string("owner", self.owner)
        _require_absolute_path("lock_path", self.lock_path)
        _require_non_empty_string("token", self.token)
        _require_utc_datetime("acquired_at", self.acquired_at)
        _require_non_negative_int("process_id", self.process_id)
        _require_non_negative_int("revision", self.revision)
        _require_utc_datetime("expires_at", self.expires_at)
        if self.expires_at < self.acquired_at:
            raise ValueError("expires_at must not be earlier than acquired_at")


@dataclass(frozen=True)
class WakeEndpoint:
    member_name: str
    backend: ResolvedBackend
    endpoint: str
    revision: int

    def __post_init__(self) -> None:
        _require_non_empty_string("member_name", self.member_name)
        _require_enum("backend", self.backend, ResolvedBackend)
        _require_non_empty_string("endpoint", self.endpoint)
        _require_non_negative_int("revision", self.revision)


@dataclass(frozen=True)
class DeliveryReceipt:
    message_id: str
    recipient_names: tuple[str, ...]
    delivered_at: datetime
    fanout_count: int
    duplicate_count: int = 0

    def __post_init__(self) -> None:
        _require_non_empty_string("message_id", self.message_id)
        _normalize_string_tuple(self, "recipient_names", allow_empty_items=False, unique=True)
        _require_utc_datetime("delivered_at", self.delivered_at)
        _require_non_negative_int("fanout_count", self.fanout_count)
        _require_non_negative_int("duplicate_count", self.duplicate_count)
        if self.fanout_count != len(self.recipient_names):
            raise ValueError("fanout_count must match recipient_names")


@dataclass(frozen=True)
class IntegrationReport:
    batch_id: str
    state: BatchState
    target_ref_before: str
    target_ref_after: str
    result_commit_id: str | None = None
    conflict_task_id: str | None = None
    integrated_member_names: tuple[str, ...] = ()
    diagnostics: tuple[str, ...] = ()
    started_at: datetime | None = None
    completed_at: datetime | None = None

    def __post_init__(self) -> None:
        _require_non_empty_string("batch_id", self.batch_id)
        _require_enum("state", self.state, BatchState)
        _require_non_empty_string("target_ref_before", self.target_ref_before)
        _require_non_empty_string("target_ref_after", self.target_ref_after)
        if self.result_commit_id is not None:
            _require_git_commit_id("result_commit_id", self.result_commit_id)
        if self.conflict_task_id is not None:
            _require_non_empty_string("conflict_task_id", self.conflict_task_id)
        _normalize_string_tuple(self, "integrated_member_names", allow_empty_items=False, unique=True)
        _normalize_string_tuple(self, "diagnostics")
        _require_optional_utc_datetime("started_at", self.started_at)
        _require_optional_utc_datetime("completed_at", self.completed_at)
        if self.state is BatchState.COMPLETED:
            if self.completed_at is None:
                raise ValueError("completed_at must be present when integration completed")
            _require_git_commit_id("result_commit_id", self.result_commit_id)


@dataclass(frozen=True)
class TeamError(RuntimeError):
    code: str
    phase: str
    message: str
    team_name: str | None = None
    member_name: str | None = None
    batch_id: str | None = None
    task_id: str | None = None
    path: Path | None = None
    revision: int = 0

    def __init__(
        self,
        *,
        code: str,
        phase: str,
        message: str,
        team_name: str | None = None,
        member_name: str | None = None,
        batch_id: str | None = None,
        task_id: str | None = None,
        path: Path | None = None,
        revision: int = 0,
    ) -> None:
        _require_non_empty_string("code", code)
        _require_non_empty_string("phase", phase)
        _require_non_empty_string("message", message)
        if team_name is not None:
            _require_non_empty_string("team_name", team_name)
        if member_name is not None:
            _require_non_empty_string("member_name", member_name)
        if batch_id is not None:
            _require_non_empty_string("batch_id", batch_id)
        if task_id is not None:
            _require_non_empty_string("task_id", task_id)
        if path is not None:
            _require_absolute_path("path", path)
        _require_non_negative_int("revision", revision)
        super().__init__(message)
        object.__setattr__(self, "code", code)
        object.__setattr__(self, "phase", phase)
        object.__setattr__(self, "message", message)
        object.__setattr__(self, "team_name", team_name)
        object.__setattr__(self, "member_name", member_name)
        object.__setattr__(self, "batch_id", batch_id)
        object.__setattr__(self, "task_id", task_id)
        object.__setattr__(self, "path", path)
        object.__setattr__(self, "revision", revision)


def _require_instance(field_name: str, value: object, expected_type: type) -> None:
    if not isinstance(value, expected_type):
        raise ValueError(f"{field_name} must be a {expected_type.__name__}")


def _require_enum(field_name: str, value: object, enum_type: type[Enum]) -> None:
    if not isinstance(value, enum_type):
        raise ValueError(f"{field_name} must be a {enum_type.__name__}")


def _require_non_empty_string(field_name: str, value: object) -> None:
    if type(value) is not str or not value:
        raise ValueError(f"{field_name} must be a non-empty string")


def _require_bool(field_name: str, value: object) -> None:
    if type(value) is not bool:
        raise ValueError(f"{field_name} must be a bool")


def _require_positive_int(field_name: str, value: object) -> None:
    if type(value) is not int or value <= 0:
        raise ValueError(f"{field_name} must be a positive integer")


def _require_non_negative_int(field_name: str, value: object) -> None:
    if type(value) is not int or value < 0:
        raise ValueError(f"{field_name} must be a non-negative integer")


def _require_absolute_path(field_name: str, value: object) -> None:
    if not isinstance(value, Path):
        raise ValueError(f"{field_name} must be a Path")
    if not value.is_absolute():
        raise ValueError(f"{field_name} must be an absolute path")


def _require_optional_absolute_path(field_name: str, value: object) -> None:
    if value is None:
        return
    _require_absolute_path(field_name, value)


def _require_utc_datetime(field_name: str, value: object) -> None:
    if not isinstance(value, datetime):
        raise ValueError(f"{field_name} must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must include UTC timezone")
    if value.utcoffset() != timezone.utc.utcoffset(value):
        raise ValueError(f"{field_name} must use UTC timezone")


def _require_optional_utc_datetime(field_name: str, value: object) -> None:
    if value is None:
        return
    _require_utc_datetime(field_name, value)


def _require_git_commit_id(field_name: str, value: object | None) -> None:
    if type(value) is not str or len(value) != 40:
        raise ValueError(f"{field_name} must be a 40-character git commit id")
    if any(char not in "0123456789abcdef" for char in value.lower()):
        raise ValueError(f"{field_name} must be a 40-character git commit id")


def _normalize_string_tuple(
    instance: object,
    field_name: str,
    *,
    allow_empty_items: bool = True,
    unique: bool = False,
) -> None:
    value = getattr(instance, field_name)
    if value is None:
        object.__setattr__(instance, field_name, ())
        return
    if not isinstance(value, tuple):
        value = tuple(value)
        object.__setattr__(instance, field_name, value)
    seen: set[str] = set()
    for item in value:
        if type(item) is not str:
            raise ValueError(f"{field_name} must contain strings")
        if not allow_empty_items and not item:
            raise ValueError(f"{field_name} must not contain empty strings")
        if unique:
            if item in seen:
                raise ValueError(f"{field_name} must not contain duplicate values")
            seen.add(item)


def _normalize_path_tuple(instance: object, field_name: str) -> None:
    value = getattr(instance, field_name)
    if not isinstance(value, tuple):
        value = tuple(value)
        object.__setattr__(instance, field_name, value)
    for item in value:
        _require_absolute_path(field_name, item)


def _normalize_enum_tuple(instance: object, field_name: str, enum_type: type[Enum]) -> None:
    value = getattr(instance, field_name)
    if not isinstance(value, tuple):
        value = tuple(value)
        object.__setattr__(instance, field_name, value)
    for item in value:
        if not isinstance(item, enum_type):
            raise ValueError(f"{field_name} must contain {enum_type.__name__} values")


def _normalize_string_mapping(instance: object, field_name: str) -> None:
    value = getattr(instance, field_name)
    if value is None:
        object.__setattr__(instance, field_name, MappingProxyType({}))
        return
    if not isinstance(value, Mapping):
        value = dict(value)
    normalized: dict[str, str] = {}
    for key, item in value.items():
        if type(key) is not str or not key:
            raise ValueError(f"{field_name} keys must be non-empty strings")
        if type(item) is not str:
            raise ValueError(f"{field_name} values must be strings")
        normalized[key] = item
    object.__setattr__(instance, field_name, MappingProxyType(normalized))


def _normalize_dataclass_tuple(instance: object, field_name: str, expected_type: type) -> None:
    value = getattr(instance, field_name)
    if not isinstance(value, tuple):
        value = tuple(value)
        object.__setattr__(instance, field_name, value)
    for item in value:
        if not isinstance(item, expected_type):
            raise ValueError(f"{field_name} must contain {expected_type.__name__} values")


def _normalize_mapping(instance: object, field_name: str, expected_type: type) -> None:
    value = getattr(instance, field_name)
    if value is None:
        object.__setattr__(instance, field_name, MappingProxyType({}))
        return
    if not isinstance(value, Mapping):
        value = dict(value)
    normalized: dict[str, object] = {}
    for key, item in value.items():
        if type(key) is not str or not key:
            raise ValueError(f"{field_name} keys must be non-empty strings")
        if not isinstance(item, expected_type):
            raise ValueError(f"{field_name} must contain {expected_type.__name__} values")
        normalized[key] = item
    object.__setattr__(instance, field_name, MappingProxyType(normalized))
