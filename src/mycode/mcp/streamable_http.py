from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Mapping

import httpx

from mycode.mcp.models import MCPServerConfig, MCPTransportKind
from mycode.mcp.transport import MCPTransportError


DEFAULT_PROTOCOL_VERSION = "2025-11-25"


class StreamableHTTPTransport:
    def __init__(
        self,
        config: MCPServerConfig,
        *,
        enable_get_stream: bool = True,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if config.transport is not MCPTransportKind.STREAMABLE_HTTP:
            raise ValueError("StreamableHTTPTransport requires a streamable_http config")
        self._config = config
        self._enable_get_stream = enable_get_stream
        self._client = client
        self._owns_client = client is None
        self._messages: asyncio.Queue[dict[str, object] | MCPTransportError] = asyncio.Queue()
        self._get_task: asyncio.Task[None] | None = None
        self._protocol_version = DEFAULT_PROTOCOL_VERSION
        self._session_id: str | None = None
        self._opened = False
        self._close_lock = asyncio.Lock()

    @property
    def is_open(self) -> bool:
        return self._opened and self._client is not None and not self._client.is_closed

    @property
    def session_id(self) -> str | None:
        return self._session_id

    @property
    def pending_message_count(self) -> int:
        return self._messages.qsize()

    def set_protocol_version(self, version: str) -> None:
        if not isinstance(version, str) or not version:
            raise ValueError("MCP protocol version must be a non-empty string")
        self._protocol_version = version

    async def open(self) -> None:
        if self.is_open:
            raise MCPTransportError("already_open", "MCP HTTP transport is already open")
        if self._owns_client:
            self._client = httpx.AsyncClient(
                timeout=self._config.timeout_seconds,
                trust_env=False,
            )
        if self._client is None or self._client.is_closed:
            raise MCPTransportError("open_failed", "MCP HTTP client is unavailable")
        self._opened = True
        if self._enable_get_stream:
            self._get_task = asyncio.create_task(
                self._consume_get_stream(),
                name=f"mcp-http-get-{self._config.name}",
            )

    async def send(self, message: Mapping[str, object]) -> None:
        client = self._require_client()
        try:
            response = await client.post(
                self._require_url(),
                headers=self._headers(accept="application/json, text/event-stream"),
                json=dict(message),
            )
        except (httpx.HTTPError, OSError) as exc:
            raise MCPTransportError(
                "disconnected",
                f"MCP HTTP request failed: {self._config.name}",
            ) from exc

        self._update_session(response)
        if response.status_code in {202, 204}:
            return
        if response.is_error:
            raise MCPTransportError(
                "http_error",
                f"MCP HTTP server returned status {response.status_code}: {self._config.name}",
            )

        content_type = _content_type(response)
        if content_type == "application/json":
            await self._enqueue_json_response(response)
            return
        if content_type == "text/event-stream":
            await self._enqueue_sse_lines(response.aiter_lines())
            return
        raise MCPTransportError(
            "invalid_content_type",
            f"MCP HTTP server returned an unsupported content type: {self._config.name}",
        )

    async def receive(self) -> AsyncIterator[dict[str, object]]:
        self._require_client()
        while True:
            item = await self._messages.get()
            if isinstance(item, MCPTransportError):
                raise item
            yield item

    async def close(self) -> None:
        async with self._close_lock:
            if not self._opened and (self._client is None or self._client.is_closed):
                return
            self._opened = False
            get_task = self._get_task
            if get_task is not None and not get_task.done():
                get_task.cancel()
            if get_task is not None:
                await asyncio.gather(get_task, return_exceptions=True)
            self._get_task = None

            if self._client is not None and self._owns_client and not self._client.is_closed:
                await self._client.aclose()

    async def _consume_get_stream(self) -> None:
        client = self._require_client()
        try:
            async with client.stream(
                "GET",
                self._require_url(),
                headers=self._headers(accept="text/event-stream"),
            ) as response:
                self._update_session(response)
                if response.status_code in {404, 405}:
                    return
                if response.is_error:
                    raise MCPTransportError(
                        "http_error",
                        f"MCP GET event stream failed: {self._config.name}",
                    )
                if _content_type(response) != "text/event-stream":
                    raise MCPTransportError(
                        "invalid_content_type",
                        f"MCP GET response is not an event stream: {self._config.name}",
                    )
                await self._enqueue_sse_lines(response.aiter_lines())
        except asyncio.CancelledError:
            raise
        except MCPTransportError as exc:
            await self._messages.put(exc)
        except (httpx.HTTPError, OSError):
            await self._messages.put(
                MCPTransportError(
                    "disconnected",
                    f"MCP GET event stream disconnected: {self._config.name}",
                )
            )

    async def _enqueue_json_response(self, response: httpx.Response) -> None:
        try:
            message = response.json()
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise MCPTransportError(
                "invalid_json",
                f"MCP HTTP server returned invalid JSON: {self._config.name}",
            ) from exc
        await self._enqueue_message(message)

    async def _enqueue_sse_lines(self, lines: AsyncIterator[str]) -> None:
        async for data in _iter_sse_data(lines):
            try:
                message = json.loads(data)
            except json.JSONDecodeError as exc:
                raise MCPTransportError(
                    "invalid_json",
                    f"MCP HTTP event stream returned invalid JSON: {self._config.name}",
                ) from exc
            await self._enqueue_message(message)

    async def _enqueue_message(self, message: object) -> None:
        if not isinstance(message, dict):
            raise MCPTransportError(
                "invalid_json",
                f"MCP HTTP server returned a non-object message: {self._config.name}",
            )
        await self._messages.put(message)

    def _headers(self, *, accept: str) -> dict[str, str]:
        headers = dict(self._config.headers)
        headers["Accept"] = accept
        headers["MCP-Protocol-Version"] = self._protocol_version
        if self._session_id is not None:
            headers["MCP-Session-Id"] = self._session_id
        return headers

    def _update_session(self, response: httpx.Response) -> None:
        session_id = response.headers.get("MCP-Session-Id")
        if session_id:
            self._session_id = session_id

    def _require_client(self) -> httpx.AsyncClient:
        if not self.is_open or self._client is None:
            raise MCPTransportError("not_open", "MCP HTTP transport is not open")
        return self._client

    def _require_url(self) -> str:
        if self._config.url is None:
            raise MCPTransportError("open_failed", "MCP HTTP URL is missing")
        return self._config.url


def _content_type(response: httpx.Response) -> str:
    return response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()


async def _iter_sse_data(lines: AsyncIterator[str]) -> AsyncIterator[str]:
    data_lines: list[str] = []
    async for line in lines:
        if not line:
            if data_lines:
                yield "\n".join(data_lines)
                data_lines.clear()
            continue
        if line.startswith(":"):
            continue
        if line.startswith("data:"):
            data_lines.append(line[5:].lstrip(" "))
    if data_lines:
        yield "\n".join(data_lines)
