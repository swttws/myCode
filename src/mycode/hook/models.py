from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Literal

from mycode.llm.base import ChatMessage
from mycode.permission.models import PermissionScalar
from mycode.prompt.models import PromptContextBlock
from mycode.tool.base import ToolCall, ToolDefinition, ToolResult


class HookEvent(str, Enum):
    APP_STARTED = "app_started"
    HOOKS_LOADED = "hooks_loaded"
    RUNTIME_ERROR = "runtime_error"
    SESSION_START = "session_start"
    SESSION_END = "session_end"
    SESSION_CLEAR = "session_clear"
    USER_REQUEST_START = "user_request_start"
    USER_REQUEST_END = "user_request_end"
    MODEL_ROUND_START = "model_round_start"
    MODEL_ROUND_END = "model_round_end"
    USER_MESSAGE = "user_message"
    ASSISTANT_MESSAGE = "assistant_message"
    TOOL_RESULT_MESSAGE = "tool_result_message"
    TOOL_BEFORE = "tool_before"
    TOOL_AFTER = "tool_after"


class HookActionType(str, Enum):
    COMMAND = "command"
    PROMPT = "prompt"
    HTTP = "http"
    SUB_AGENT = "sub_agent"


class MatchKind(str, Enum):
    EXACT = "exact"
    GLOB = "glob"
    REGEX = "regex"


@dataclass(frozen=True)
class ValueMatcher:
    kind: MatchKind
    expected: PermissionScalar | str
    negate: bool = False


@dataclass(frozen=True)
class HookPredicate:
    field: str
    matcher: ValueMatcher


@dataclass(frozen=True)
class HookCondition:
    mode: Literal["all", "any"]
    predicates: tuple[HookPredicate, ...]


@dataclass(frozen=True)
class HookAction:
    type: HookActionType
    command: str | None = None
    cwd: str | None = None
    env: Mapping[str, str] = field(default_factory=dict)
    content: str | None = None
    method: str | None = None
    url: str | None = None
    headers: Mapping[str, str] = field(default_factory=dict)
    json_body: Mapping[str, object] | None = None
    task: str | None = None
    input: Mapping[str, object] | None = None
    output: str | None = None
    block: bool = False
    reason: str | None = None


@dataclass(frozen=True)
class HookRule:
    id: str
    event: HookEvent
    condition: HookCondition | None
    action: HookAction
    once: bool
    background: bool
    timeout_seconds: float | None
    index: int


@dataclass(frozen=True)
class HookConfig:
    version: int
    rules: tuple[HookRule, ...]
    path: Path | None = None


@dataclass(frozen=True)
class HookContext:
    event: HookEvent
    workspace_root: Path
    turn_id: int | None = None
    round_index: int | None = None
    user_text: str | None = None
    message: ChatMessage | None = None
    tool_call: ToolCall | None = None
    tool_definition: ToolDefinition | None = None
    normalized_arguments: Mapping[str, object] = field(default_factory=dict)
    raw_arguments: Mapping[str, object] = field(default_factory=dict)
    tool_result: ToolResult | None = None
    error_code: str | None = None
    error_message: str | None = None
    plan_only: bool = False


@dataclass(frozen=True)
class HookActionResult:
    ok: bool
    output: str = ""
    error: str | None = None
    blocked: bool = False
    block_reason: str | None = None


@dataclass(frozen=True)
class HookTriggerResult:
    actions: tuple[HookActionResult, ...]
    blocked_tool_result: ToolResult | None = None


@dataclass(frozen=True)
class HookPromptInjection:
    id: str
    rule_id: str
    content: str
    created_event: HookEvent


class HookError(RuntimeError):
    """Hook 领域错误基类。"""


class HookConfigError(HookError):
    """Hook 配置无法安全加载。"""


class HookExecutionError(HookError):
    """Hook 动作执行失败。"""
