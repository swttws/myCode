from __future__ import annotations

import asyncio

from mycode.tool.base import ToolCall, ToolDefinition, ToolResult
from mycode.tool.registry import ToolRegistry


class ToolExecutor:
    """统一执行工具调用，把异常和超时包装成结构化结果。"""

    def __init__(self, registry: ToolRegistry, timeout_seconds: float = 10.0) -> None:
        self._registry = registry
        self._timeout_seconds = timeout_seconds

    def definitions(self) -> list[ToolDefinition]:
        return self._registry.definitions()

    async def execute(self, call: ToolCall) -> ToolResult:
        tool = self._registry.get(call.name)
        if tool is None:
            return ToolResult(
                ok=False,
                tool_name=call.name,
                content={"tool_call_id": call.id},
                error=f"unknown tool: {call.name}",
            )

        if call.arguments is None:
            return ToolResult(
                ok=False,
                tool_name=call.name,
                content={"tool_call_id": call.id, "raw_arguments": call.raw_arguments},
                error="invalid JSON arguments",
            )

        try:
            return await asyncio.wait_for(
                asyncio.to_thread(tool.execute, call.arguments),
                timeout=self._timeout_seconds,
            )
        except asyncio.TimeoutError:
            return ToolResult(
                ok=False,
                tool_name=call.name,
                content={"tool_call_id": call.id, "timed_out": True},
                error=f"tool execution timeout after {self._timeout_seconds} seconds",
            )
        except Exception as exc:
            return ToolResult(
                ok=False,
                tool_name=call.name,
                content={"tool_call_id": call.id},
                error=str(exc),
            )
