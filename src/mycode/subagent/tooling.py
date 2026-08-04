from __future__ import annotations

from collections.abc import Callable
import inspect
from pathlib import Path
from dataclasses import dataclass
from types import MappingProxyType

from mycode.compact import create_context_manager
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
    ToolWorkspaceScope,
    create_default_tool_registry,
)
from mycode.workspace import WorkspaceContext, WorkspaceKind, WorkspaceLease


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
    context_manager: object | None = None
    skill_runtime: object | None = None
    hook_runtime: object | None = None
    cleanup: Callable[[], None] | None = None

    def close(self) -> None:
        if self.cleanup is not None:
            self.cleanup()


class TaskToolRegistryFactory:
    def __init__(
        self,
        *,
        workspace_root: str | Path,
        home: str | Path | None = None,
        skill_catalog_factory: Callable[[Callable[[], frozenset[str]]], object] | None = None,
        mcp_pool: object | None = None,
        executor_timeout_seconds: float = 10.0,
    ) -> None:
        self._workspace_root = Path(workspace_root)
        self._home = Path(home) if home is not None else Path.home()
        self._skill_catalog_factory = skill_catalog_factory
        self._mcp_pool = mcp_pool
        self._executor_timeout_seconds = executor_timeout_seconds

    def create(
        self,
        parent_registry: ToolRegistry,
        *,
        memory=None,
        llm=None,
        llm_config=None,
        llm_factory=None,
        permission=None,
        agent_config=None,
        hook_runtime_factory: Callable[[], object] | None = None,
        workspace_lease: WorkspaceLease | None = None,
    ) -> TaskToolRuntime:
        cleanup_callbacks: list[Callable[[], None]] = []
        workspace_context = (
            workspace_lease.context
            if workspace_lease is not None
            else _shared_workspace(self._workspace_root)
        )
        workspace_root = workspace_context.root
        context_manager = self._create_context_manager(
            workspace_root=workspace_root,
            memory=memory,
            llm=llm,
            llm_config=llm_config,
            agent_config=agent_config,
            cleanup_callbacks=cleanup_callbacks,
        )
        hook_runtime = hook_runtime_factory() if hook_runtime_factory is not None else None

        local_defaults = create_default_tool_registry(workspace_root)
        local_by_name = {
            definition.name: local_defaults.get(definition.name)
            for definition in local_defaults.definitions()
        }
        if context_manager is not None:
            local_by_name["read_compact_artifact"] = context_manager.artifact_tool_for_scope(
                ToolRuntimeScope.TASK_LOCAL
            )

        task_tools = []
        missing_task_local_tools: list[str] = []
        load_skill_requested = False
        tool_search_parent = None
        mcp_pool = self._mcp_pool
        is_worktree_workspace = (
            workspace_lease is not None
            and workspace_lease.context.kind is WorkspaceKind.WORKTREE
        )

        for definition in parent_registry.definitions():
            parent_tool = parent_registry.get(definition.name)
            if parent_tool is None:
                continue
            if (
                is_worktree_workspace
                and definition.workspace_scope is ToolWorkspaceScope.SHARED_ONLY
            ):
                continue
            if _is_tool_search(parent_tool):
                tool_search_parent = parent_tool
                mcp_pool = _pool_for(parent_tool, fallback=mcp_pool)
                continue

            if _is_mcp_wrapper(parent_tool):
                mcp_pool = _pool_for(parent_tool, fallback=mcp_pool)
                task_tools.append(_clone_mcp_wrapper(parent_tool, pool=mcp_pool))
                continue

            if definition.name == "load_skill" and definition.runtime_scope is ToolRuntimeScope.TASK_LOCAL:
                load_skill_requested = True
                continue

            if definition.name in local_by_name:
                task_tools.append(local_by_name[definition.name])
                continue

            if definition.runtime_scope is ToolRuntimeScope.PARENT_ONLY:
                task_tools.append(ParentOnlyToolAdapter(definition))
                continue

            if definition.runtime_scope is ToolRuntimeScope.TASK_LOCAL:
                local_tool = local_by_name.get(definition.name)
                if local_tool is None:
                    missing_task_local_tools.append(definition.name)
                    continue
                task_tools.append(local_tool)
                continue

            task_tools.append(parent_tool)

        if missing_task_local_tools:
            _run_cleanup(tuple(cleanup_callbacks))
            raise RuntimeError(
                "task_local_tool_factory_missing: "
                + ", ".join(sorted(missing_task_local_tools))
            )

        registry = ToolRegistry(task_tools)
        executor = ToolExecutor(registry, timeout_seconds=self._executor_timeout_seconds)

        if tool_search_parent is not None:
            if mcp_pool is None:
                _run_cleanup(tuple(cleanup_callbacks))
                raise RuntimeError("task_mcp_pool_missing")
            registry.register(_clone_tool_search(tool_search_parent, registry=registry, pool=mcp_pool))

        skill_runtime = None
        if load_skill_requested:
            try:
                skill_runtime = self._create_skill_runtime(
                    workspace_root=workspace_root,
                    registry=registry,
                    executor=executor,
                    llm=llm,
                    llm_config=llm_config,
                    llm_factory=llm_factory,
                    permission=permission,
                    agent_config=agent_config,
                    workspace_context=workspace_context,
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

    def _create_context_manager(
        self,
        *,
        workspace_root: Path,
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
            home=self._home,
            llm=llm,
            memory=memory,
            config=llm_config.compact,
            model_timeout_seconds=agent_config.model_timeout_seconds,
        )
        cleanup_callbacks.append(context_manager.close)
        return context_manager

    def _create_skill_runtime(
        self,
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
    ):
        if (
            self._skill_catalog_factory is None
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

        catalog = _create_skill_catalog(
            self._skill_catalog_factory,
            task_tool_names,
            workspace_context.root,
        )
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


def _is_mcp_wrapper(tool) -> bool:
    should_defer = getattr(tool, "should_defer", None)
    return (
        callable(should_defer)
        and should_defer() is True
        and _remote_tool_for(tool) is not None
        and _pool_for(tool) is not None
    )


def _is_tool_search(tool) -> bool:
    definition = getattr(tool, "definition", None)
    return (
        getattr(definition, "name", None) == "tool_search"
        and _pool_for(tool) is not None
    )


def _remote_tool_for(tool):
    remote_tool = getattr(tool, "remote_tool", None)
    if remote_tool is not None:
        return remote_tool
    return getattr(tool, "_remote_tool", None)


def _pool_for(tool, *, fallback=None):
    pool = getattr(tool, "pool", None)
    if pool is not None:
        return pool
    pool = getattr(tool, "_pool", None)
    return pool if pool is not None else fallback


def _clone_mcp_wrapper(parent_tool, *, pool):
    remote_tool = _remote_tool_for(parent_tool)
    if remote_tool is None or pool is None:
        raise RuntimeError("task_mcp_wrapper_clone_failed")
    return parent_tool.__class__(remote_tool, pool)


def _clone_tool_search(parent_tool, *, registry: ToolRegistry, pool):
    if pool is None:
        raise RuntimeError("task_mcp_pool_missing")
    return parent_tool.__class__(registry, pool)


def _create_skill_catalog(factory, tool_names, workspace_root: Path):
    if _factory_accepts_workspace_root(factory):
        return factory(tool_names, workspace_root)
    return factory(tool_names)


def _factory_accepts_workspace_root(factory) -> bool:
    try:
        signature = inspect.signature(factory)
    except (TypeError, ValueError):
        return False
    parameters = tuple(signature.parameters.values())
    return any(
        parameter.kind is inspect.Parameter.VAR_POSITIONAL
        or parameter.kind is inspect.Parameter.VAR_KEYWORD
        for parameter in parameters
    ) or len(parameters) >= 2


def _shared_workspace(workspace_root: Path) -> WorkspaceContext:
    root = Path(workspace_root).resolve()
    return WorkspaceContext(
        kind=WorkspaceKind.SHARED,
        root=root,
        repository_root=root,
        repository_id="task-tool-workspace",
        task_identity=None,
        branch_name=None,
        hooks_path=None,
    )


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
