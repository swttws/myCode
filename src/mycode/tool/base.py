from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


JSONSchema = dict[str, Any]
ToolArguments = dict[str, Any]


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    description: str
    parameters: JSONSchema


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
