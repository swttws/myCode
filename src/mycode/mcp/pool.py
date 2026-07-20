from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field, replace
from typing import Callable

from mycode.mcp.connection import MCPConnection, MCPConnectionError, MCPRemoteError
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
ToolListListener = Callable[[str, tuple[RemoteTool, ...]], None]


@dataclass
class _ServerEntry:
    config: MCPServerConfig
    # 锁只保护单个 server，同一池中的其他 server 仍可并行连接和调用。
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
        self._tool_listeners: list[ToolListListener] = []

    @property
    def tools(self) -> tuple[RemoteTool, ...]:
        tools = [tool for entry in self._entries.values() for tool in entry.tools]
        return tuple(sorted(tools, key=lambda tool: tool.public_name))

    @property
    def server_names(self) -> tuple[str, ...]:
        return tuple(self._entries)

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

    def add_tools_listener(self, listener: ToolListListener) -> None:
        self._tool_listeners.append(listener)

    async def initialize_all(self) -> tuple[MCPDiagnostic, ...]:
        # 各 server 并行初始化并各自返回诊断，单点失败不会阻断可用 server。
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
        if not self.has_tool(server_name, remote_name):
            return _tool_failure(public_name, "tool_unavailable")

        connection = entry.connection
        if connection is None:
            return _tool_failure(public_name, "server_unavailable")
        try:
            result = await connection.request(
                "tools/call",
                {"name": remote_name, "arguments": dict(arguments)},
            )
        except MCPRemoteError:
            # 合法的 JSON-RPC 远端错误只代表本次调用失败，连接本身仍可继续复用。
            return _tool_failure(public_name, "remote_error")
        except MCPConnectionError as exc:
            # 传输或协议错误会污染连接状态，清空工具快照并等待后续惰性重连。
            await self._mark_failed(entry, connection)
            return _tool_failure(public_name, exc.category)
        except Exception:
            await self._mark_failed(entry, connection)
            return _tool_failure(public_name, "call_failed")

        if not _is_valid_call_tool_result(result):
            await self._mark_failed(entry, connection)
            return _tool_failure(public_name, "invalid_response")
        is_error = result.get("isError", False)
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

            # 工具搜索或调用可触发失败 server 的惰性重连；锁保证只创建一个新连接。
            old_connection = entry.connection
            if old_connection is not None and entry.state is not MCPServerState.FAILED:
                await old_connection.close()
            diagnostics = await self._connect_entry(entry)
            if diagnostics:
                self._diagnostics += diagnostics
            return entry.state is MCPServerState.READY

    def has_tool(self, server_name: str, remote_name: str) -> bool:
        entry = self._entries.get(server_name)
        return (
            entry is not None
            and entry.state is MCPServerState.READY
            and any(tool.remote_name == remote_name for tool in entry.tools)
        )

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
                    transport=entry.config.transport,
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
                    transport=entry.config.transport,
                ),
            )

        entry.tools, diagnostics = _normalize_tools(entry.config, discovered)
        entry.state = MCPServerState.READY
        # READY 后再同步注册中心，监听器即使失败也只产生诊断，不回滚连接。
        listener_diagnostics = self._notify_tools_changed(entry)
        return diagnostics + listener_diagnostics

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

    def _notify_tools_changed(self, entry: _ServerEntry) -> tuple[MCPDiagnostic, ...]:
        diagnostics: list[MCPDiagnostic] = []
        for listener in self._tool_listeners:
            try:
                listener(entry.config.name, entry.tools)
            except Exception:
                diagnostics.append(
                    MCPDiagnostic(
                        server_name=entry.config.name,
                        category="tool_registry",
                        message=f"MCP tool registry refresh failed: {entry.config.name}",
                        transport=entry.config.transport,
                    )
                )
        return tuple(diagnostics)


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
            diagnostics.append(_tool_diagnostic(config, "incompatible tool name"))
            continue
        if public_name in public_names:
            diagnostics.append(_tool_diagnostic(config, "duplicate tool name"))
            continue

        public_names.add(public_name)
        # 读权限必须由本地配置按远端原名显式授予，未声明工具一律保守视为写操作。
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


def _tool_diagnostic(config: MCPServerConfig, reason: str) -> MCPDiagnostic:
    return MCPDiagnostic(
        server_name=config.name,
        category="tool_definition",
        message=f"MCP tool skipped for server {config.name}: {reason}",
        transport=config.transport,
    )


def _tool_failure(public_name: str, category: str) -> ToolResult:
    return ToolResult(
        ok=False,
        tool_name=public_name,
        content={"category": category},
        error=f"MCP tool call failed: {category}",
    )


def _is_valid_call_tool_result(result: dict[str, object]) -> bool:
    content = result.get("content")
    if not isinstance(content, list):
        return False
    for item in content:
        if not isinstance(item, dict):
            return False
        item_type = item.get("type")
        if not isinstance(item_type, str) or not item_type:
            return False
    structured_content = result.get("structuredContent")
    if structured_content is not None and not isinstance(structured_content, dict):
        return False
    is_error = result.get("isError", False)
    return isinstance(is_error, bool)


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
