from __future__ import annotations

import asyncio

from mycode.mcp import (
    MCPConfig,
    MCPServerConfig,
    MCPServerState,
    MCPTransportKind,
    RemoteTool,
)
from mycode.mcp.connection import MCPConnectionError
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
    def __init__(self, tools=(), *, error: Exception | None = None, gate=None, activity=None):
        self.tools = tuple(tools)
        self.error = error
        self.gate = gate
        self.activity = activity
        self.initialize_count = 0
        self.close_count = 0
        self.is_failed = False

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
