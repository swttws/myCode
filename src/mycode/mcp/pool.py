from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field, replace
from typing import Callable

from mycode.mcp.connection import MCPConnection, MCPConnectionError
from mycode.mcp.models import (
    MCPConfig,
    MCPDiagnostic,
    MCPServerConfig,
    MCPServerState,
    MCPTransportKind,
    RemoteTool,
)
from mycode.mcp.stdio import StdioTransport
from mycode.mcp.streamable_http import StreamableHTTPTransport
from mycode.tool import ToolArguments, ToolKind, ToolResult


PUBLIC_TOOL_NAME_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")
ConnectionFactory = Callable[[MCPServerConfig], MCPConnection]


@dataclass
class _ServerEntry:
    config: MCPServerConfig
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    state: MCPServerState = MCPServerState.NEW
    connection: MCPConnection | None = None
    tools: tuple[RemoteTool, ...] = ()


class MCPServerPool:
    def __init__(
        self,
        config: MCPConfig,
        *,
        connection_factory: ConnectionFactory | None = None,
    ) -> None:
        self._entries = {
            server.name: _ServerEntry(config=server) for server in config.servers
        }
        self._connection_factory = connection_factory or _default_connection_factory
        self._diagnostics: tuple[MCPDiagnostic, ...] = ()
        self._closed = False

    @property
    def tools(self) -> tuple[RemoteTool, ...]:
        tools = [tool for entry in self._entries.values() for tool in entry.tools]
        return tuple(sorted(tools, key=lambda tool: tool.public_name))

    @property
    def diagnostics(self) -> tuple[MCPDiagnostic, ...]:
        return self._diagnostics

    def server_state(self, server_name: str) -> MCPServerState:
        entry = self._entries.get(server_name)
        if entry is None:
            raise KeyError(server_name)
        return entry.state

    def is_available(self, server_name: str) -> bool:
        entry = self._entries.get(server_name)
        return (
            entry is not None
            and entry.state is MCPServerState.READY
            and entry.connection is not None
            and not entry.connection.is_failed
        )

    async def initialize_all(self) -> tuple[MCPDiagnostic, ...]:
        results = await asyncio.gather(
            *(self._initialize_entry(entry) for entry in self._entries.values())
        )
        self._diagnostics = tuple(
            diagnostic for server_diagnostics in results for diagnostic in server_diagnostics
        )
        return self._diagnostics

    async def _initialize_entry(
        self,
        entry: _ServerEntry,
    ) -> tuple[MCPDiagnostic, ...]:
        async with entry.lock:
            if entry.state is MCPServerState.READY:
                return ()
            return await self._connect_entry(entry)

    async def call_tool(
        self,
        server_name: str,
        remote_name: str,
        arguments: ToolArguments,
    ) -> ToolResult:
        public_name = f"{server_name}__{remote_name}"
        entry = self._entries.get(server_name)
        if entry is None:
            return _tool_failure(public_name, "unknown_server")
        if not await self.ensure_available(server_name):
            return _tool_failure(public_name, "server_unavailable")

        connection = entry.connection
        if connection is None:
            return _tool_failure(public_name, "server_unavailable")
        try:
            result = await connection.request(
                "tools/call",
                {"name": remote_name, "arguments": dict(arguments)},
            )
        except MCPConnectionError as exc:
            await self._mark_failed(entry, connection)
            return _tool_failure(public_name, exc.category)
        except Exception:
            await self._mark_failed(entry, connection)
            return _tool_failure(public_name, "call_failed")

        is_error = result.get("isError", False)
        if not isinstance(is_error, bool):
            await self._mark_failed(entry, connection)
            return _tool_failure(public_name, "invalid_response")
        return ToolResult(
            ok=not is_error,
            tool_name=public_name,
            content=dict(result),
            error="remote MCP tool returned an error" if is_error else None,
        )

    async def ensure_available(self, server_name: str) -> bool:
        entry = self._entries.get(server_name)
        if entry is None or self._closed:
            return False
        async with entry.lock:
            if (
                entry.state is MCPServerState.READY
                and entry.connection is not None
                and not entry.connection.is_failed
            ):
                return True
            if entry.state is MCPServerState.CLOSED:
                return False

            old_connection = entry.connection
            if old_connection is not None and entry.state is not MCPServerState.FAILED:
                await old_connection.close()
            diagnostics = await self._connect_entry(entry)
            if diagnostics:
                self._diagnostics += diagnostics
            return entry.state is MCPServerState.READY

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        await asyncio.gather(*(self._close_entry(entry) for entry in self._entries.values()))

    async def _connect_entry(
        self,
        entry: _ServerEntry,
    ) -> tuple[MCPDiagnostic, ...]:
        entry.state = MCPServerState.CONNECTING
        connection = self._connection_factory(entry.config)
        entry.connection = connection
        try:
            discovered = await connection.initialize()
        except MCPConnectionError as exc:
            entry.state = MCPServerState.FAILED
            entry.tools = ()
            await connection.close()
            return (
                MCPDiagnostic(
                    server_name=entry.config.name,
                    category=exc.category,
                    message=f"MCP server initialization failed: {entry.config.name}",
                ),
            )
        except Exception:
            entry.state = MCPServerState.FAILED
            entry.tools = ()
            await connection.close()
            return (
                MCPDiagnostic(
                    server_name=entry.config.name,
                    category="connection",
                    message=f"MCP server initialization failed: {entry.config.name}",
                ),
            )

        entry.tools, diagnostics = _normalize_tools(entry.config, discovered)
        entry.state = MCPServerState.READY
        return diagnostics

    async def _mark_failed(
        self,
        entry: _ServerEntry,
        connection: MCPConnection,
    ) -> None:
        async with entry.lock:
            if entry.connection is not connection:
                return
            entry.state = MCPServerState.FAILED
            entry.tools = ()
            await connection.close()

    async def _close_entry(self, entry: _ServerEntry) -> None:
        async with entry.lock:
            if entry.state is MCPServerState.CLOSED:
                return
            if entry.connection is not None:
                await entry.connection.close()
            entry.tools = ()
            entry.state = MCPServerState.CLOSED


def _normalize_tools(
    config: MCPServerConfig,
    discovered: tuple[RemoteTool, ...],
) -> tuple[tuple[RemoteTool, ...], tuple[MCPDiagnostic, ...]]:
    tools: list[RemoteTool] = []
    diagnostics: list[MCPDiagnostic] = []
    public_names: set[str] = set()

    for tool in discovered:
        public_name = f"{config.name}__{tool.remote_name}"
        if not PUBLIC_TOOL_NAME_PATTERN.fullmatch(public_name):
            diagnostics.append(_tool_diagnostic(config.name, "incompatible tool name"))
            continue
        if public_name in public_names:
            diagnostics.append(_tool_diagnostic(config.name, "duplicate tool name"))
            continue

        public_names.add(public_name)
        kind = ToolKind.READ if tool.remote_name in config.read_tools else ToolKind.WRITE
        tools.append(
            replace(
                tool,
                server_name=config.name,
                public_name=public_name,
                parameters=dict(tool.parameters),
                kind=kind,
            )
        )

    return tuple(tools), tuple(diagnostics)


def _tool_diagnostic(server_name: str, reason: str) -> MCPDiagnostic:
    return MCPDiagnostic(
        server_name=server_name,
        category="tool_definition",
        message=f"MCP tool skipped for server {server_name}: {reason}",
    )


def _tool_failure(public_name: str, category: str) -> ToolResult:
    return ToolResult(
        ok=False,
        tool_name=public_name,
        content={"category": category},
        error=f"MCP tool call failed: {category}",
    )


def _default_connection_factory(config: MCPServerConfig) -> MCPConnection:
    if config.transport is MCPTransportKind.STDIO:
        transport = StdioTransport(config)
    else:
        transport = StreamableHTTPTransport(config)
    return MCPConnection(
        transport,
        server_name=config.name,
        timeout_seconds=config.timeout_seconds,
    )
