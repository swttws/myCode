from __future__ import annotations

from mycode.tool.base import Tool, ToolDefinition, ToolKind


class ToolRegistry:
    """集中登记工具，并输出协议需要的 tool spec。"""

    def __init__(self, tools: list[Tool] | None = None) -> None:
        self._tools: dict[str, Tool] = {}
        for tool in tools or []:
            self.register(tool)

    def register(self, tool: Tool) -> None:
        definition = tool.definition
        name = definition.name
        if name in self._tools:
            raise ValueError(f"duplicate tool name: {name}")
        # Agent 调度只相信显式分类，不根据工具名称猜测读写属性。
        if definition.kind not in (ToolKind.READ, ToolKind.WRITE):
            raise ValueError(f"invalid tool kind: {definition.kind}")
        self._tools[name] = tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def definitions(self) -> list[ToolDefinition]:
        # 稳定工具顺序让相同工具集合保持可缓存的请求前缀。
        return sorted((tool.definition for tool in self._tools.values()), key=lambda definition: definition.name)

    def openai_tool_specs(self) -> list[dict[str, object]]:
        return self.openai_chat_tool_specs()

    def openai_chat_tool_specs(self) -> list[dict[str, object]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": definition.name,
                    "description": definition.description,
                    "parameters": definition.parameters,
                },
            }
            for definition in self.definitions()
        ]

    def openai_responses_tool_specs(self) -> list[dict[str, object]]:
        return [
            {
                "type": "function",
                "name": definition.name,
                "description": definition.description,
                "parameters": definition.parameters,
                "strict": False,
            }
            for definition in self.definitions()
        ]
