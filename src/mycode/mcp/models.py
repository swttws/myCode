from __future__ import annotations

import math
import re
from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Mapping

from mycode.tool.base import JSONSchema, ToolKind


SERVER_NAME_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*$")


class MCPTransportKind(str, Enum):
    STDIO = "stdio"
    STREAMABLE_HTTP = "streamable_http"


class MCPServerState(str, Enum):
    NEW = "new"
    CONNECTING = "connecting"
    READY = "ready"
    FAILED = "failed"
    CLOSED = "closed"


@dataclass(frozen=True)
class MCPServerConfig:
    name: str
    transport: MCPTransportKind
    timeout_seconds: float
    command: str | None
    args: tuple[str, ...]
    env: Mapping[str, str]
    url: str | None
    headers: Mapping[str, str]
    read_tools: frozenset[str]

    def __post_init__(self) -> None:
        if not SERVER_NAME_PATTERN.fullmatch(self.name):
            raise ValueError("invalid MCP server name")

        try:
            transport = MCPTransportKind(self.transport)
        except (TypeError, ValueError) as exc:
            raise ValueError("invalid MCP transport") from exc

        timeout = self.timeout_seconds
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
            raise ValueError("MCP timeout must be a positive finite number")
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("MCP timeout must be a positive finite number")

        object.__setattr__(self, "transport", transport)
        object.__setattr__(self, "timeout_seconds", float(timeout))
        object.__setattr__(self, "args", tuple(self.args))
        object.__setattr__(self, "env", MappingProxyType(dict(self.env)))
        object.__setattr__(self, "headers", MappingProxyType(dict(self.headers)))
        object.__setattr__(self, "read_tools", frozenset(self.read_tools))


@dataclass(frozen=True)
class MCPConfig:
    servers: tuple[MCPServerConfig, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "servers", tuple(self.servers))


@dataclass(frozen=True)
class MCPDiagnostic:
    server_name: str | None
    category: str
    message: str


@dataclass(frozen=True)
class RemoteTool:
    server_name: str
    remote_name: str
    public_name: str
    description: str
    parameters: JSONSchema
    kind: ToolKind


@dataclass(frozen=True)
class DeferredToolSummary:
    name: str
    description: str
