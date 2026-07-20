from __future__ import annotations

import asyncio

import httpx
import pytest

from mycode.mcp import MCPServerConfig, MCPTransportKind
from mycode.mcp.connection import MCPConnection
from mycode.mcp.streamable_http import StreamableHTTPTransport
from mycode.mcp.transport import MCPTransportError
from tests.mcp_helpers import run_http_server
from tests.helpers import ControlledAsyncByteStream


def make_config(url: str) -> MCPServerConfig:
    return MCPServerConfig(
        name="http_test",
        transport=MCPTransportKind.STREAMABLE_HTTP,
        timeout_seconds=1.0,
        command=None,
        args=(),
        env={},
        url=url,
        headers={"Authorization": "Bearer configured-secret"},
        read_tools=frozenset(),
    )


def test_streamable_http_posts_json_and_reuses_session_header():
    async def scenario(server):
        transport = StreamableHTTPTransport(make_config(server.url), enable_get_stream=False)
        await transport.open()
        try:
            await transport.send({"jsonrpc": "2.0", "id": 1, "method": "initialize"})
            initialize_response = await anext(transport.receive())
            assert initialize_response["id"] == 1
            assert transport.session_id == "session-123"

            await transport.send({"jsonrpc": "2.0", "id": 2, "method": "test/json"})
            assert (await anext(transport.receive()))["id"] == 2
        finally:
            await transport.close()

        first = server.requests.get_nowait()
        second = server.requests.get_nowait()
        assert first["headers"]["Authorization"] == "Bearer configured-secret"
        assert first["headers"]["MCP-Protocol-Version"] == "2025-11-25"
        assert "MCP-Session-Id" not in first["headers"]
        assert second["headers"]["MCP-Session-Id"] == "session-123"
        assert second["headers"]["Content-Type"] == "application/json"
        assert transport.is_open is False

    with run_http_server() as server:
        asyncio.run(scenario(server))


def test_streamable_http_parses_multiple_sse_events_in_order():
    async def scenario(server):
        transport = StreamableHTTPTransport(make_config(server.url), enable_get_stream=False)
        await transport.open()
        try:
            await transport.send({"jsonrpc": "2.0", "id": 3, "method": "test/sse"})
            response = await anext(transport.receive())
            notification = await anext(transport.receive())
            assert response["id"] == 3
            assert notification["method"] == "notifications/tools/list_changed"
        finally:
            await transport.close()

    with run_http_server() as server:
        asyncio.run(scenario(server))


def test_streamable_http_accepts_notification_with_202():
    async def scenario(server):
        transport = StreamableHTTPTransport(make_config(server.url), enable_get_stream=False)
        await transport.open()
        try:
            await transport.send(
                {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}}
            )
            assert transport.pending_message_count == 0
        finally:
            await transport.close()

    with run_http_server() as server:
        asyncio.run(scenario(server))


def test_streamable_http_optional_get_stream_enqueues_server_message():
    async def scenario(server):
        transport = StreamableHTTPTransport(make_config(server.url), enable_get_stream=True)
        await transport.open()
        try:
            transport.start_event_stream()
            message = await asyncio.wait_for(anext(transport.receive()), timeout=1)
            assert message["method"] == "notifications/progress"
        finally:
            await transport.close()

        record = server.requests.get_nowait()
        assert record["method"] == "GET"
        assert record["headers"]["Accept"] == "text/event-stream"

    with run_http_server() as server:
        asyncio.run(scenario(server))


@pytest.mark.parametrize(
    ("method", "category"),
    [
        ("test/http_error", "http_error"),
        ("test/invalid_content_type", "invalid_content_type"),
    ],
)
def test_streamable_http_returns_stable_errors_without_response_body(method, category):
    async def scenario(server):
        transport = StreamableHTTPTransport(make_config(server.url), enable_get_stream=False)
        await transport.open()
        try:
            with pytest.raises(MCPTransportError) as captured:
                await transport.send({"jsonrpc": "2.0", "id": 4, "method": method})
            assert captured.value.category == category
            assert "sensitive-response-body" not in str(captured.value)
        finally:
            await transport.close()

    with run_http_server() as server:
        asyncio.run(scenario(server))


def test_streamable_http_rejects_send_before_open():
    transport = StreamableHTTPTransport(
        make_config("http://127.0.0.1:1/mcp"), enable_get_stream=False
    )

    with pytest.raises(MCPTransportError, match="not open"):
        asyncio.run(transport.send({"jsonrpc": "2.0", "method": "ping"}))


def test_streamable_http_wraps_connection_failure_as_stable_transport_error():
    async def scenario():
        transport = StreamableHTTPTransport(
            make_config("http://127.0.0.1:1/mcp"), enable_get_stream=False
        )
        await transport.open()
        try:
            with pytest.raises(MCPTransportError) as captured:
                await transport.send({"jsonrpc": "2.0", "id": 5, "method": "test/json"})
            assert captured.value.category == "disconnected"
        finally:
            await transport.close()

    asyncio.run(scenario())


def test_connection_starts_optional_get_stream_only_after_session_is_established():
    async def scenario(server):
        server.require_session_for_get = True
        server.initialize_delay_seconds = 0.05
        transport = StreamableHTTPTransport(make_config(server.url), enable_get_stream=True)
        connection = MCPConnection(transport, server_name="http_test", timeout_seconds=1)
        try:
            assert await connection.initialize() == ()
            for _ in range(50):
                if server.requests.qsize() >= 4:
                    break
                await asyncio.sleep(0.01)
            records = []
            while not server.requests.empty():
                records.append(server.requests.get_nowait())
            get_record = next(record for record in records if record["method"] == "GET")
            assert get_record["headers"]["MCP-Session-Id"] == "session-123"
        finally:
            await connection.close()

    with run_http_server() as server:
        asyncio.run(scenario(server))


def test_streamable_http_post_sse_returns_without_waiting_for_stream_eof():
    async def scenario():
        stream = ControlledAsyncByteStream(
            b'data: {"jsonrpc":"2.0","id":77,"result":{"ok":true}}\n\n',
            [],
        )

        async def handler(request):
            return httpx.Response(
                200,
                headers={"Content-Type": "text/event-stream"},
                stream=stream,
            )

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        transport = StreamableHTTPTransport(
            make_config("https://example.invalid/mcp"),
            enable_get_stream=False,
            client=client,
        )
        await transport.open()
        try:
            await asyncio.wait_for(
                transport.send({"jsonrpc": "2.0", "id": 77, "method": "test/sse_open"}),
                timeout=0.2,
            )
            message = await asyncio.wait_for(anext(transport.receive()), timeout=0.2)
            assert message["id"] == 77
        finally:
            await transport.close()
            await client.aclose()

    asyncio.run(scenario())


def test_streamable_http_internal_headers_override_case_insensitive_config_values():
    async def scenario(server):
        base = make_config(server.url)
        config = MCPServerConfig(
            name=base.name,
            transport=base.transport,
            timeout_seconds=base.timeout_seconds,
            command=None,
            args=(),
            env={},
            url=base.url,
            headers={
                "authorization": "Bearer configured-secret",
                "accept": "text/plain",
                "content-type": "text/plain",
                "mcp-protocol-version": "old-version",
                "mcp-session-id": "configured-session",
            },
            read_tools=frozenset(),
        )
        transport = StreamableHTTPTransport(config, enable_get_stream=False)
        await transport.open()
        try:
            await transport.send({"jsonrpc": "2.0", "id": 90, "method": "initialize"})
            await anext(transport.receive())
            await transport.send({"jsonrpc": "2.0", "id": 91, "method": "test/json"})
            await anext(transport.receive())
        finally:
            await transport.close()

        first = server.requests.get_nowait()
        second = server.requests.get_nowait()
        assert first["header_values"]["Accept"] == [
            "application/json, text/event-stream"
        ]
        assert first["header_values"]["Content-Type"] == ["application/json"]
        assert first["header_values"]["MCP-Protocol-Version"] == ["2025-11-25"]
        assert first["header_values"]["MCP-Session-Id"] == []
        assert second["header_values"]["MCP-Session-Id"] == ["session-123"]

    with run_http_server() as server:
        asyncio.run(scenario(server))
