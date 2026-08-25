from __future__ import annotations

import asyncio
import inspect
import logging

from mycode.tool.base import (
    AsyncTool,
    ToolCall,
    ToolDefinition,
    ToolInvocationContext,
    ToolResult,
    ToolWorkspaceScope,
)
from mycode.tool.registry import ToolRegistry
from mycode.workspace import WorkspaceKind


logger = logging.getLogger(__name__)


class ToolExecutor:
    """统一执行工具调用，把异常和超时包装成结构化结果。"""

    def __init__(self, registry: ToolRegistry, timeout_seconds: float = 10.0) -> None:
        self._registry = registry
        self._timeout_seconds = timeout_seconds

    def definitions(self) -> list[ToolDefinition]:
        return self._registry.definitions()

    async def execute(
        self,
        call: ToolCall,
        *,
        context: ToolInvocationContext | None = None,
    ) -> ToolResult:
        tool = self._registry.get(call.name)
        if tool is None:
            logger.warning("模型请求了未知工具：%s", call.name)
            return ToolResult(
                ok=False,
                tool_name=call.name,
                content={"tool_call_id": call.id},
                error=f"unknown tool: {call.name}",
            )

        if call.arguments is None:
            logger.warning("工具参数不是合法 JSON：%s", call.name)
            return ToolResult(
                ok=False,
                tool_name=call.name,
                content={"tool_call_id": call.id, "raw_arguments": call.raw_arguments},
                error="invalid JSON arguments",
            )

        definition = tool.definition
        if (
            context is not None
            and context.workspace.kind is WorkspaceKind.WORKTREE
            and definition.workspace_scope is ToolWorkspaceScope.SHARED_ONLY
        ):
            return ToolResult(
                ok=False,
                tool_name=call.name,
                content={
                    "tool_call_id": call.id,
                    "reason_code": "workspace_scope_forbidden",
                    "workspace": str(context.workspace.root),
                },
                error="隔离工作区不能执行仅共享工作区工具。",
            )
        timeout_seconds = (
            definition.execution_timeout_seconds
            if definition.execution_timeout_seconds is not None
            else self._timeout_seconds
        )

        try:
            logger.info("开始执行工具：%s", call.name)
            operation = (
                _invoke_async_tool(tool.execute_async, call.arguments, context)
                if isinstance(tool, AsyncTool)
                else asyncio.to_thread(
                    _invoke_sync_tool,
                    tool.execute,
                    call.arguments,
                    context,
                )
            )
            result = await asyncio.wait_for(
                operation,
                timeout=timeout_seconds,
            )
            logger.info("工具执行完成：%s，成功：%s", call.name, result.ok)
            return result
        except asyncio.TimeoutError:
            logger.warning("工具执行超时：%s", call.name)
            return ToolResult(
                ok=False,
                tool_name=call.name,
                content={"tool_call_id": call.id, "timed_out": True},
                error=f"tool execution timeout after {timeout_seconds} seconds",
            )
        except Exception as exc:
            logger.exception("工具执行异常：%s", call.name)
            return ToolResult(
                ok=False,
                tool_name=call.name,
                content={"tool_call_id": call.id},
                error=str(exc),
            )


def _invoke_sync_tool(execute, arguments, context: ToolInvocationContext | None):
    if context is not None and _accepts_context(execute):
        return execute(arguments, context)
    return execute(arguments)


async def _invoke_async_tool(execute_async, arguments, context: ToolInvocationContext | None):
    if context is not None and _accepts_context(execute_async):
        return await execute_async(arguments, context)
    return await execute_async(arguments)


def _accepts_context(method) -> bool:
    try:
        signature = inspect.signature(method)
    except (TypeError, ValueError):
        return False
    parameters = tuple(signature.parameters.values())
    return any(
        parameter.kind is inspect.Parameter.VAR_POSITIONAL
        or parameter.kind is inspect.Parameter.VAR_KEYWORD
        or parameter.name == "context"
        for parameter in parameters
    ) or len(parameters) >= 2
