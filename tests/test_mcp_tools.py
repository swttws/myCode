from __future__ import annotations

import asyncio

from mycode.mcp import MCPToolWrapper, RemoteTool, ToolSearch, register_mcp_tools
from mycode.tool import ToolDefinition, ToolKind, ToolRegistry, ToolResult


def make_remote(server: str, name: str, *, kind=ToolKind.WRITE) -> RemoteTool:
    return RemoteTool(
        server_name=server,
        remote_name=name,
        public_name=f"{server}__{name}",
        description=f"Use {name}.",
        parameters={
            "type": "object",
            "properties": {"value": {"type": "string"}},
            "required": ["value"],
        },
        kind=kind,
    )


class FakePool:
    def __init__(self, tools=(), *, available=(), ensure_result=True):
        self.tools = tuple(tools)
        self.available = set(available)
        self.ensure_result = ensure_result
        self.calls = []
        self.ensure_calls = []

    def is_available(self, server_name):
        return server_name in self.available

    async def ensure_available(self, server_name):
        self.ensure_calls.append(server_name)
        if self.ensure_result:
            self.available.add(server_name)
        return self.ensure_result

    async def call_tool(self, server_name, remote_name, arguments):
        self.calls.append((server_name, remote_name, arguments))
        return ToolResult(
            ok=True,
            tool_name=f"{server_name}__{remote_name}",
            content={"value": arguments.get("value")},
        )


class LocalTool:
    @property
    def definition(self):
        return ToolDefinition(
            name="local",
            description="Local tool.",
            parameters={"type": "object", "properties": {}},
            kind=ToolKind.READ,
        )

    def execute(self, arguments):
        return ToolResult(ok=True, tool_name="local", content={})


def test_mcp_wrapper_is_deferred_and_preserves_remote_definition():
    remote = make_remote("files", "read_file", kind=ToolKind.READ)
    wrapper = MCPToolWrapper(remote, FakePool([remote], available={"files"}))

    assert wrapper.should_defer() is True
    assert wrapper.definition == ToolDefinition(
        name="files__read_file",
        description="Use read_file.",
        parameters=remote.parameters,
        kind=ToolKind.READ,
        grant_arguments=(),
    )


def test_mcp_wrapper_routes_async_call_to_original_server_and_tool_name():
    async def scenario():
        remote = make_remote("files", "echo")
        pool = FakePool([remote], available={"files"})
        wrapper = MCPToolWrapper(remote, pool)

        result = await wrapper.execute_async({"value": "hello"})

        assert result.ok is True
        assert pool.calls == [("files", "echo", {"value": "hello"})]

    asyncio.run(scenario())


def test_tool_search_is_read_tool_and_successfully_discovers_exact_public_name():
    async def scenario():
        remote = make_remote("files", "echo")
        pool = FakePool([remote], available={"files"})
        registry = ToolRegistry()
        wrapper = MCPToolWrapper(remote, pool)
        registry.register(wrapper)
        search = ToolSearch(registry, pool)

        result = await search.execute_async({"name": "files__echo"})

        assert search.definition.name == "tool_search"
        assert search.definition.kind is ToolKind.READ
        assert result.ok is True
        assert result.content["definition"] == {
            "name": "files__echo",
            "description": "Use echo.",
            "parameters": remote.parameters,
        }
        assert [definition.name for definition in registry.model_definitions()] == ["files__echo"]

    asyncio.run(scenario())


def test_tool_search_attempts_server_recovery_before_discovery():
    async def scenario():
        remote = make_remote("files", "echo")
        pool = FakePool([remote], available=set(), ensure_result=True)
        registry = ToolRegistry([MCPToolWrapper(remote, pool)])
        search = ToolSearch(registry, pool)

        result = await search.execute_async({"name": "files__echo"})

        assert result.ok is True
        assert pool.ensure_calls == ["files"]

    asyncio.run(scenario())


def test_tool_search_failures_are_structured_and_do_not_discover_tools():
    async def scenario():
        alpha = make_remote("alpha", "echo")
        beta = make_remote("beta", "echo")
        pool = FakePool([alpha, beta], available={"alpha"}, ensure_result=False)
        registry = ToolRegistry(
            [LocalTool(), MCPToolWrapper(alpha, pool), MCPToolWrapper(beta, pool)]
        )
        search = ToolSearch(registry, pool)

        cases = [
            ({"name": "missing__tool"}, "not_found"),
            ({"name": "local"}, "not_mcp_tool"),
            ({"name": "echo"}, "ambiguous"),
            ({"name": "beta__echo"}, "server_unavailable"),
            ({}, "invalid_arguments"),
        ]
        for arguments, category in cases:
            result = await search.execute_async(arguments)
            assert result.ok is False
            assert result.content["category"] == category

        assert registry.deferred_summaries()
        assert [definition.name for definition in registry.model_definitions()] == ["local"]

    asyncio.run(scenario())


def test_register_mcp_tools_adds_wrappers_and_single_search_tool():
    alpha = make_remote("alpha", "one")
    beta = make_remote("beta", "two")
    pool = FakePool([beta, alpha], available={"alpha", "beta"})
    registry = ToolRegistry([LocalTool()])

    wrappers = register_mcp_tools(pool, registry)

    assert [wrapper.definition.name for wrapper in wrappers] == ["alpha__one", "beta__two"]
    assert registry.get("tool_search") is not None
    assert [definition.name for definition in registry.model_definitions()] == ["local", "tool_search"]

