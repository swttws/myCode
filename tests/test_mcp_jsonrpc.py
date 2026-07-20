from __future__ import annotations

import asyncio

import pytest

from mycode.mcp.connection import MCPConnection, MCPConnectionError, MCPRemoteError
from mycode.mcp.jsonrpc import (
    JSONRPCError,
    JSONRPCMessageKind,
    MCPProtocolError,
    make_cancel_notification,
    make_error_response,
    make_notification,
    make_request,
    make_success_response,
    parse_message,
)
from mycode.tool import ToolKind
from tests.mcp_helpers import MemoryMCPTransport
from mycode.mcp.transport import MCPTransportError


def test_builds_jsonrpc_request_and_notification():
    assert make_request(3, "tools/list", {"cursor": "next"}) == {
        "jsonrpc": "2.0",
        "id": 3,
        "method": "tools/list",
        "params": {"cursor": "next"},
    }
    assert make_notification("notifications/initialized", {}) == {
        "jsonrpc": "2.0",
        "method": "notifications/initialized",
        "params": {},
    }


def test_builds_success_error_and_cancel_messages():
    assert make_success_response("ping-1", {}) == {
        "jsonrpc": "2.0",
        "id": "ping-1",
        "result": {},
    }
    assert make_error_response("request-1", -32601, "Method not found") == {
        "jsonrpc": "2.0",
        "id": "request-1",
        "error": {"code": -32601, "message": "Method not found"},
    }
    assert make_cancel_notification(9, reason="timeout") == {
        "jsonrpc": "2.0",
        "method": "notifications/cancelled",
        "params": {"requestId": 9, "reason": "timeout"},
    }


def test_parse_success_response_preserves_id_and_result():
    parsed = parse_message({"jsonrpc": "2.0", "id": 7, "result": {"tools": []}})

    assert parsed.kind is JSONRPCMessageKind.RESPONSE
    assert parsed.id == 7
    assert parsed.result == {"tools": []}
    assert parsed.error is None


def test_parse_error_response_returns_structured_error():
    parsed = parse_message(
        {
            "jsonrpc": "2.0",
            "id": "call-1",
            "error": {"code": -32000, "message": "failed", "data": {"retry": False}},
        }
    )

    assert parsed.kind is JSONRPCMessageKind.RESPONSE
    assert parsed.id == "call-1"
    assert parsed.error == JSONRPCError(-32000, "failed", {"retry": False})


def test_parse_notification_and_server_request():
    notification = parse_message(
        {"jsonrpc": "2.0", "method": "notifications/tools/list_changed", "params": {}}
    )
    request = parse_message({"jsonrpc": "2.0", "id": 4, "method": "ping"})

    assert notification.kind is JSONRPCMessageKind.NOTIFICATION
    assert notification.method == "notifications/tools/list_changed"
    assert request.kind is JSONRPCMessageKind.REQUEST
    assert request.id == 4
    assert request.params == {}


@pytest.mark.parametrize(
    ("message", "category"),
    [
        ([], "not_object"),
        ({"jsonrpc": "1.0", "id": 1, "result": {}}, "invalid_version"),
        ({"jsonrpc": "2.0", "id": 1, "result": {}, "error": {}}, "invalid_response"),
        ({"jsonrpc": "2.0", "result": {}}, "missing_id"),
        ({"jsonrpc": "2.0", "id": 1}, "invalid_message"),
        ({"jsonrpc": "2.0", "method": ""}, "invalid_method"),
        ({"jsonrpc": "2.0", "id": True, "method": "ping"}, "invalid_id"),
        (
            {"jsonrpc": "2.0", "id": 1, "error": {"code": "bad", "message": "failed"}},
            "invalid_error",
        ),
        (
            {"jsonrpc": "2.0", "id": 1, "error": {"code": -1}},
            "invalid_error",
        ),
        ({"jsonrpc": "2.0", "method": "ping", "params": []}, "invalid_params"),
    ],
)
def test_parse_rejects_invalid_messages_without_echoing_payload(message, category):
    secret = "do-not-leak"
    if isinstance(message, dict):
        message["secret"] = secret

    with pytest.raises(MCPProtocolError) as captured:
        parse_message(message)

    assert captured.value.category == category
    assert secret not in str(captured.value)


@pytest.mark.parametrize("method", ["", None, 42])
def test_message_builders_reject_invalid_methods(method):
    with pytest.raises(ValueError, match="method"):
        make_request(1, method)


def test_error_builder_rejects_boolean_error_code():
    with pytest.raises(ValueError, match="error code"):
        make_error_response(1, True, "bad")


async def initialize_connection(connection: MCPConnection, transport: MemoryMCPTransport):
    initialization = asyncio.create_task(connection.initialize())
    initialize_request = await transport.next_sent()
    assert initialize_request["method"] == "initialize"
    await transport.push(
        {
            "jsonrpc": "2.0",
            "id": initialize_request["id"],
            "result": {
                "protocolVersion": "2025-11-25",
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "memory", "version": "1"},
            },
        }
    )
    initialized_notification = await transport.next_sent()
    assert initialized_notification == {
        "jsonrpc": "2.0",
        "method": "notifications/initialized",
        "params": {},
    }
    list_request = await transport.next_sent()
    assert list_request["method"] == "tools/list"
    await transport.push(
        {
            "jsonrpc": "2.0",
            "id": list_request["id"],
            "result": {
                "tools": [
                    {
                        "name": "echo",
                        "description": "Echo arguments.",
                        "inputSchema": {
                            "type": "object",
                            "properties": {"value": {"type": "string"}},
                        },
                    }
                ]
            },
        }
    )
    return await initialization


def test_connection_initializes_in_protocol_order_and_discovers_tools():
    async def scenario():
        transport = MemoryMCPTransport()
        connection = MCPConnection(transport, server_name="memory", timeout_seconds=0.5)
        try:
            tools = await initialize_connection(connection, transport)

            assert connection.protocol_version == "2025-11-25"
            assert transport.protocol_version == "2025-11-25"
            assert connection.capabilities == {"tools": {"listChanged": False}}
            assert connection.is_initialized is True
            assert len(tools) == 1
            assert tools[0].remote_name == "echo"
            assert tools[0].public_name == "memory__echo"
            assert tools[0].kind is ToolKind.WRITE
        finally:
            await connection.close()

        assert transport.open_count == 1
        assert transport.close_count == 1

    asyncio.run(scenario())


def test_connection_matches_out_of_order_success_and_error_responses_by_id():
    async def scenario():
        transport = MemoryMCPTransport()
        connection = MCPConnection(transport, server_name="memory", timeout_seconds=0.5)
        try:
            await initialize_connection(connection, transport)
            first_task = asyncio.create_task(connection.request("first", {"value": 1}))
            second_task = asyncio.create_task(connection.request("second", {"value": 2}))
            first_request = await transport.next_sent()
            second_request = await transport.next_sent()

            await transport.push(
                {
                    "jsonrpc": "2.0",
                    "id": second_request["id"],
                    "error": {"code": -32001, "message": "second failed"},
                }
            )
            await transport.push(
                {
                    "jsonrpc": "2.0",
                    "id": first_request["id"],
                    "result": {"request": "first"},
                }
            )

            assert await first_task == {"request": "first"}
            with pytest.raises(MCPRemoteError) as captured:
                await second_task
            assert captured.value.code == -32001
            assert connection.pending_request_count == 0
        finally:
            await connection.close()

    asyncio.run(scenario())


def test_connection_rejects_incompatible_protocol_version():
    async def scenario():
        transport = MemoryMCPTransport()
        connection = MCPConnection(transport, server_name="memory", timeout_seconds=0.5)
        initialization = asyncio.create_task(connection.initialize())
        request = await transport.next_sent()
        await transport.push(
            {
                "jsonrpc": "2.0",
                "id": request["id"],
                "result": {"protocolVersion": "1900-01-01", "capabilities": {}},
            }
        )
        try:
            with pytest.raises(MCPConnectionError) as captured:
                await initialization
            assert captured.value.category == "unsupported_version"
        finally:
            await connection.close()

    asyncio.run(scenario())


def test_connection_rejects_invalid_tool_list():
    async def scenario():
        transport = MemoryMCPTransport()
        connection = MCPConnection(transport, server_name="memory", timeout_seconds=0.5)
        initialization = asyncio.create_task(connection.initialize())
        initialize_request = await transport.next_sent()
        await transport.push(
            {
                "jsonrpc": "2.0",
                "id": initialize_request["id"],
                "result": {"protocolVersion": "2025-11-25", "capabilities": {}},
            }
        )
        await transport.next_sent()
        list_request = await transport.next_sent()
        await transport.push(
            {"jsonrpc": "2.0", "id": list_request["id"], "result": {"tools": "bad"}}
        )
        try:
            with pytest.raises(MCPConnectionError) as captured:
                await initialization
            assert captured.value.category == "invalid_tool_list"
        finally:
            await connection.close()

    asyncio.run(scenario())


def test_connection_notification_does_not_complete_pending_request():
    async def scenario():
        transport = MemoryMCPTransport()
        connection = MCPConnection(transport, server_name="memory", timeout_seconds=0.5)
        try:
            await initialize_connection(connection, transport)
            task = asyncio.create_task(connection.request("waiting", {}))
            request = await transport.next_sent()
            await transport.push(
                {"jsonrpc": "2.0", "method": "notifications/progress", "params": {"value": 1}}
            )
            await asyncio.sleep(0)
            assert task.done() is False
            await transport.push(
                {"jsonrpc": "2.0", "id": request["id"], "result": {"done": True}}
            )
            assert await task == {"done": True}
        finally:
            await connection.close()

    asyncio.run(scenario())


def test_connection_responds_to_ping_and_rejects_unsupported_server_request():
    async def scenario():
        transport = MemoryMCPTransport()
        connection = MCPConnection(transport, server_name="memory", timeout_seconds=0.5)
        try:
            await initialize_connection(connection, transport)
            await transport.push({"jsonrpc": "2.0", "id": "ping-1", "method": "ping"})
            assert await transport.next_sent() == {
                "jsonrpc": "2.0",
                "id": "ping-1",
                "result": {},
            }

            await transport.push(
                {"jsonrpc": "2.0", "id": "roots-1", "method": "roots/list", "params": {}}
            )
            assert await transport.next_sent() == {
                "jsonrpc": "2.0",
                "id": "roots-1",
                "error": {"code": -32601, "message": "Method not found"},
            }
        finally:
            await connection.close()

    asyncio.run(scenario())


def test_connection_timeout_sends_cancel_and_ignores_late_response():
    async def scenario():
        transport = MemoryMCPTransport()
        connection = MCPConnection(transport, server_name="memory", timeout_seconds=0.02)
        try:
            await initialize_connection(connection, transport)
            timed_out = asyncio.create_task(connection.request("slow", {}))
            slow_request = await transport.next_sent()
            with pytest.raises(MCPConnectionError) as captured:
                await timed_out
            assert captured.value.category == "timeout"
            assert await transport.next_sent() == {
                "jsonrpc": "2.0",
                "method": "notifications/cancelled",
                "params": {"requestId": slow_request["id"], "reason": "timeout"},
            }
            assert connection.pending_request_count == 0

            await transport.push(
                {"jsonrpc": "2.0", "id": slow_request["id"], "result": {"late": True}}
            )
            next_task = asyncio.create_task(connection.request("next", {}))
            next_request = await transport.next_sent()
            await transport.push(
                {"jsonrpc": "2.0", "id": next_request["id"], "result": {"next": True}}
            )
            assert await next_task == {"next": True}
        finally:
            await connection.close()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "failure",
    [
        MCPTransportError("disconnected", "transport disconnected"),
        {"jsonrpc": "1.0", "id": 99, "result": {}},
    ],
)
def test_connection_failure_finishes_all_pending_and_closes_transport_once(failure):
    async def scenario():
        transport = MemoryMCPTransport()
        connection = MCPConnection(transport, server_name="memory", timeout_seconds=0.5)
        await initialize_connection(connection, transport)
        first = asyncio.create_task(connection.request("first", {}))
        second = asyncio.create_task(connection.request("second", {}))
        await transport.next_sent()
        await transport.next_sent()

        await transport.push(failure)
        for task in (first, second):
            with pytest.raises(MCPConnectionError) as captured:
                await task
            assert captured.value.category == "receive_failed"

        await asyncio.sleep(0)
        assert connection.is_failed is True
        assert connection.pending_request_count == 0
        assert transport.close_count == 1
        await connection.close()
        assert transport.close_count == 1

    asyncio.run(scenario())


def test_connection_rejects_tool_call_before_initialization():
    async def scenario():
        transport = MemoryMCPTransport()
        connection = MCPConnection(transport, server_name="memory", timeout_seconds=0.5)
        with pytest.raises(MCPConnectionError) as captured:
            await connection.request("tools/call", {"name": "echo", "arguments": {}})
        assert captured.value.category == "not_initialized"

    asyncio.run(scenario())
