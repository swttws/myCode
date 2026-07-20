from __future__ import annotations

from typing import TYPE_CHECKING

from mycode.mcp.models import RemoteTool
from mycode.tool import (
    ToolArguments,
    ToolDefinition,
    ToolKind,
    ToolRegistry,
    ToolResult,
)


if TYPE_CHECKING:
    from mycode.mcp.pool import MCPServerPool


TOOL_SEARCH_NAME = "tool_search"


class MCPToolWrapper:
    def __init__(self, remote_tool: RemoteTool, pool: MCPServerPool) -> None:
        self._remote_tool = remote_tool
        self._pool = pool
        self._definition = ToolDefinition(
            name=remote_tool.public_name,
            description=remote_tool.description,
            parameters=dict(remote_tool.parameters),
            kind=remote_tool.kind,
            grant_arguments=(),
        )

    @property
    def definition(self) -> ToolDefinition:
        return self._definition

    @property
    def server_name(self) -> str:
        return self._remote_tool.server_name

    @property
    def remote_name(self) -> str:
        return self._remote_tool.remote_name

    def should_defer(self) -> bool:
        return True

    async def execute_async(self, arguments: ToolArguments) -> ToolResult:
        return await self._pool.call_tool(
            self.server_name,
            self.remote_name,
            arguments,
        )


class ToolSearch:
    def __init__(self, registry: ToolRegistry, pool: MCPServerPool) -> None:
        self._registry = registry
        self._pool = pool
        self._definition = ToolDefinition(
            name=TOOL_SEARCH_NAME,
            description="按完整公开名称获取一个延迟 MCP 工具的完整定义。",
            parameters={
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "系统提醒中列出的完整 MCP 工具名称。",
                    }
                },
                "required": ["name"],
            },
            kind=ToolKind.READ,
            grant_arguments=(),
        )

    @property
    def definition(self) -> ToolDefinition:
        return self._definition

    def execute(self, arguments: ToolArguments) -> ToolResult:
        wrapper, failure = self._resolve(arguments)
        if failure is not None:
            return failure
        assert wrapper is not None
        if not self._pool.is_available(wrapper.server_name):
            return _search_failure("server_unavailable")
        return self._discover(wrapper)

    async def execute_async(self, arguments: ToolArguments) -> ToolResult:
        wrapper, failure = self._resolve(arguments)
        if failure is not None:
            return failure
        assert wrapper is not None
        if not self._pool.is_available(wrapper.server_name):
            if not await self._pool.ensure_available(wrapper.server_name):
                return _search_failure("server_unavailable")
        return self._discover(wrapper)

    def _resolve(
        self,
        arguments: ToolArguments,
    ) -> tuple[MCPToolWrapper | None, ToolResult | None]:
        name = arguments.get("name")
        if not isinstance(name, str) or not name:
            return None, _search_failure("invalid_arguments")

        tool = self._registry.get(name)
        if tool is not None:
            if isinstance(tool, MCPToolWrapper):
                return tool, None
            return None, _search_failure("not_mcp_tool")

        candidates = [
            candidate
            for definition in self._registry.definitions()
            if isinstance((candidate := self._registry.get(definition.name)), MCPToolWrapper)
            and candidate.remote_name == name
        ]
        if len(candidates) > 1:
            return None, _search_failure("ambiguous")
        return None, _search_failure("not_found")

    def _discover(self, wrapper: MCPToolWrapper) -> ToolResult:
        definition = wrapper.definition
        content = {
            "definition": {
                "name": definition.name,
                "description": definition.description,
                "parameters": dict(definition.parameters),
            }
        }
        if not self._registry.mark_discovered(definition.name):
            return _search_failure("not_found")
        return ToolResult(
            ok=True,
            tool_name=TOOL_SEARCH_NAME,
            content=content,
        )


def register_mcp_tools(
    pool: MCPServerPool,
    registry: ToolRegistry,
) -> tuple[MCPToolWrapper, ...]:
    wrappers = tuple(
        MCPToolWrapper(remote_tool, pool)
        for remote_tool in sorted(pool.tools, key=lambda tool: tool.public_name)
    )
    for wrapper in wrappers:
        registry.register(wrapper)
    if wrappers and registry.get(TOOL_SEARCH_NAME) is None:
        registry.register(ToolSearch(registry, pool))
    return wrappers


def _search_failure(category: str) -> ToolResult:
    return ToolResult(
        ok=False,
        tool_name=TOOL_SEARCH_NAME,
        content={"category": category},
        error=f"tool search failed: {category}",
    )
