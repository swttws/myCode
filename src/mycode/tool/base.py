from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Protocol


JSONSchema = dict[str, Any]
ToolArguments = dict[str, Any]


class ToolKind(str, Enum):
    # 本地调度元信息，不会进入供应商 tool payload。
    READ = "read"
    WRITE = "write"


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    description: str
    parameters: JSONSchema
    kind: ToolKind


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: ToolArguments | None
    raw_arguments: str = ""


@dataclass(frozen=True)
class ToolResult:
    ok: bool
    tool_name: str
    content: dict[str, Any]
    error: str | None = None


class Tool(Protocol):
    @property
    def definition(self) -> ToolDefinition:
        raise NotImplementedError

    def execute(self, arguments: ToolArguments) -> ToolResult:
        raise NotImplementedError
