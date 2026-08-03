from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from types import MappingProxyType
from typing import Mapping

from mycode.llm import ChatMessage, UsageObservation
from mycode.permission.models import PermissionMode
from mycode.prompt.models import PromptContextBlock
from mycode.tool import ToolDefinition


RESULT_TRUNCATED_MARKER = "...[结果已截断]"
DEFAULT_FOREGROUND_TIMEOUT_SECONDS = 120.0
DEFAULT_MAX_CONCURRENCY = 4
DEFAULT_BACKGROUND_ALLOWED_TOOLS = ("read_file", "find_files", "search_code")
DEFAULT_MAX_TASK_BYTES = 64 * 1024
DEFAULT_MAX_RESULT_BYTES = 128 * 1024
DEFAULT_MAX_NOTIFICATION_BYTES = 4 * 1024
DEFAULT_MAX_QUEUED_TASKS = 64
DEFAULT_MAX_RETAINED_TASKS = 256


class SubAgentKind(str, Enum):
    DEFINED = "defined"
    FORK = "fork"


class AgentRoleSource(str, Enum):
    PLUGIN = "plugin"
    BUILTIN = "builtin"
    USER = "user"
    PROJECT = "project"


class AgentModelTier(str, Enum):
    INHERIT = "inherit"
    HAIKU = "haiku"
    SONNET = "sonnet"
    OPUS = "opus"


class AgentPermissionMode(str, Enum):
    INHERIT = "inherit"
    STRICT = "strict"
    DEFAULT = "default"
    PERMISSIVE = "permissive"


class SubAgentTaskState(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class AgentRoleMetadata:
    name: str  # 角色名称，必须与角色文件名保持一致。
    description: str  # 给父 Agent 和用户看的角色用途说明。
    allowed_tools: tuple[str, ...]  # 角色允许暴露给模型的普通工具名。
    denied_tools: tuple[str, ...]  # 黑名单优先于白名单。
    model: AgentModelTier  # inherit 表示复用父模型，否则映射到配置中的具体模型。
    max_rounds: int  # 子 Agent 最大模型轮次。
    permission_mode: AgentPermissionMode  # 子 Agent 权限只能继承或收紧父权限。

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("role name must not be empty.")
        if not self.description:
            raise ValueError("role description must not be empty.")
        if type(self.max_rounds) is not int or self.max_rounds <= 0:
            raise ValueError("max_rounds must be a positive integer.")
        _ensure_tuple_of_strings(self.allowed_tools, "allowed_tools")
        _ensure_tuple_of_strings(self.denied_tools, "denied_tools")


@dataclass(frozen=True)
class AgentRoleDefinition:
    metadata: AgentRoleMetadata  # 校验后的角色元数据。
    instruction: str  # 伴随子 Agent 生命周期的角色系统提示。
    source: AgentRoleSource  # 最终生效的角色来源。
    entry_path: Path  # 可定位诊断用的角色入口路径。
    revision: str  # 规范化角色文件内容的稳定指纹。

    def __post_init__(self) -> None:
        if not self.instruction:
            raise ValueError("instruction must not be empty.")
        if not self.revision:
            raise ValueError("revision must not be empty.")


@dataclass(frozen=True)
class AgentRoleDiagnostic:
    code: str  # 稳定错误码。
    source: AgentRoleSource  # 产生诊断的候选来源。
    path: str  # 可定位到文件的路径文本。
    message: str  # 面向用户的中文诊断摘要。
    role_name: str | None = None

    def __post_init__(self) -> None:
        if not self.code:
            raise ValueError("error_code must not be empty.")
        if not self.path:
            raise ValueError("path must not be empty.")
        if not self.message:
            raise ValueError("message must not be empty.")


@dataclass(frozen=True)
class AgentCatalogSnapshot:
    definitions: tuple[AgentRoleDefinition, ...]
    diagnostics: tuple[AgentRoleDiagnostic, ...]
    generation: int


@dataclass(frozen=True)
class SubAgentConfig:
    model_map: Mapping[AgentModelTier, str]  # 三个模型档位到当前协议具体模型 ID 的映射。
    foreground_timeout_seconds: float = DEFAULT_FOREGROUND_TIMEOUT_SECONDS
    max_concurrency: int = DEFAULT_MAX_CONCURRENCY
    background_allowed_tools: tuple[str, ...] = DEFAULT_BACKGROUND_ALLOWED_TOOLS
    max_task_bytes: int = DEFAULT_MAX_TASK_BYTES
    max_result_bytes: int = DEFAULT_MAX_RESULT_BYTES
    max_notification_bytes: int = DEFAULT_MAX_NOTIFICATION_BYTES
    max_queued_tasks: int = DEFAULT_MAX_QUEUED_TASKS
    max_retained_tasks: int = DEFAULT_MAX_RETAINED_TASKS

    def __post_init__(self) -> None:
        model_map = dict(self.model_map)
        object.__setattr__(self, "model_map", MappingProxyType(model_map))
        _ensure_tuple_of_strings(self.background_allowed_tools, "background_allowed_tools")


@dataclass(frozen=True)
class ParentAgentSnapshot:
    messages: tuple[ChatMessage, ...]  # 父请求构建完成后的消息前缀快照。
    tools: tuple[ToolDefinition, ...]  # 父请求构建完成后的工具 schema 快照。
    model_id: str  # 父 Agent 当前真实模型 ID。
    max_rounds: int  # 父 Agent 当前最大轮次。
    permission_mode: PermissionMode  # 父 Agent 当前有效权限档位。


@dataclass(frozen=True)
class SubAgentLaunchRequest:
    kind: SubAgentKind
    task: str
    role_name: str | None
    requested_background: bool
    parent: ParentAgentSnapshot


@dataclass(frozen=True)
class SubAgentUsage:
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    cache_read_tokens: int | None = None
    cache_write_tokens: int | None = None

    @classmethod
    def aggregate(cls, observations: tuple[UsageObservation, ...]) -> "SubAgentUsage":
        return cls(
            input_tokens=_sum_if_complete(observations, "input_tokens"),
            output_tokens=_sum_if_complete(observations, "output_tokens"),
            total_tokens=_sum_if_complete(observations, "total_tokens"),
            cache_read_tokens=_sum_if_complete(observations, "cache_read_tokens"),
            cache_write_tokens=_sum_if_complete(observations, "cache_write_tokens"),
        )


@dataclass(frozen=True)
class SubAgentResult:
    detail: str
    summary: str
    detail_truncated: bool = False
    summary_truncated: bool = False


@dataclass(frozen=True)
class SubAgentTaskSummary:
    id: str
    sequence: int
    kind: SubAgentKind
    role_name: str | None
    state: SubAgentTaskState
    detached: bool
    rounds: int
    error_code: str | None
    usage: SubAgentUsage

    def __post_init__(self) -> None:
        _validate_task_identity(self.id, self.sequence, self.rounds)
        if self.state is SubAgentTaskState.FAILED and not self.error_code:
            raise ValueError("error_code must be present for failed tasks.")


@dataclass(frozen=True)
class SubAgentTaskSnapshot:
    id: str
    sequence: int
    kind: SubAgentKind
    role_name: str | None
    state: SubAgentTaskState
    detached: bool
    rounds: int
    result: SubAgentResult | None
    error_code: str | None
    error_message: str | None
    usage: SubAgentUsage

    def __post_init__(self) -> None:
        _validate_task_identity(self.id, self.sequence, self.rounds)
        _validate_state_payload(
            self.state,
            self.result,
            self.error_code,
            self.error_message,
            allow_non_terminal=True,
        )


@dataclass(frozen=True)
class SubAgentNotification:
    task_id: str
    state: SubAgentTaskState
    summary: str
    summary_truncated: bool
    usage: SubAgentUsage
    role_name: str | None = None


@dataclass(frozen=True)
class SubAgentExecutionReport:
    state: SubAgentTaskState
    rounds: int
    result: SubAgentResult | None
    error_code: str | None
    error_message: str | None
    usage: SubAgentUsage

    def __post_init__(self) -> None:
        if self.rounds < 0:
            raise ValueError("rounds must not be negative.")
        _validate_state_payload(
            self.state,
            self.result,
            self.error_code,
            self.error_message,
            allow_non_terminal=False,
        )


@dataclass(frozen=True)
class ToolPolicyDecision:
    allowed: bool
    reason_code: str | None = None
    message_zh: str | None = None


@dataclass(frozen=True)
class NotificationReservation:
    id: str
    notifications: tuple[SubAgentNotification, ...]
    dropped_count: int
    block: PromptContextBlock


def truncate_utf8_bytes(text: str, max_bytes: int) -> tuple[str, bool]:
    if type(max_bytes) is not int or max_bytes <= 0:
        raise ValueError("max_bytes must be a positive integer.")
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text, False

    marker_bytes = RESULT_TRUNCATED_MARKER.encode("utf-8")
    if len(marker_bytes) >= max_bytes:
        return _decode_prefix(marker_bytes, max_bytes), True

    content_limit = max_bytes - len(marker_bytes)
    return _decode_prefix(encoded, content_limit) + RESULT_TRUNCATED_MARKER, True


def _decode_prefix(encoded: bytes, limit: int) -> str:
    return encoded[:limit].decode("utf-8", errors="ignore")


def _sum_if_complete(observations: tuple[UsageObservation, ...], field_name: str) -> int | None:
    values: list[int] = []
    for observation in observations:
        value = getattr(observation, field_name)
        if value is None:
            return None
        if value < 0:
            return None
        values.append(value)
    if not values:
        return None
    return sum(values)


def _ensure_tuple_of_strings(values: tuple[str, ...], field_name: str) -> None:
    if not isinstance(values, tuple) or any(type(value) is not str or not value for value in values):
        raise ValueError(f"{field_name} must be a tuple of non-empty strings.")


def _validate_task_identity(task_id: str, sequence: int, rounds: int) -> None:
    if not task_id:
        raise ValueError("task id must not be empty.")
    if type(sequence) is not int or sequence <= 0:
        raise ValueError("sequence must be a positive integer.")
    if type(rounds) is not int or rounds < 0:
        raise ValueError("rounds must not be negative.")


def _validate_state_payload(
    state: SubAgentTaskState,
    result: SubAgentResult | None,
    error_code: str | None,
    error_message: str | None,
    *,
    allow_non_terminal: bool,
) -> None:
    if state in (SubAgentTaskState.QUEUED, SubAgentTaskState.RUNNING):
        if not allow_non_terminal:
            raise ValueError("execution report state must be terminal.")
        if result is not None or error_code is not None or error_message is not None:
            raise ValueError("non-terminal task must not include result or error.")
        return
    if state is SubAgentTaskState.COMPLETED:
        if result is None:
            raise ValueError("result must be present for completed tasks.")
        if error_code is not None or error_message is not None:
            raise ValueError("completed task must not include error_code.")
        return
    if state in (SubAgentTaskState.FAILED, SubAgentTaskState.CANCELLED):
        if not error_code:
            raise ValueError("error_code must be present for failed or cancelled tasks.")
