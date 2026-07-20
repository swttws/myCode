from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Mapping

from mycode.mcp.jsonrpc import (
    JSONRPCMessageKind,
    MCPProtocolError,
    ParsedJSONRPCMessage,
    make_cancel_notification,
    make_error_response,
    make_notification,
    make_request,
    make_success_response,
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
        # 接收循环按 JSON-RPC id 找回等待中的 Future，使多个并发请求可复用同一连接。
        self._pending: dict[int | str, asyncio.Future[dict[str, object]]] = {}
        self._receiver_task: asyncio.Task[None] | None = None
        self._initialize_lock = asyncio.Lock()
        self._close_lock = asyncio.Lock()
        self._initialized = False
        self._closed = False
        self._failed = False
        self._transport_closed = False
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

    @property
    def is_failed(self) -> bool:
        return self._failed

    async def initialize(self) -> tuple[RemoteTool, ...]:
        async with self._initialize_lock:
            if self._initialized:
                return self._tools
            if self._closed:
                raise MCPConnectionError("closed", "MCP connection is closed")
            if self._failed:
                raise MCPConnectionError("failed", "MCP connection has failed")

            await self._transport.open()
            self._transport_closed = False
            # 必须先启动接收循环，initialize 请求的响应才有消费者负责分发。
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
            # Streamable HTTP 的独立 GET 流要等握手完成后再启动，以携带协商后的版本和会话。
            start_event_stream = getattr(self._transport, "start_event_stream", None)
            if callable(start_event_stream):
                start_event_stream()
            self._tools = await self._list_tools()
            self._initialized = True
            return self._tools

    async def request(
        self,
        method: str,
        params: Mapping[str, object],
    ) -> dict[str, object]:
        if self._closed:
            raise MCPConnectionError("closed", "MCP connection is closed")
        if self._failed:
            raise MCPConnectionError("failed", "MCP connection has failed")
        if method == "tools/call" and not self._initialized:
            raise MCPConnectionError(
                "not_initialized",
                "MCP tools cannot be called before initialization",
            )
        request_id = self._next_request_id
        self._next_request_id += 1
        loop = asyncio.get_running_loop()
        future: asyncio.Future[dict[str, object]] = loop.create_future()
        self._pending[request_id] = future
        try:
            try:
                await self._transport.send(make_request(request_id, method, params))
            except (MCPTransportError, OSError) as exc:
                self._discard_pending(request_id, future)
                raise MCPConnectionError(
                    "send_failed",
                    f"unable to send MCP request: {self._server_name}",
                ) from exc

            try:
                # shield 防止 wait_for 直接取消 Future；超时分支会统一清理并通知 server。
                return await asyncio.wait_for(
                    asyncio.shield(future),
                    timeout=self._timeout_seconds,
                )
            except asyncio.TimeoutError as exc:
                await self._cancel_pending(request_id, future, reason="timeout")
                raise MCPConnectionError(
                    "timeout",
                    f"MCP request timed out: {self._server_name}",
                ) from exc
        except asyncio.CancelledError:
            await self._cancel_pending(request_id, future, reason="cancelled")
            raise

    async def notify(self, method: str, params: Mapping[str, object]) -> None:
        if self._closed:
            raise MCPConnectionError("closed", "MCP connection is closed")
        if self._failed:
            raise MCPConnectionError("failed", "MCP connection has failed")
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
            await self._close_transport()

    async def _receive_loop(self) -> None:
        try:
            # 所有传输都归一化为消息流，连接层只处理 JSON-RPC 语义。
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
                    await self._handle_server_request(message)
        except asyncio.CancelledError:
            raise
        except (MCPTransportError, MCPProtocolError) as exc:
            await self._fail_connection()
            logger.warning("MCP 接收循环失败：%s，类别：%s", self._server_name, exc.category)
        except Exception:
            await self._fail_connection()
            logger.warning("MCP 接收循环异常：%s", self._server_name)

    async def _handle_server_request(self, message: ParsedJSONRPCMessage) -> None:
        if message.id is None:
            return
        if message.method == "ping":
            response = make_success_response(message.id, {})
        else:
            # 当前客户端不实现 sampling 等反向能力，未知 server 请求按 JSON-RPC 规范拒绝。
            response = make_error_response(message.id, -32601, "Method not found")
        await self._transport.send(response)

    def _dispatch_response(self, message: ParsedJSONRPCMessage) -> None:
        if message.id is None:
            return
        future = self._pending.pop(message.id, None)
        if future is None or future.done():
            # 请求可能已超时或被取消；迟到响应不能重新激活已结束的调用。
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
                    # 远端工具默认按写工具处理，只有配置显式声明后才会降为 READ。
                    kind=ToolKind.WRITE,
                )
            )
        return tuple(tools)

    async def _list_tools(self) -> tuple[RemoteTool, ...]:
        tools: list[RemoteTool] = []
        cursor: str | None = None
        seen_cursors: set[str] = set()
        # tools/list 支持分页；记录 cursor 可阻止异常 server 制造无限循环。
        while True:
            params = {"cursor": cursor} if cursor is not None else {}
            result = await self.request("tools/list", params)
            tools.extend(self._parse_tools(result))
            if "nextCursor" not in result:
                return tuple(tools)
            next_cursor = result["nextCursor"]
            if (
                not isinstance(next_cursor, str)
                or not next_cursor
                or next_cursor in seen_cursors
            ):
                raise self._invalid_tool_list()
            seen_cursors.add(next_cursor)
            cursor = next_cursor

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

    def _discard_pending(
        self,
        request_id: int | str,
        future: asyncio.Future[dict[str, object]],
    ) -> None:
        self._pending.pop(request_id, None)
        if future.done() and not future.cancelled():
            future.exception()
        else:
            future.cancel()

    async def _cancel_pending(
        self,
        request_id: int | str,
        future: asyncio.Future[dict[str, object]],
        *,
        reason: str,
    ) -> None:
        self._discard_pending(request_id, future)
        # 本地取消已经生效，远端取消通知仅尽力发送，失败不能覆盖原始超时/取消原因。
        with contextlib.suppress(MCPTransportError, OSError, RuntimeError):
            await self._transport.send(make_cancel_notification(request_id, reason=reason))

    async def _fail_connection(self) -> None:
        # 接收链路失败后统一终止所有等待者，避免请求永久悬挂。
        self._failed = True
        self._initialized = False
        self._fail_pending(
            MCPConnectionError(
                "receive_failed",
                f"MCP receive loop failed: {self._server_name}",
            )
        )
        await self._close_transport()

    async def _close_transport(self) -> None:
        if self._transport_closed:
            return
        self._transport_closed = True
        await self._transport.close()
