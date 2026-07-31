from __future__ import annotations

from typing import Any

from mycode.skill.models import SkillMode, SkillResourceError
from mycode.skill.runtime import SkillRuntime
from mycode.tool import (
    ToolArguments,
    ToolDefinition,
    ToolKind,
    ToolResult,
    ToolRuntimeScope,
)


class SkillLoadTool:
    def __init__(self, *, runtime: SkillRuntime, executor=None) -> None:
        self._runtime = runtime
        self._executor = executor

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name=SkillRuntime.LOAD_TOOL_NAME,
            description="按名称加载 Skill SOP，或读取已激活 Skill 的单个资源。",
            parameters={
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "arguments": {"type": "string"},
                    "resource": {"type": "string"},
                },
                "required": ["name"],
            },
            kind=ToolKind.READ,
            parallel_safe=False,
            runtime_scope=ToolRuntimeScope.TASK_LOCAL,
        )

    async def execute_async(self, arguments: ToolArguments) -> ToolResult:
        name = arguments.get("name")
        if not isinstance(name, str) or not name:
            return _failure("invalid_arguments", "name is required")
        raw_arguments = arguments.get("arguments", "")
        if raw_arguments is None:
            raw_arguments = ""
        if not isinstance(raw_arguments, str):
            return _failure("invalid_arguments", "arguments must be a string")

        try:
            self._runtime.refresh()
        except Exception as exc:
            return _failure("refresh_error", str(exc))

        resource = arguments.get("resource")
        if resource is not None:
            if raw_arguments:
                return _failure("invalid_arguments", "resource reads cannot include arguments")
            return self._read_resource(name, resource)

        try:
            definition = self._runtime.definition(name)
        except KeyError:
            return _failure("unknown_skill", f"unknown skill: {name}", name=name)

        activation = self._runtime.activate(name, raw_arguments)
        if definition.metadata.mode is SkillMode.SHARED:
            return ToolResult(
                ok=True,
                tool_name=SkillRuntime.LOAD_TOOL_NAME,
                content={
                    "action": "activated",
                    "name": name,
                    "mode": "shared",
                    "revision": activation.revision,
                    "resources": list(definition.resources),
                    "set_scope": True,
                },
            )
        if self._executor is None:
            return _failure("executor_unavailable", "isolated skill executor is unavailable", name=name)
        run_context = self._runtime.current_run_context()
        if run_context is None:
            return _failure("missing_run_context", "isolated skill requires parent run context", name=name)
        if run_context.isolated_depth > 0:
            return _failure("recursive_isolated_skill", "recursive isolated skill execution is not allowed", name=name)
        result = await self._executor.execute_loaded(definition, raw_arguments)
        if not result.ok:
            return _failure(result.error_code or "isolated_execution_error", result.summary, name=name)
        return ToolResult(
            ok=True,
            tool_name=SkillRuntime.LOAD_TOOL_NAME,
            content={
                "action": "completed",
                "name": name,
                "mode": "isolated",
                "summary": result.summary,
            },
        )

    def _read_resource(self, name: str, resource: Any) -> ToolResult:
        if not isinstance(resource, str) or not resource:
            return _failure("invalid_arguments", "resource must be a string", name=name)
        if not self._runtime.is_active(name):
            return _failure("resource_error", "skill is not active", name=name)
        try:
            text = self._runtime.read_resource(name, resource)
        except (KeyError, SkillResourceError) as exc:
            return _failure("resource_error", str(exc), name=name)
        return ToolResult(
            ok=True,
            tool_name=SkillRuntime.LOAD_TOOL_NAME,
            content={
                "action": "resource",
                "name": name,
                "path": resource.replace("\\", "/"),
                "text": text,
            },
        )


def _failure(category: str, message: str, *, name: str | None = None) -> ToolResult:
    content: dict[str, Any] = {"category": category}
    if name is not None:
        content["name"] = name
    return ToolResult(
        ok=False,
        tool_name=SkillRuntime.LOAD_TOOL_NAME,
        content=content,
        error=message,
    )
