from __future__ import annotations

from pathlib import Path
from dataclasses import dataclass
from types import MappingProxyType

from mycode.permission.models import (
    PermissionDecision,
    PermissionEffect,
    PermissionMode,
)
from mycode.permission.service import PermissionService
from mycode.subagent.models import (
    AgentPermissionMode,
    AgentRoleDefinition,
    SubAgentKind,
    SubAgentLaunchRequest,
    ToolPolicyDecision,
)
from mycode.tool import (
    ToolArguments,
    ToolCall,
    ToolDefinition,
    ToolExecutor,
    ToolRegistry,
    ToolResult,
    ToolRuntimeScope,
    create_default_tool_registry,
)


class ParentOnlyToolAdapter:
    def __init__(self, definition: ToolDefinition) -> None:
        self._definition = definition

    @property
    def definition(self) -> ToolDefinition:
        return self._definition

    def execute(self, arguments: ToolArguments) -> ToolResult:
        reason_code = "parent_runtime_tool_forbidden"
        return ToolResult(
            ok=False,
            tool_name=self._definition.name,
            content={
                "reason_code": reason_code,
                "message": "该工具只能在父 Agent 运行时执行，子 Agent 中已拒绝。",
            },
            error=reason_code,
        )


@dataclass(frozen=True)
class TaskToolRuntime:
    registry: ToolRegistry
    executor: ToolExecutor


class TaskToolRegistryFactory:
    def __init__(
        self,
        *,
        workspace_root: str | Path,
        executor_timeout_seconds: float = 10.0,
    ) -> None:
        self._workspace_root = Path(workspace_root)
        self._executor_timeout_seconds = executor_timeout_seconds

    def create(self, parent_registry: ToolRegistry) -> TaskToolRuntime:
        local_defaults = create_default_tool_registry(self._workspace_root)
        local_by_name = {
            definition.name: local_defaults.get(definition.name)
            for definition in local_defaults.definitions()
        }
        task_tools = []
        for definition in parent_registry.definitions():
            parent_tool = parent_registry.get(definition.name)
            if parent_tool is None:
                continue
            if definition.runtime_scope is ToolRuntimeScope.PARENT_ONLY:
                task_tools.append(ParentOnlyToolAdapter(definition))
                continue
            if definition.runtime_scope is ToolRuntimeScope.TASK_LOCAL:
                local_tool = local_by_name.get(definition.name)
                if local_tool is None:
                    raise RuntimeError(
                        f"task_local_tool_factory_missing: {definition.name}"
                    )
                task_tools.append(local_tool)
                continue
            task_tools.append(parent_tool)

        registry = ToolRegistry(task_tools)
        return TaskToolRuntime(
            registry=registry,
            executor=ToolExecutor(registry, timeout_seconds=self._executor_timeout_seconds),
        )


class SubAgentToolPolicy:
    def __init__(
        self,
        *,
        tool_definitions: tuple[ToolDefinition, ...],
        background_allowed_tools: tuple[str, ...],
    ) -> None:
        self._definitions = {definition.name: definition for definition in tool_definitions}
        self._background_allowed_tools = frozenset(background_allowed_tools)

    def visible_names(
        self,
        *,
        request: SubAgentLaunchRequest,
        role: AgentRoleDefinition | None,
        detached: bool,
    ) -> frozenset[str]:
        if request.kind is SubAgentKind.FORK:
            return frozenset(tool.name for tool in request.parent.tools)
        if role is None:
            return frozenset()

        candidates = {
            name
            for name, definition in self._definitions.items()
            if name != "Agent" and definition.runtime_scope is not ToolRuntimeScope.PARENT_ONLY
        }
        allowed = (
            set(candidates)
            if role.metadata.allowed_tools == ("*",)
            else set(role.metadata.allowed_tools) & candidates
        )
        visible = allowed - set(role.metadata.denied_tools)
        if detached:
            visible &= self._background_allowed_tools
        return frozenset(visible)

    def evaluate(
        self,
        *,
        request: SubAgentLaunchRequest,
        role: AgentRoleDefinition | None,
        detached: bool,
        tool_name: str,
    ) -> ToolPolicyDecision:
        definition = self._definition_for(request, tool_name)
        if tool_name == "Agent":
            return _denied("subagent_recursive_forbidden", "子 Agent 中禁止再次调用 Agent 工具。")
        if definition is not None and definition.runtime_scope is ToolRuntimeScope.PARENT_ONLY:
            return _denied("parent_runtime_tool_forbidden", "该工具只能在父 Agent 运行时执行。")
        if detached and tool_name not in self._background_allowed_tools:
            return _denied("background_tool_forbidden", "后台子 Agent 不允许执行该工具。")
        if request.kind is SubAgentKind.DEFINED and role is not None:
            if tool_name in role.metadata.denied_tools:
                return _denied("role_tool_forbidden", "角色黑名单禁止执行该工具。")
            if role.metadata.allowed_tools != ("*",) and tool_name not in role.metadata.allowed_tools:
                return _denied("role_tool_not_allowed", "角色白名单未开放该工具。")
        return ToolPolicyDecision(allowed=True)

    def effective_permission_mode(
        self,
        parent: PermissionMode,
        role: AgentRoleDefinition | None,
    ) -> PermissionMode:
        if role is None or role.metadata.permission_mode is AgentPermissionMode.INHERIT:
            return parent
        role_mode = _ROLE_PERMISSION_MAP[role.metadata.permission_mode]
        return min((parent, role_mode), key=lambda mode: _PERMISSION_RANK[mode])

    def _definition_for(
        self,
        request: SubAgentLaunchRequest,
        tool_name: str,
    ) -> ToolDefinition | None:
        if tool_name in self._definitions:
            return self._definitions[tool_name]
        for definition in request.parent.tools:
            if definition.name == tool_name:
                return definition
        return None


class SubAgentPermissionInterceptor:
    def __init__(
        self,
        *,
        tool_policy: SubAgentToolPolicy,
        request: SubAgentLaunchRequest,
        role: AgentRoleDefinition | None,
        detached: bool,
        permission,
    ) -> None:
        self._tool_policy = tool_policy
        self._request = request
        self._role = role
        self._detached = detached
        self._permission = permission

    async def before_tool(
        self,
        call: ToolCall,
        definition: ToolDefinition,
        *,
        plan_only: bool,
        round_index: int,
    ) -> PermissionDecision:
        policy = self._tool_policy.evaluate(
            request=self._request,
            role=self._role,
            detached=self._detached,
            tool_name=call.name,
        )
        if not policy.allowed:
            return _permission_denied(
                policy.reason_code or "subagent_tool_forbidden",
                policy.message_zh or "子 Agent 工具策略拒绝执行该工具。",
                self._request.parent.permission_mode,
            )

        decision = await self._permission.before_tool(
            call,
            definition,
            plan_only=plan_only,
            round_index=round_index,
        )
        if decision.effect is PermissionEffect.ASK:
            return _permission_denied(
                "approval_required_non_interactive",
                "子 Agent 非交互执行中不能请求人工审批，已拒绝该工具调用。",
                decision.mode,
            )
        return decision

    def denied_result(self, call: ToolCall, decision: PermissionDecision) -> ToolResult:
        return ToolResult(
            ok=False,
            tool_name=call.name,
            content={
                "tool_call_id": call.id,
                "reason_code": decision.reason_code,
                "decision": decision.effect.value,
                "message": decision.message_zh,
            },
            error=decision.message_zh,
        )

    async def after_tool(self, call: ToolCall, result: ToolResult) -> ToolResult:
        after_tool = getattr(self._permission, "after_tool", None)
        if callable(after_tool):
            return await after_tool(call, result)
        return result


def create_task_permission_service(
    workspace_root: str | Path,
    *,
    home: str | Path | None = None,
) -> PermissionService:
    return PermissionService.create(workspace_root, home=home)


_PERMISSION_RANK = {
    PermissionMode.STRICT: 0,
    PermissionMode.DEFAULT: 1,
    PermissionMode.PERMISSIVE: 2,
}
_ROLE_PERMISSION_MAP = {
    AgentPermissionMode.STRICT: PermissionMode.STRICT,
    AgentPermissionMode.DEFAULT: PermissionMode.DEFAULT,
    AgentPermissionMode.PERMISSIVE: PermissionMode.PERMISSIVE,
}


def _denied(reason_code: str, message_zh: str) -> ToolPolicyDecision:
    return ToolPolicyDecision(
        allowed=False,
        reason_code=reason_code,
        message_zh=message_zh,
    )


def _permission_denied(
    reason_code: str,
    message_zh: str,
    mode: PermissionMode,
) -> PermissionDecision:
    return PermissionDecision(
        effect=PermissionEffect.DENY,
        reason_code=reason_code,
        message_zh=message_zh,
        mode=mode,
        display_arguments=MappingProxyType({}),
    )
