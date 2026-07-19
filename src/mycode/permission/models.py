from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import TypeAlias

from mycode.tool.base import ToolCall, ToolDefinition, ToolResult


class PermissionEffect(str, Enum):
    ALLOW = "allow"
    DENY = "deny"
    ASK = "ask"
    # FORBIDDEN 代表内置安全底线，配置和人工审批都不能创建或覆盖它。
    FORBIDDEN = "forbidden"


class PermissionMode(str, Enum):
    STRICT = "strict"
    DEFAULT = "default"
    PERMISSIVE = "permissive"


class RuleSource(str, Enum):
    SESSION = "session"
    LOCAL_PROJECT = "local_project"
    REPOSITORY_PROJECT = "repository_project"
    USER_GLOBAL = "user_global"


class ApprovalDecisionType(str, Enum):
    APPROVE_ONCE = "approve_once"
    APPROVE_SESSION = "approve_session"
    APPROVE_PROJECT = "approve_project"
    REJECT = "reject"
    CANCEL = "cancel"


class ApprovalOutcome(str, Enum):
    EXECUTE = "execute"
    REJECTED = "rejected"
    CANCELLED = "cancelled"
    ERROR = "error"


PermissionScalar: TypeAlias = str | int | float | bool


@dataclass(frozen=True)
class ArgumentCondition:
    name: str
    expected: PermissionScalar


@dataclass(frozen=True)
class PermissionRule:
    id: str
    effect: PermissionEffect
    tool: str
    arguments: tuple[ArgumentCondition, ...]
    source: RuleSource


@dataclass(frozen=True, order=True)
class RuleSpecificity:
    exact_tool: int
    constrained_arguments: int
    exact_arguments: int


@dataclass(frozen=True)
class RuleMatch:
    rule: PermissionRule
    specificity: RuleSpecificity


@dataclass(frozen=True)
class PermissionSubject:
    call: ToolCall
    definition: ToolDefinition
    normalized_arguments: Mapping[str, object]
    grant_arguments: Mapping[str, PermissionScalar]
    display_arguments: Mapping[str, object]


@dataclass(frozen=True)
class CommandAssessment:
    effect: PermissionEffect
    category: str | None
    reason_code: str | None
    message_zh: str | None


@dataclass(frozen=True)
class PermissionDecision:
    effect: PermissionEffect
    reason_code: str
    message_zh: str
    mode: PermissionMode
    display_arguments: Mapping[str, object]
    source: RuleSource | None = None
    rule_id: str | None = None
    risk_category: str | None = None


@dataclass(frozen=True)
class PermissionGrant:
    tool: str
    arguments: tuple[ArgumentCondition, ...]
    fingerprint: str


@dataclass(frozen=True)
class ApprovalRequest:
    id: str
    tool_call: ToolCall
    decision: PermissionDecision
    options: tuple[ApprovalDecisionType, ...]
    candidate_grant: PermissionGrant | None
    plan_only: bool
    round_index: int


@dataclass(frozen=True)
class ApprovalDecision:
    type: ApprovalDecisionType


@dataclass(frozen=True)
class ApprovalResolution:
    outcome: ApprovalOutcome
    tool_result: ToolResult | None = None


ApprovalProvider: TypeAlias = Callable[[ApprovalRequest], Awaitable[ApprovalDecision]]


@dataclass(frozen=True)
class PermissionFileConfig:
    version: int
    mode: PermissionMode | None
    rules: tuple[PermissionRule, ...]
    workspace: str | None = None


@dataclass(frozen=True)
class PermissionPaths:
    user_global: Path
    local_project: Path
    repository_project: Path


@dataclass
class PermissionSessionState:
    mode_override: PermissionMode | None = None
    rules: list[PermissionRule] = field(default_factory=list)

    def reset(self) -> None:
        self.mode_override = None
        self.rules.clear()


class PermissionError(RuntimeError):
    """权限领域错误基类。"""


class PermissionConfigError(PermissionError):
    """权限配置无法安全加载。"""


class PermissionEvaluationError(PermissionError):
    # 判定异常由上层转换为拒绝，不能因组件失败而降级为允许。
    """权限判定无法安全完成。"""


class PermissionPersistenceError(PermissionError):
    """本地授权无法原子持久化。"""
