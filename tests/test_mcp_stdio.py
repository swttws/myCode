from __future__ import annotations

import asyncio
import logging

import pytest

from mycode.mcp import MCPServerConfig, MCPTransportKind
from mycode.mcp.stdio import StdioTransport
from mycode.mcp.transport import MCPTransportError
from tests.mcp_helpers import create_stdio_server


def make_config(tmp_path, **overrides):
    command, args = create_stdio_server(tmp_path)
    values = {
        "name": "stdio_test",
        "transport": MCPTransportKind.STDIO,
        "timeout_seconds": 1.0,
        "command": command,
        "args": args,
        "env": {"MCP_TEST_TOKEN": "configured-token"},
        "url": None,
        "headers": {},
        "read_tools": frozenset(),
    }
    values.update(overrides)
    return MCPServerConfig(**values)


def test_stdio_transport_sends_and_receives_line_delimited_json(tmp_path):
    async def scenario():
        transport = StdioTransport(make_config(tmp_path))
        await transport.open()
        try:
            await transport.send(
                {"jsonrpc": "2.0", "id": 1, "method": "echo", "params": {"value": "ok"}}
            )
            response = await anext(transport.receive())
            assert response == {
                "jsonrpc": "2.0",
                "id": 1,
                "result": {"params": {"value": "ok"}, "token": "configured-token"},
            }
        finally:
            await transport.close()

        assert transport.is_open is False

    asyncio.run(scenario())


def test_stdio_transport_consumes_stderr_without_logging_contents(tmp_path, caplog):
    async def scenario():
        transport = StdioTransport(make_config(tmp_path))
        await transport.open()
        try:
            await transport.send({"jsonrpc": "2.0", "id": 2, "method": "stderr"})
            assert (await anext(transport.receive()))["id"] == 2
            await asyncio.sleep(0.05)
        finally:
            await transport.close()

    with caplog.at_level(logging.DEBUG):
        asyncio.run(scenario())

    assert "sensitive-stderr-value" not in caplog.text


def test_stdio_transport_rejects_invalid_stdout_json(tmp_path):
    async def scenario():
        transport = StdioTransport(make_config(tmp_path))
        await transport.open()
        try:
            await transport.send({"jsonrpc": "2.0", "id": 3, "method": "invalid"})
            with pytest.raises(MCPTransportError) as captured:
                await anext(transport.receive())
            assert captured.value.category == "invalid_json"
            assert "not-json" not in str(captured.value)
        finally:
            await transport.close()

    asyncio.run(scenario())


def test_stdio_transport_reports_process_exit_as_disconnect(tmp_path):
    async def scenario():
        transport = StdioTransport(make_config(tmp_path))
        await transport.open()
        try:
            await transport.send({"jsonrpc": "2.0", "id": 4, "method": "exit"})
            with pytest.raises(MCPTransportError) as captured:
                await anext(transport.receive())
            assert captured.value.category == "disconnected"
        finally:
            await transport.close()

    asyncio.run(scenario())


def test_stdio_close_terminates_hanging_process_and_is_idempotent(tmp_path):
    async def scenario():
        transport = StdioTransport(make_config(tmp_path), shutdown_timeout_seconds=0.05)
        await transport.open()
        process = transport.process
        await transport.send({"jsonrpc": "2.0", "id": 5, "method": "hang"})
        await asyncio.sleep(0.05)

        await transport.close()
        await transport.close()

        assert process is not None
        assert process.returncode is not None
        assert transport.is_open is False

    asyncio.run(scenario())


def test_stdio_transport_rejects_send_before_open(tmp_path):
    transport = StdioTransport(make_config(tmp_path))

    with pytest.raises(MCPTransportError, match="not open"):
        asyncio.run(transport.send({"jsonrpc": "2.0", "method": "ping"}))

