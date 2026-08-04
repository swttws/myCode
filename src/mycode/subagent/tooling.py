from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

from mycode.compact import create_context_manager
from mycode.mcp.tools import MCPToolWrapper, ToolSearch
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
    ToolCall,
    ToolDefinition,
    ToolExecutor,
    ToolRegistry,
    ToolResult,
    ToolRuntimeScope,
    ToolWorkspaceScope,
    create_default_tool_registry,
)
from mycode.workspace import WorkspaceContext, WorkspaceKind


@dataclass(frozen=True)
class TaskToolRuntime:
    registry: ToolRegistry
    executor: ToolExecutor
    context_manager: object | None = None
    skill_runtime: object | None = None
    hook_runtime: object | None = None
    cleanup: Callable[[], None] | None = None

    def close(self) -> None:
        if self.cleanup is not None:
            self.cleanup()


def create_task_tool_runtime(
    *,
    workspace: WorkspaceContext,
    parent_registry: ToolRegistry,
    allowed_tool_names: frozenset[str],
    permission,
    memory=None,
    llm=None,
    llm_config=None,
    llm_factory=None,
    agent_config=None,
    hook_runtime_factory: Callable[[], object] | None = None,
    home: str | Path | None = None,
    skill_catalog_factory: Callable[[Callable[[], frozenset[str]], Path], object] | None = None,
    mcp_pool=None,
    executor_timeout_seconds: float = 10.0,
) -> TaskToolRuntime:
    cleanup_callbacks: list[Callable[[], None]] = []
    workspace_root = workspace.root
    context_manager = _create_context_manager(
        workspace_root=workspace_root,
        home=Path(home) if home is not None else Path.home(),
        memory=memory,
        llm=llm,
        llm_config=llm_config,
        agent_config=agent_config,
        cleanup_callbacks=cleanup_callbacks,
    )
    hook_runtime = hook_runtime_factory() if hook_runtime_factory is not None else None
    local_by_name = _workspace_tool_factories(workspace_root)

    task_tools, missing_task_local_tools, load_skill_requested, tool_search_requested, active_mcp_pool = _collect_task_tools(
        parent_registry=parent_registry,
        workspace=workspace,
        allowed_tool_names=allowed_tool_names,
        local_by_name=local_by_name,
        mcp_pool=mcp_pool,
    )

    if missing_task_local_tools:
        _run_cleanup(tuple(cleanup_callbacks))
        raise RuntimeError(
            "task_local_tool_factory_missing: "
            + ", ".join(sorted(missing_task_local_tools))
        )

    registry = ToolRegistry(task_tools)
    executor = ToolExecutor(registry, timeout_seconds=executor_timeout_seconds)

    if tool_search_requested:
        if active_mcp_pool is None:
            _run_cleanup(tuple(cleanup_callbacks))
            raise RuntimeError("task_mcp_pool_missing")
        registry.register(ToolSearch(registry, active_mcp_pool))

    skill_runtime = None
    if load_skill_requested:
        try:
            skill_runtime = _create_skill_runtime(
                workspace_root=workspace_root,
                registry=registry,
                executor=executor,
                llm=llm,
                llm_config=llm_config,
                llm_factory=llm_factory,
                permission=permission,
                agent_config=agent_config,
                workspace_context=workspace,
                skill_catalog_factory=skill_catalog_factory,
            )
        except Exception:
            _run_cleanup(tuple(cleanup_callbacks))
            raise

    return TaskToolRuntime(
        registry=registry,
        executor=executor,
        context_manager=context_manager,
        skill_runtime=skill_runtime,
        hook_runtime=hook_runtime,
        cleanup=_cleanup_all(tuple(cleanup_callbacks)),
    )


@dataclass(frozen=True)
class _TaskToolSelection:
    tool: object | None = None
    missing_task_local_tool: str | None = None
    load_skill_requested: bool = False
    tool_search_requested: bool = False
    active_mcp_pool: object | None = None


def _collect_task_tools(
    *,
    parent_registry: ToolRegistry,
    workspace: WorkspaceContext,
    allowed_tool_names: frozenset[str],
    local_by_name: dict[str, object],
    mcp_pool,
) -> tuple[list[object], list[str], bool, bool, object | None]:
    task_tools: list[object] = []
    missing_task_local_tools: list[str] = []
    load_skill_requested = False
    tool_search_requested = False
    active_mcp_pool = mcp_pool

    for definition in parent_registry.definitions():
        selection = _select_task_tool(
            parent_registry=parent_registry,
            definition=definition,
            workspace=workspace,
            allowed_tool_names=allowed_tool_names,
            local_by_name=local_by_name,
            active_mcp_pool=active_mcp_pool,
        )
        if selection.tool is not None:
            task_tools.append(selection.tool)
        if selection.missing_task_local_tool is not None:
            missing_task_local_tools.append(selection.missing_task_local_tool)
        load_skill_requested = load_skill_requested or selection.load_skill_requested
        tool_search_requested = tool_search_requested or selection.tool_search_requested
        active_mcp_pool = active_mcp_pool or selection.active_mcp_pool

    return task_tools, missing_task_local_tools, load_skill_requested, tool_search_requested, active_mcp_pool


def _select_task_tool(
    *,
    parent_registry: ToolRegistry,
    definition: ToolDefinition,
    workspace: WorkspaceContext,
    allowed_tool_names: frozenset[str],
    local_by_name: dict[str, object],
    active_mcp_pool,
) -> _TaskToolSelection:
    name = definition.name
    if name not in allowed_tool_names or _hidden_in_workspace(definition, workspace):
        return _TaskToolSelection()

    parent_tool = parent_registry.get(name)
    if parent_tool is None:
        return _TaskToolSelection()
    if isinstance(parent_tool, ToolSearch):
        return _TaskToolSelection(
            tool=None,
            tool_search_requested=True,
            active_mcp_pool=active_mcp_pool or parent_tool.pool,
        )
    if isinstance(parent_tool, MCPToolWrapper):
        pool = active_mcp_pool or parent_tool.pool
        return _TaskToolSelection(
            tool=MCPToolWrapper(parent_tool.remote_tool, pool),
            active_mcp_pool=pool,
        )
    if name == "load_skill" and definition.runtime_scope is ToolRuntimeScope.TASK_LOCAL:
        return _TaskToolSelection(load_skill_requested=True)
    if name in local_by_name:
        return _TaskToolSelection(tool=local_by_name[name])
    if definition.runtime_scope is ToolRuntimeScope.PARENT_ONLY:
        return _TaskToolSelection()
    if definition.runtime_scope is ToolRuntimeScope.TASK_LOCAL:
        return _TaskToolSelection(missing_task_local_tool=name)
    return _TaskToolSelection(tool=parent_tool)



def _workspace_tool_factories(workspace_root: Path) -> dict[str, object]:
    local_defaults = create_default_tool_registry(workspace_root)
    local_by_name = {
        definition.name: local_defaults.get(definition.name)
        for definition in local_defaults.definitions()
    }
    return local_by_name


def _hidden_in_workspace(definition: ToolDefinition, workspace: WorkspaceContext) -> bool:
    return (
        workspace.kind is WorkspaceKind.WORKTREE
        and definition.workspace_scope is ToolWorkspaceScope.SHARED_ONLY
    )


def _create_context_manager(
    *,
    workspace_root: Path,
    home: Path,
    memory,
    llm,
    llm_config,
    agent_config,
    cleanup_callbacks: list[Callable[[], None]],
):
    if memory is None or llm is None or llm_config is None or agent_config is None:
        return None
    context_manager = create_context_manager(
        workspace_root=workspace_root,
        home=home,
        llm=llm,
        memory=memory,
        config=llm_config.compact,
        model_timeout_seconds=agent_config.model_timeout_seconds,
    )
    cleanup_callbacks.append(context_manager.close)
    return context_manager


def _create_skill_runtime(
    *,
    workspace_root: Path,
    registry: ToolRegistry,
    executor: ToolExecutor,
    llm,
    llm_config,
    llm_factory,
    permission,
    agent_config,
    workspace_context: WorkspaceContext,
    skill_catalog_factory: Callable[[Callable[[], frozenset[str]], Path], object] | None,
):
    if (
        skill_catalog_factory is None
        or llm is None
        or llm_config is None
        or llm_factory is None
        or permission is None
        or agent_config is None
    ):
        raise RuntimeError("task_local_tool_factory_missing: load_skill")

    from mycode.skill.executor import SkillExecutor
    from mycode.skill.load_tool import SkillLoadTool
    from mycode.skill.runtime import SkillRuntime

    def task_tool_names() -> frozenset[str]:
        names = frozenset(definition.name for definition in registry.definitions())
        return names | {SkillRuntime.LOAD_TOOL_NAME}

    catalog = skill_catalog_factory(task_tool_names, workspace_root)
    catalog.initialize()
    skill_runtime = SkillRuntime(catalog)
    skill_executor = SkillExecutor(
        runtime=skill_runtime,
        main_llm=llm,
        llm_config=llm_config,
        llm_factory=llm_factory,
        tool_registry=registry,
        tool_executor=executor,
        permission=permission,
        agent_config=agent_config,
        workspace=workspace_context,
    )
    registry.register(SkillLoadTool(runtime=skill_runtime, executor=skill_executor))
    return skill_runtime


def _cleanup_all(callbacks: tuple[Callable[[], None], ...]):
    if not callbacks:
        return None

    def cleanup() -> None:
        _run_cleanup(callbacks)

    return cleanup


def _run_cleanup(callbacks: tuple[Callable[[], None], ...]) -> None:
    for callback in reversed(callbacks):
        callback()


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
        candidates = self._candidate_names()
        if request.kind is SubAgentKind.FORK:
            visible = {definition.name for definition in request.parent.tools} & candidates
        elif role is None:
            visible = set()
        else:
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
            return _denied("subagent_recursive_forbidden", "subagent cannot call Agent recursively.")
        if definition is not None and definition.runtime_scope is ToolRuntimeScope.PARENT_ONLY:
            return _denied("parent_runtime_tool_forbidden", "tool is only executable in the parent runtime.")
        if detached and tool_name not in self._background_allowed_tools:
            return _denied("background_tool_forbidden", "background subagent cannot execute this tool.")
        if request.kind is SubAgentKind.DEFINED and role is not None:
            if tool_name in role.metadata.denied_tools:
                return _denied("role_tool_forbidden", "role denied this tool.")
            if role.metadata.allowed_tools != ("*",) and tool_name not in role.metadata.allowed_tools:
                return _denied("role_tool_not_allowed", "role did not allow this tool.")
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

    def _candidate_names(self) -> frozenset[str]:
        return frozenset(
            name
            for name, definition in self._definitions.items()
            if name != "Agent" and definition.runtime_scope is not ToolRuntimeScope.PARENT_ONLY
        )

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
                policy.message_zh or "subagent tool policy denied this tool.",
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
                "subagent cannot request interactive approval; tool call denied.",
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
        return await self._permission.after_tool(call, result)


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
