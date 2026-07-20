from __future__ import annotations

import asyncio

import pytest

from mycode.mcp import (
    MCPConfig,
    MCPServerConfig,
    MCPServerState,
    MCPTransportKind,
    RemoteTool,
)
from mycode.mcp.connection import MCPConnectionError, MCPRemoteError
from mycode.mcp.pool import MCPServerPool
from mycode.tool import ToolKind


def make_server_config(name: str, *, read_tools=()) -> MCPServerConfig:
    return MCPServerConfig(
        name=name,
        transport=MCPTransportKind.STDIO,
        timeout_seconds=1.0,
        command="unused",
        args=(),
        env={},
        url=None,
        headers={},
        read_tools=frozenset(read_tools),
    )


def make_remote_tool(server_name: str, name: str) -> RemoteTool:
    return RemoteTool(
        server_name=server_name,
        remote_name=name,
        public_name=f"{server_name}__{name}",
        description=f"{name} description",
        parameters={"type": "object", "properties": {}},
        kind=ToolKind.WRITE,
    )


class FakeConnection:
    def __init__(
        self,
        tools=(),
        *,
        error: Exception | None = None,
        gate=None,
        activity=None,
        responses=(),
    ):
        self.tools = tuple(tools)
        self.error = error
        self.gate = gate
        self.activity = activity
        self.initialize_count = 0
        self.close_count = 0
        self.is_failed = False
        self.responses = list(responses)
        self.request_calls = []

    async def initialize(self):
        self.initialize_count += 1
        if self.activity is not None:
            self.activity["active"] += 1
            self.activity["maximum"] = max(
                self.activity["maximum"], self.activity["active"]
            )
        if self.gate is not None:
            await self.gate.wait()
        if self.activity is not None:
            self.activity["active"] -= 1
        if self.error is not None:
            raise self.error
        return self.tools

    async def close(self):
        self.close_count += 1

    async def request(self, method, params):
        self.request_calls.append((method, params))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            if not isinstance(response, MCPRemoteError):
                self.is_failed = True
            raise response
        return response


def test_pool_initializes_servers_concurrently_and_caches_prefixed_tools():
    async def scenario():
        gate = asyncio.Event()
        activity = {"active": 0, "maximum": 0}
        connections = {
            "alpha": FakeConnection(
                [make_remote_tool("alpha", "echo")], gate=gate, activity=activity
            ),
            "beta": FakeConnection(
                [make_remote_tool("beta", "echo")], gate=gate, activity=activity
            ),
        }
        pool = MCPServerPool(
            MCPConfig((make_server_config("alpha"), make_server_config("beta"))),
            connection_factory=lambda config: connections[config.name],
        )

        initialization = asyncio.create_task(pool.initialize_all())
        for _ in range(20):
            if activity["active"] == 2:
                break
            await asyncio.sleep(0)
        assert activity["active"] == 2
        gate.set()

        diagnostics = await initialization
        assert diagnostics == ()
        assert activity["maximum"] == 2
        assert [tool.public_name for tool in pool.tools] == ["alpha__echo", "beta__echo"]
        assert pool.server_state("alpha") is MCPServerState.READY
        assert pool.server_state("beta") is MCPServerState.READY
        assert connections["alpha"].initialize_count == 1
        assert connections["beta"].initialize_count == 1

    asyncio.run(scenario())


def test_pool_isolates_failed_server_and_keeps_available_tools():
    async def scenario():
        connections = {
            "broken": FakeConnection(
                error=MCPConnectionError("connect_failed", "sensitive internal detail")
            ),
            "healthy": FakeConnection([make_remote_tool("healthy", "echo")]),
        }
        pool = MCPServerPool(
            MCPConfig((make_server_config("broken"), make_server_config("healthy"))),
            connection_factory=lambda config: connections[config.name],
        )

        diagnostics = await pool.initialize_all()

        assert pool.server_state("broken") is MCPServerState.FAILED
        assert pool.server_state("healthy") is MCPServerState.READY
        assert [tool.public_name for tool in pool.tools] == ["healthy__echo"]
        assert diagnostics[0].server_name == "broken"
        assert diagnostics[0].category == "connect_failed"
        assert diagnostics[0].transport is MCPTransportKind.STDIO
        assert "sensitive internal detail" not in diagnostics[0].message

    asyncio.run(scenario())


def test_pool_applies_exact_read_tool_allowlist_per_server():
    async def scenario():
        connection = FakeConnection(
            [
                make_remote_tool("files", "read_file"),
                make_remote_tool("files", "read_file_extra"),
            ]
        )
        pool = MCPServerPool(
            MCPConfig((make_server_config("files", read_tools={"read_file"}),)),
            connection_factory=lambda config: connection,
        )

        await pool.initialize_all()
        tools = {tool.remote_name: tool for tool in pool.tools}

        assert tools["read_file"].kind is ToolKind.READ
        assert tools["read_file_extra"].kind is ToolKind.WRITE

    asyncio.run(scenario())


def test_pool_skips_only_invalid_or_duplicate_remote_tool_names():
    async def scenario():
        connection = FakeConnection(
            [
                make_remote_tool("server", "valid"),
                make_remote_tool("server", "bad.name"),
                make_remote_tool("server", "valid"),
            ]
        )
        pool = MCPServerPool(
            MCPConfig((make_server_config("server"),)),
            connection_factory=lambda config: connection,
        )

        diagnostics = await pool.initialize_all()

        assert [tool.public_name for tool in pool.tools] == ["server__valid"]
        assert pool.server_state("server") is MCPServerState.READY
        assert len(diagnostics) == 2
        assert all(diagnostic.category == "tool_definition" for diagnostic in diagnostics)

    asyncio.run(scenario())


def test_pool_reuses_initialized_connection_for_consecutive_tool_calls():
    async def scenario():
        connection = FakeConnection(
            [make_remote_tool("server", "echo")],
            responses=[
                {"content": [{"type": "text", "text": "first"}]},
                {"content": [{"type": "text", "text": "second"}]},
            ],
        )
        pool = MCPServerPool(
            MCPConfig((make_server_config("server"),)),
            connection_factory=lambda config: connection,
        )
        await pool.initialize_all()

        first = await pool.call_tool("server", "echo", {"value": 1})
        second = await pool.call_tool("server", "echo", {"value": 2})

        assert first.ok is True
        assert second.ok is True
        assert connection.initialize_count == 1
        assert connection.request_calls == [
            ("tools/call", {"name": "echo", "arguments": {"value": 1}}),
            ("tools/call", {"name": "echo", "arguments": {"value": 2}}),
        ]

    asyncio.run(scenario())


def test_pool_reconnects_and_rediscovers_after_call_failure():
    async def scenario():
        first_connection = FakeConnection(
            [make_remote_tool("server", "echo")],
            responses=[MCPConnectionError("timeout", "sensitive timeout detail")],
        )
        second_connection = FakeConnection(
            [make_remote_tool("server", "echo")],
            responses=[{"content": [{"type": "text", "text": "recovered"}]}],
        )
        connections = [first_connection, second_connection]
        pool = MCPServerPool(
            MCPConfig((make_server_config("server"),)),
            connection_factory=lambda config: connections.pop(0),
        )
        await pool.initialize_all()

        failed = await pool.call_tool("server", "echo", {})
        recovered = await pool.call_tool("server", "echo", {})

        assert failed.ok is False
        assert failed.content["category"] == "timeout"
        assert "sensitive timeout detail" not in failed.error
        assert first_connection.close_count == 1
        assert second_connection.initialize_count == 1
        assert recovered.ok is True
        assert pool.server_state("server") is MCPServerState.READY

    asyncio.run(scenario())


def test_pool_concurrent_waiters_trigger_only_one_reconnect():
    async def scenario():
        first_connection = FakeConnection([make_remote_tool("server", "echo")])
        reconnect_gate = asyncio.Event()
        second_connection = FakeConnection(
            [make_remote_tool("server", "echo")],
            gate=reconnect_gate,
            responses=[
                {"content": [{"type": "text", "text": "one"}]},
                {"content": [{"type": "text", "text": "two"}]},
            ],
        )
        connections = [first_connection, second_connection]
        pool = MCPServerPool(
            MCPConfig((make_server_config("server"),)),
            connection_factory=lambda config: connections.pop(0),
        )
        await pool.initialize_all()
        first_connection.is_failed = True

        first = asyncio.create_task(pool.call_tool("server", "echo", {"call": 1}))
        second = asyncio.create_task(pool.call_tool("server", "echo", {"call": 2}))
        for _ in range(20):
            if second_connection.initialize_count == 1:
                break
            await asyncio.sleep(0)
        reconnect_gate.set()

        results = await asyncio.gather(first, second)
        assert all(result.ok for result in results)
        assert second_connection.initialize_count == 1
        assert connections == []

    asyncio.run(scenario())


def test_pool_converts_remote_is_error_to_structured_tool_failure():
    async def scenario():
        connection = FakeConnection(
            [make_remote_tool("server", "echo")],
            responses=[
                {
                    "content": [{"type": "text", "text": "remote rejected input"}],
                    "isError": True,
                }
            ],
        )
        pool = MCPServerPool(
            MCPConfig((make_server_config("server"),)),
            connection_factory=lambda config: connection,
        )
        await pool.initialize_all()

        result = await pool.call_tool("server", "echo", {"bad": True})

        assert result.ok is False
        assert result.tool_name == "server__echo"
        assert result.content["isError"] is True
        assert result.error == "remote MCP tool returned an error"

    asyncio.run(scenario())


def test_pool_close_is_idempotent_and_closes_every_connection():
    async def scenario():
        connections = {
            "alpha": FakeConnection([make_remote_tool("alpha", "echo")]),
            "beta": FakeConnection([make_remote_tool("beta", "echo")]),
        }
        pool = MCPServerPool(
            MCPConfig((make_server_config("alpha"), make_server_config("beta"))),
            connection_factory=lambda config: connections[config.name],
        )
        await pool.initialize_all()

        await pool.close()
        await pool.close()

        assert connections["alpha"].close_count == 1
        assert connections["beta"].close_count == 1
        assert pool.server_state("alpha") is MCPServerState.CLOSED
        assert pool.server_state("beta") is MCPServerState.CLOSED
        assert pool.tools == ()

    asyncio.run(scenario())


def test_pool_remote_jsonrpc_error_does_not_invalidate_healthy_connection():
    async def scenario():
        connection = FakeConnection(
            [make_remote_tool("server", "echo")],
            responses=[
                MCPRemoteError(-32000, "remote rejected input"),
                {"content": [{"type": "text", "text": "still connected"}]},
            ],
        )
        pool = MCPServerPool(
            MCPConfig((make_server_config("server"),)),
            connection_factory=lambda config: connection,
        )
        await pool.initialize_all()

        rejected = await pool.call_tool("server", "echo", {"bad": True})
        succeeded = await pool.call_tool("server", "echo", {"bad": False})

        assert rejected.ok is False
        assert rejected.content["category"] == "remote_error"
        assert succeeded.ok is True
        assert connection.initialize_count == 1
        assert connection.close_count == 0
        assert pool.server_state("server") is MCPServerState.READY

    asyncio.run(scenario())


def test_pool_does_not_call_tool_removed_during_reconnect():
    async def scenario():
        first_connection = FakeConnection([make_remote_tool("server", "echo")])
        second_connection = FakeConnection([], responses=[])
        connections = [first_connection, second_connection]
        pool = MCPServerPool(
            MCPConfig((make_server_config("server"),)),
            connection_factory=lambda config: connections.pop(0),
        )
        await pool.initialize_all()
        first_connection.is_failed = True

        result = await pool.call_tool("server", "echo", {})

        assert result.ok is False
        assert result.content["category"] == "tool_unavailable"
        assert second_connection.request_calls == []
        assert pool.server_state("server") is MCPServerState.READY

    asyncio.run(scenario())


def test_pool_notifies_listeners_with_replacement_catalog_after_rediscovery():
    async def scenario():
        first_connection = FakeConnection([make_remote_tool("server", "old")])
        second_connection = FakeConnection([make_remote_tool("server", "new")])
        connections = [first_connection, second_connection]
        pool = MCPServerPool(
            MCPConfig((make_server_config("server"),)),
            connection_factory=lambda config: connections.pop(0),
        )
        catalogs = []
        pool.add_tools_listener(
            lambda server_name, tools: catalogs.append(
                (server_name, tuple(tool.public_name for tool in tools))
            )
        )

        await pool.initialize_all()
        first_connection.is_failed = True
        assert await pool.ensure_available("server") is True

        assert catalogs == [
            ("server", ("server__old",)),
            ("server", ("server__new",)),
        ]

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "response",
    [
        {},
        {"content": "not-a-list"},
        {"content": [{"text": "missing type"}]},
        {"content": [], "structuredContent": []},
        {"content": [], "isError": "false"},
    ],
)
def test_pool_rejects_malformed_call_tool_result(response):
    async def scenario():
        connection = FakeConnection(
            [make_remote_tool("server", "echo")],
            responses=[response],
        )
        pool = MCPServerPool(
            MCPConfig((make_server_config("server"),)),
            connection_factory=lambda config: connection,
        )
        await pool.initialize_all()

        result = await pool.call_tool("server", "echo", {})

        assert result.ok is False
        assert result.content["category"] == "invalid_response"
        assert connection.close_count == 1
        assert pool.server_state("server") is MCPServerState.FAILED

    asyncio.run(scenario())
