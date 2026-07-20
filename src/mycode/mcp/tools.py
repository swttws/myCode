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
        if not self._tool_is_current(wrapper):
            return _search_failure("not_found")
        return self._discover(wrapper)

    async def execute_async(self, arguments: ToolArguments) -> ToolResult:
        wrapper, failure = self._resolve(arguments)
        if wrapper is None:
            server_name = self._configured_server_for(arguments.get("name"))
            if server_name is not None:
                if not await self._pool.ensure_available(server_name):
                    return _search_failure("server_unavailable")
                wrapper, failure = self._resolve(arguments)
        if failure is not None:
            return failure
        assert wrapper is not None
        if not self._pool.is_available(wrapper.server_name):
            if not await self._pool.ensure_available(wrapper.server_name):
                return _search_failure("server_unavailable")
        if not self._tool_is_current(wrapper):
            return _search_failure("not_found")
        return self._discover(wrapper)

    def _configured_server_for(self, public_name: object) -> str | None:
        if not isinstance(public_name, str):
            return None
        server_name, separator, remote_name = public_name.partition("__")
        if not separator or not remote_name:
            return None
        server_names = getattr(self._pool, "server_names", ())
        return server_name if server_name in server_names else None

    def _tool_is_current(self, wrapper: MCPToolWrapper) -> bool:
        has_tool = getattr(self._pool, "has_tool", None)
        if callable(has_tool):
            return has_tool(wrapper.server_name, wrapper.remote_name)
        return any(
            tool.public_name == wrapper.definition.name for tool in self._pool.tools
        )

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
    def ensure_search_tool() -> None:
        existing = registry.get(TOOL_SEARCH_NAME)
        if existing is None:
            registry.register(ToolSearch(registry, pool))
        elif not isinstance(existing, ToolSearch):
            raise ValueError(
                f"reserved MCP tool name is already registered: {TOOL_SEARCH_NAME}"
            )

    def reconcile(server_name: str, remote_tools: tuple[RemoteTool, ...]) -> None:
        desired = {
            tool.public_name: MCPToolWrapper(tool, pool)
            for tool in remote_tools
            if tool.server_name == server_name
        }
        existing = {
            definition.name: tool
            for definition in registry.definitions()
            if isinstance((tool := registry.get(definition.name)), MCPToolWrapper)
            and tool.server_name == server_name
        }

        for name, wrapper in existing.items():
            replacement = desired.get(name)
            if replacement is None or replacement.definition != wrapper.definition:
                registry.unregister(name)
        for name, wrapper in desired.items():
            if registry.get(name) is None:
                registry.register(wrapper)
        if desired:
            ensure_search_tool()

    pool.add_tools_listener(reconcile)
    if getattr(pool, "server_names", ()):
        ensure_search_tool()
    grouped: dict[str, list[RemoteTool]] = {}
    for remote_tool in pool.tools:
        grouped.setdefault(remote_tool.server_name, []).append(remote_tool)
    for server_name, remote_tools in grouped.items():
        reconcile(server_name, tuple(remote_tools))

    return tuple(
        tool
        for definition in registry.definitions()
        if isinstance((tool := registry.get(definition.name)), MCPToolWrapper)
    )


def _search_failure(category: str) -> ToolResult:
    return ToolResult(
        ok=False,
        tool_name=TOOL_SEARCH_NAME,
        content={"category": category},
        error=f"tool search failed: {category}",
    )
