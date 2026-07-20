from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping

from mycode.mcp.jsonrpc import (
    JSONRPCMessageKind,
    MCPProtocolError,
    ParsedJSONRPCMessage,
    make_notification,
    make_request,
    parse_message,
)
from mycode.mcp.models import RemoteTool
from mycode.mcp.transport import MCPTransport, MCPTransportError
from mycode.tool import ToolKind


logger = logging.getLogger(__name__)
SUPPORTED_PROTOCOL_VERSION = "2025-11-25"


class MCPConnectionError(RuntimeError):
    def __init__(self, category: str, message: str) -> None:
        super().__init__(message)
        self.category = category


class MCPRemoteError(MCPConnectionError):
    def __init__(self, code: int, message: str) -> None:
        super().__init__("remote_error", message)
        self.code = code


class MCPConnection:
    def __init__(
        self,
        transport: MCPTransport,
        *,
        server_name: str,
        timeout_seconds: float,
    ) -> None:
        self._transport = transport
        self._server_name = server_name
        self._timeout_seconds = timeout_seconds
        self._next_request_id = 1
        self._pending: dict[int | str, asyncio.Future[dict[str, object]]] = {}
        self._receiver_task: asyncio.Task[None] | None = None
        self._initialize_lock = asyncio.Lock()
        self._close_lock = asyncio.Lock()
        self._initialized = False
        self._closed = False
        self._tools: tuple[RemoteTool, ...] = ()
        self.protocol_version: str | None = None
        self.capabilities: dict[str, object] = {}
        self.server_info: dict[str, object] = {}

    @property
    def is_initialized(self) -> bool:
        return self._initialized

    @property
    def pending_request_count(self) -> int:
        return len(self._pending)

    async def initialize(self) -> tuple[RemoteTool, ...]:
        async with self._initialize_lock:
            if self._initialized:
                return self._tools
            if self._closed:
                raise MCPConnectionError("closed", "MCP connection is closed")

            await self._transport.open()
            self._receiver_task = asyncio.create_task(
                self._receive_loop(),
                name=f"mcp-receive-{self._server_name}",
            )
            result = await self.request(
                "initialize",
                {
                    "protocolVersion": SUPPORTED_PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": {"name": "myCode", "version": "0.1.0"},
                },
            )
            self._apply_initialization_result(result)
            await self.notify("notifications/initialized", {})
            tool_result = await self.request("tools/list", {})
            self._tools = self._parse_tools(tool_result)
            self._initialized = True
            return self._tools

    async def request(
        self,
        method: str,
        params: Mapping[str, object],
    ) -> dict[str, object]:
        if self._closed:
            raise MCPConnectionError("closed", "MCP connection is closed")
        request_id = self._next_request_id
        self._next_request_id += 1
        loop = asyncio.get_running_loop()
        future: asyncio.Future[dict[str, object]] = loop.create_future()
        self._pending[request_id] = future
        try:
            await self._transport.send(make_request(request_id, method, params))
        except (MCPTransportError, OSError) as exc:
            self._pending.pop(request_id, None)
            raise MCPConnectionError(
                "send_failed",
                f"unable to send MCP request: {self._server_name}",
            ) from exc

        try:
            return await asyncio.wait_for(
                asyncio.shield(future),
                timeout=self._timeout_seconds,
            )
        except asyncio.TimeoutError as exc:
            self._pending.pop(request_id, None)
            raise MCPConnectionError(
                "timeout",
                f"MCP request timed out: {self._server_name}",
            ) from exc

    async def notify(self, method: str, params: Mapping[str, object]) -> None:
        if self._closed:
            raise MCPConnectionError("closed", "MCP connection is closed")
        try:
            await self._transport.send(make_notification(method, params))
        except (MCPTransportError, OSError) as exc:
            raise MCPConnectionError(
                "send_failed",
                f"unable to send MCP notification: {self._server_name}",
            ) from exc

    async def close(self) -> None:
        async with self._close_lock:
            if self._closed:
                return
            self._closed = True
            self._initialized = False
            self._fail_pending(
                MCPConnectionError("closed", f"MCP connection closed: {self._server_name}")
            )
            receiver_task = self._receiver_task
            if receiver_task is not None and not receiver_task.done():
                receiver_task.cancel()
            if receiver_task is not None:
                await asyncio.gather(receiver_task, return_exceptions=True)
            self._receiver_task = None
            await self._transport.close()

    async def _receive_loop(self) -> None:
        try:
            async for raw_message in self._transport.receive():
                message = parse_message(raw_message)
                if message.kind is JSONRPCMessageKind.RESPONSE:
                    self._dispatch_response(message)
                elif message.kind is JSONRPCMessageKind.NOTIFICATION:
                    logger.info(
                        "收到 MCP server 通知：%s，server=%s",
                        message.method,
                        self._server_name,
                    )
                else:
                    logger.info(
                        "收到 MCP server 请求：%s，server=%s",
                        message.method,
                        self._server_name,
                    )
        except asyncio.CancelledError:
            raise
        except (MCPTransportError, MCPProtocolError) as exc:
            self._fail_pending(
                MCPConnectionError(
                    "receive_failed",
                    f"MCP receive loop failed: {self._server_name}",
                )
            )
            logger.warning("MCP 接收循环失败：%s，类别：%s", self._server_name, exc.category)
        except Exception:
            self._fail_pending(
                MCPConnectionError(
                    "receive_failed",
                    f"MCP receive loop failed: {self._server_name}",
                )
            )
            logger.exception("MCP 接收循环异常：%s", self._server_name)

    def _dispatch_response(self, message: ParsedJSONRPCMessage) -> None:
        if message.id is None:
            return
        future = self._pending.pop(message.id, None)
        if future is None or future.done():
            logger.debug("忽略未知或迟到的 MCP 响应：server=%s", self._server_name)
            return
        if message.error is not None:
            future.set_exception(MCPRemoteError(message.error.code, message.error.message))
            return
        if not isinstance(message.result, dict):
            future.set_exception(
                MCPConnectionError(
                    "invalid_response",
                    f"MCP response result must be an object: {self._server_name}",
                )
            )
            return
        future.set_result(dict(message.result))

    def _apply_initialization_result(self, result: Mapping[str, object]) -> None:
        version = result.get("protocolVersion")
        if version != SUPPORTED_PROTOCOL_VERSION:
            raise MCPConnectionError(
                "unsupported_version",
                f"MCP server selected an unsupported protocol version: {self._server_name}",
            )
        capabilities = result.get("capabilities")
        if not isinstance(capabilities, dict):
            raise MCPConnectionError(
                "invalid_initialize",
                f"MCP server capabilities are invalid: {self._server_name}",
            )
        server_info = result.get("serverInfo", {})
        if not isinstance(server_info, dict):
            raise MCPConnectionError(
                "invalid_initialize",
                f"MCP server info is invalid: {self._server_name}",
            )

        self.protocol_version = version
        self.capabilities = dict(capabilities)
        self.server_info = dict(server_info)
        set_protocol_version = getattr(self._transport, "set_protocol_version", None)
        if callable(set_protocol_version):
            set_protocol_version(version)

    def _parse_tools(self, result: Mapping[str, object]) -> tuple[RemoteTool, ...]:
        raw_tools = result.get("tools")
        if not isinstance(raw_tools, list):
            raise MCPConnectionError(
                "invalid_tool_list",
                f"MCP tool list is invalid: {self._server_name}",
            )

        tools: list[RemoteTool] = []
        for raw_tool in raw_tools:
            if not isinstance(raw_tool, dict):
                raise self._invalid_tool_list()
            name = raw_tool.get("name")
            description = raw_tool.get("description", "")
            parameters = raw_tool.get("inputSchema")
            if (
                not isinstance(name, str)
                or not name
                or not isinstance(description, str)
                or not isinstance(parameters, dict)
            ):
                raise self._invalid_tool_list()
            tools.append(
                RemoteTool(
                    server_name=self._server_name,
                    remote_name=name,
                    public_name=f"{self._server_name}__{name}",
                    description=description,
                    parameters=dict(parameters),
                    kind=ToolKind.WRITE,
                )
            )
        return tuple(tools)

    def _invalid_tool_list(self) -> MCPConnectionError:
        return MCPConnectionError(
            "invalid_tool_list",
            f"MCP tool list is invalid: {self._server_name}",
        )

    def _fail_pending(self, error: MCPConnectionError) -> None:
        pending = tuple(self._pending.values())
        self._pending.clear()
        for future in pending:
            if not future.done():
                future.set_exception(error)
