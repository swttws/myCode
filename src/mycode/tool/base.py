from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum
from typing import Any, Protocol, runtime_checkable

from mycode.workspace import WorkspaceContext


JSONSchema = dict[str, Any]
ToolArguments = dict[str, Any]


class ToolKind(str, Enum):
    # 本地调度元信息，不会进入供应商 tool payload。
    READ = "read"
    WRITE = "write"


class ToolRuntimeScope(str, Enum):
    # 本地运行时作用域，不会进入供应商 tool payload。
    SHARED = "shared"
    TASK_LOCAL = "task_local"
    PARENT_ONLY = "parent_only"


class ToolWorkspaceScope(str, Enum):
    # 本地工作区能力元信息，不会进入供应商 tool payload。
    WORKSPACE_AWARE = "workspace_aware"
    SHARED_ONLY = "shared_only"


@dataclass(frozen=True)
class ToolInvocationContext:
    workspace: WorkspaceContext


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    description: str
    parameters: JSONSchema
    kind: ToolKind
    grant_arguments: tuple[str, ...] = ()
    parallel_safe: bool = True
    requires_approval: bool = True
    runtime_scope: ToolRuntimeScope = ToolRuntimeScope.SHARED
    workspace_scope: ToolWorkspaceScope = ToolWorkspaceScope.SHARED_ONLY
    execution_timeout_seconds: float | None = None

    def __post_init__(self) -> None:
        if self.kind not in (ToolKind.READ, ToolKind.WRITE):
            raise ValueError(f"invalid tool kind: {self.kind}")
        if type(self.requires_approval) is not bool:
            raise ValueError("requires_approval must be a bool.")
        if self.runtime_scope not in (
            ToolRuntimeScope.SHARED,
            ToolRuntimeScope.TASK_LOCAL,
            ToolRuntimeScope.PARENT_ONLY,
        ):
            raise ValueError(f"invalid tool runtime scope: {self.runtime_scope}")
        if self.workspace_scope not in (
            ToolWorkspaceScope.WORKSPACE_AWARE,
            ToolWorkspaceScope.SHARED_ONLY,
        ):
            raise ValueError(f"invalid tool workspace scope: {self.workspace_scope}")
        timeout = self.execution_timeout_seconds
        if timeout is None:
            return
        if isinstance(timeout, bool) or type(timeout) not in (int, float):
            raise ValueError("execution_timeout_seconds must be a positive finite number.")
        if not math.isfinite(float(timeout)) or float(timeout) <= 0:
            raise ValueError("execution_timeout_seconds must be a positive finite number.")


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: ToolArguments | None
    raw_arguments: str = ""


@dataclass(frozen=True)
class ToolExecutionControl:
    stop_current_round: bool = False
    replan_next_round: bool = False

    def __post_init__(self) -> None:
        if type(self.stop_current_round) is not bool:
            raise ValueError("stop_current_round must be a bool")
        if type(self.replan_next_round) is not bool:
            raise ValueError("replan_next_round must be a bool")


@dataclass(frozen=True)
class ToolResult:
    ok: bool
    tool_name: str
    content: dict[str, Any]
    error: str | None = None
    control: ToolExecutionControl | None = None


@dataclass(frozen=True)
class DeferredToolSummary:
    name: str
    description: str


class Tool(Protocol):
    @property
    def definition(self) -> ToolDefinition:
        raise NotImplementedError

    def execute(
        self,
        arguments: ToolArguments,
        context: ToolInvocationContext | None = None,
    ) -> ToolResult:
        raise NotImplementedError


@runtime_checkable
class AsyncTool(Protocol):
    async def execute_async(
        self,
        arguments: ToolArguments,
        context: ToolInvocationContext | None = None,
    ) -> ToolResult:
        raise NotImplementedError


@runtime_checkable
class DeferredTool(Protocol):
    def should_defer(self) -> bool:
        raise NotImplementedError
