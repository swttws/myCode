import asyncio
import sys
import types
from pathlib import Path
from types import MappingProxyType

import pytest

try:
    import httpx  # noqa: F401
except ModuleNotFoundError:
    fake_httpx = types.ModuleType("httpx")

    class FakeHTTPError(Exception):
        pass

    class FakeHeaders(dict):
        pass

    class FakeAsyncClient:
        is_closed = False

        def __init__(self, *args, **kwargs):
            pass

    class FakeResponse:
        headers = {}

    class FakeAsyncByteStream:
        pass

    fake_httpx.HTTPError = FakeHTTPError
    fake_httpx.Headers = FakeHeaders
    fake_httpx.AsyncClient = FakeAsyncClient
    fake_httpx.Response = FakeResponse
    fake_httpx.AsyncByteStream = FakeAsyncByteStream
    sys.modules["httpx"] = fake_httpx
    import httpx  # noqa: F401

from mycode.agent import AgentConfig
from mycode.compact.archive import ReadCompactArtifactTool
from mycode.config import LLMConfig
from mycode.compact.models import CompactConfig
from mycode.memory import InMemoryConversationMemory
from mycode.memory.tools import ReadMemoryNoteTool
from mycode.permission.models import PermissionDecision, PermissionEffect, PermissionMode
from mycode.permission.service import PermissionService
from mycode.mcp.tools import MCPToolWrapper, ToolSearch
from mycode.skill.catalog import SkillCatalog
from mycode.skill.load_tool import SkillLoadTool
from mycode.skill.loader import SkillLoader
from mycode.subagent.context import ParentAgentSnapshotStore
from mycode.subagent.models import (
    AgentModelTier,
    AgentPermissionMode,
    AgentRoleDefinition,
    AgentRoleMetadata,
    AgentRoleSource,
    ParentAgentSnapshot,
    SubAgentKind,
    SubAgentLaunchRequest,
)
from mycode.subagent.tooling import (
    SubAgentPermissionInterceptor,
    SubAgentToolPolicy,
    create_task_tool_runtime,
    create_task_permission_service,
)
from mycode.tool import (
    ToolCall,
    ToolDefinition,
    ToolKind,
    ToolRegistry,
    ToolResult,
    ToolRuntimeScope,
    ToolWorkspaceScope,
    create_default_tool_registry,
)
from mycode.workspace import (
    WorkspaceContext,
    WorkspaceKind,
    WorkspaceLease,
    WorkspacePreparation,
    WorkspaceTaskIdentity,
)
from tests.skill_test_support import FakeLLM, write_skill


class FakeRemoteTool:
    def __init__(self, *, server_name, remote_name, public_name, description, parameters, kind):
        self.server_name = server_name
        self.remote_name = remote_name
        self.public_name = public_name
        self.description = description
        self.parameters = parameters
        self.kind = kind


class FakeNotes:
    pass


class FakeArchiveSession:
    pass


class FakeMCPPool:
    def __init__(self, tools=(), *, available=()):
        self.tools = tuple(tools)
        self.server_names = tuple(dict.fromkeys(tool.server_name for tool in self.tools))
        self.available = set(available)
        self.ensure_calls = []

    def is_available(self, server_name):
        return server_name in self.available

    async def ensure_available(self, server_name):
        self.ensure_calls.append(server_name)
        self.available.add(server_name)
        return True

    def has_tool(self, server_name, remote_name):
        return any(
            tool.server_name == server_name and tool.remote_name == remote_name
            for tool in self.tools
        )


class FakeMCPToolWrapper(MCPToolWrapper):
    def __init__(self, remote_tool, pool):
        super().__init__(remote_tool, pool)

    def should_defer(self):
        return True


class FakeToolSearch(ToolSearch):
    def __init__(self, registry, pool):
        super().__init__(registry, pool)


class AllowPermission:
    async def before_tool(self, call, definition, *, plan_only, round_index):
        return PermissionDecision(
            effect=PermissionEffect.ALLOW,
            reason_code="allow",
            message_zh="allow",
            mode=PermissionMode.DEFAULT,
            display_arguments=MappingProxyType({}),
        )

    def denied_result(self, call, decision):
        return ToolResult(
            ok=False,
            tool_name=call.name,
            content={"reason_code": decision.reason_code},
            error=decision.message_zh,
        )

    async def after_tool(self, call, result):
        return result


class FakeTaskLocalTool:
    @property
    def definition(self):
        return tool_definition("custom_task_local", scope=ToolRuntimeScope.TASK_LOCAL)

    def execute(self, arguments):
        raise AssertionError("missing task-local factory should fail before execution")


class TaskToolRegistryFactory:
    def __init__(
        self,
        *,
        workspace_root: Path,
        home: Path | None = None,
        skill_catalog_factory=None,
        mcp_pool=None,
        executor_timeout_seconds: float = 10.0,
    ) -> None:
        self._workspace_root = workspace_root
        self._home = home
        self._skill_catalog_factory = skill_catalog_factory
        self._mcp_pool = mcp_pool
        self._executor_timeout_seconds = executor_timeout_seconds

    def create(self, parent_registry, **kwargs):
        lease = kwargs.get("workspace_lease")
        workspace = lease.context if lease is not None else shared_workspace(self._workspace_root)
        return create_task_tool_runtime(
            workspace=workspace,
            parent_registry=parent_registry,
            allowed_tool_names=frozenset(definition.name for definition in parent_registry.definitions()),
            permission=kwargs.get("permission"),
            memory=kwargs.get("memory"),
            llm=kwargs.get("llm"),
            llm_config=kwargs.get("llm_config"),
            llm_factory=kwargs.get("llm_factory"),
            agent_config=kwargs.get("agent_config"),
            hook_runtime_factory=kwargs.get("hook_runtime_factory"),
            home=self._home,
            skill_catalog_factory=self._skill_catalog_factory,
            mcp_pool=kwargs.get("mcp_pool", self._mcp_pool),
            executor_timeout_seconds=self._executor_timeout_seconds,
        )


def parent_only_definition(name="Agent"):
    return ToolDefinition(
        name=name,
        description="Parent-only tool.",
        parameters={"type": "object", "properties": {}, "required": []},
        kind=ToolKind.WRITE,
        runtime_scope=ToolRuntimeScope.PARENT_ONLY,
    )


def tool_definition(name, *, scope=ToolRuntimeScope.SHARED, kind=ToolKind.READ):
    return ToolDefinition(
        name=name,
        description=f"{name} tool.",
        parameters={"type": "object", "properties": {}, "required": []},
        kind=kind,
        runtime_scope=scope,
    )


def role(
    allowed_tools,
    *,
    denied_tools=("Agent",),
    permission_mode=AgentPermissionMode.INHERIT,
):
    return AgentRoleDefinition(
        metadata=AgentRoleMetadata(
            name="general",
            description="通用",
            allowed_tools=tuple(allowed_tools),
            denied_tools=tuple(denied_tools),
            model=AgentModelTier.INHERIT,
            max_rounds=8,
            permission_mode=permission_mode,
        ),
        instruction="执行任务。",
        source=AgentRoleSource.BUILTIN,
        entry_path="general.md",
        revision="abc",
    )


def launch_request(kind=SubAgentKind.DEFINED, *, parent_tools=None):
    parent = ParentAgentSnapshot(
        messages=(),
        tools=tuple(parent_tools or ()),
        model_id="model",
        max_rounds=8,
        permission_mode=PermissionMode.DEFAULT,
        plan_only=False,
    )
    return SubAgentLaunchRequest(
        kind=kind,
        task="task",
        role_name=("general" if kind is SubAgentKind.DEFINED else None),
        requested_background=(kind is SubAgentKind.FORK),
        parent=parent,
    )


def shared_workspace(root: Path) -> WorkspaceContext:
    resolved = root.resolve()
    return WorkspaceContext(
        kind=WorkspaceKind.SHARED,
        root=resolved,
        repository_root=resolved,
        repository_id="test-shared-workspace",
        task_identity=None,
        branch_name=None,
        hooks_path=None,
    )


def test_parent_state_tools_are_marked_parent_only_and_skill_load_is_task_local():
    assert (
        ReadMemoryNoteTool(FakeNotes()).definition.runtime_scope
        is ToolRuntimeScope.PARENT_ONLY
    )
    assert (
        ReadCompactArtifactTool(FakeArchiveSession()).definition.runtime_scope
        is ToolRuntimeScope.PARENT_ONLY
    )
    assert SkillLoadTool(runtime=object()).definition.runtime_scope is ToolRuntimeScope.TASK_LOCAL


def test_create_task_tool_runtime_omits_parent_only_tools_from_child_registry(tmp_path):
    definition = parent_only_definition("read_memory_note")

    class ParentTool:
        @property
        def definition(self):
            return definition

        def execute(self, arguments):
            raise AssertionError("parent-only tool must not be registered")

    parent_registry = ToolRegistry([ParentTool()])
    runtime = create_task_tool_runtime(
        workspace=shared_workspace(tmp_path),
        parent_registry=parent_registry,
        allowed_tool_names=frozenset({"read_memory_note"}),
        permission=AllowPermission(),
    )

    assert runtime.registry.get("read_memory_note") is None


def test_policy_visible_names_filters_defined_tools_in_order():
    definitions = (
        tool_definition("read_file"),
        tool_definition("write_file", kind=ToolKind.WRITE),
        tool_definition("search_code"),
        tool_definition("read_memory_note", scope=ToolRuntimeScope.PARENT_ONLY),
        tool_definition("Agent", scope=ToolRuntimeScope.PARENT_ONLY, kind=ToolKind.WRITE),
    )
    policy = SubAgentToolPolicy(
        tool_definitions=definitions,
        background_allowed_tools=("read_file", "search_code"),
    )
    request = launch_request(parent_tools=definitions)

    assert policy.visible_names(
        request=request,
        role=role(("*",), denied_tools=("write_file", "Agent")),
        detached=False,
    ) == frozenset({"read_file", "search_code"})
    assert policy.visible_names(
        request=request,
        role=role(()),
        detached=False,
    ) == frozenset()
    assert policy.visible_names(
        request=request,
        role=role(("*",), denied_tools=()),
        detached=True,
    ) == frozenset({"read_file", "search_code"})


def test_policy_evaluate_denies_fork_runtime_forbidden_tools_before_executor():
    definitions = (
        tool_definition("read_file"),
        tool_definition("write_file", kind=ToolKind.WRITE),
        tool_definition("read_memory_note", scope=ToolRuntimeScope.PARENT_ONLY),
        tool_definition("Agent", scope=ToolRuntimeScope.PARENT_ONLY, kind=ToolKind.WRITE),
    )
    policy = SubAgentToolPolicy(
        tool_definitions=definitions,
        background_allowed_tools=("read_file",),
    )
    request = launch_request(kind=SubAgentKind.FORK, parent_tools=definitions)

    assert policy.visible_names(request=request, role=None, detached=True) == frozenset({"read_file"})
    assert policy.evaluate(
        request=request,
        role=None,
        detached=True,
        tool_name="read_file",
    ).allowed is True
    assert policy.evaluate(
        request=request,
        role=None,
        detached=True,
        tool_name="write_file",
    ).reason_code == "background_tool_forbidden"
    assert policy.evaluate(
        request=request,
        role=None,
        detached=True,
        tool_name="read_memory_note",
    ).reason_code == "parent_runtime_tool_forbidden"
    assert policy.evaluate(
        request=request,
        role=None,
        detached=True,
        tool_name="Agent",
    ).reason_code == "subagent_recursive_forbidden"


def test_policy_effective_permission_mode_never_relaxes_parent():
    policy = SubAgentToolPolicy(tool_definitions=(), background_allowed_tools=("read_file",))
    cases = [
        (PermissionMode.STRICT, AgentPermissionMode.INHERIT, PermissionMode.STRICT),
        (PermissionMode.STRICT, AgentPermissionMode.PERMISSIVE, PermissionMode.STRICT),
        (PermissionMode.DEFAULT, AgentPermissionMode.PERMISSIVE, PermissionMode.DEFAULT),
        (PermissionMode.PERMISSIVE, AgentPermissionMode.DEFAULT, PermissionMode.DEFAULT),
        (PermissionMode.PERMISSIVE, AgentPermissionMode.STRICT, PermissionMode.STRICT),
        (PermissionMode.DEFAULT, AgentPermissionMode.INHERIT, PermissionMode.DEFAULT),
    ]

    for parent_mode, role_mode, expected in cases:
        assert policy.effective_permission_mode(parent_mode, role((), permission_mode=role_mode)) is expected


class FakePermission:
    def __init__(self, decision):
        self.decision = decision
        self.calls = 0

    async def before_tool(self, call, definition, *, plan_only, round_index):
        self.calls += 1
        return self.decision

    def denied_result(self, call, decision):
        return ToolResult(ok=False, tool_name=call.name, content={"reason_code": decision.reason_code}, error=decision.message_zh)

    async def after_tool(self, call, result):
        return result


def permission_decision(effect):
    return PermissionDecision(
        effect=effect,
        reason_code="underlying",
        message_zh="需要审批",
        mode=PermissionMode.DEFAULT,
        display_arguments=MappingProxyType({}),
    )


def test_permission_interceptor_converts_ask_to_non_interactive_deny():
    definition = tool_definition("write_file", kind=ToolKind.WRITE)
    request = launch_request(parent_tools=(definition,))
    policy = SubAgentToolPolicy(
        tool_definitions=(definition,),
        background_allowed_tools=("write_file",),
    )
    underlying = FakePermission(permission_decision(PermissionEffect.ASK))
    interceptor = SubAgentPermissionInterceptor(
        tool_policy=policy,
        request=request,
        role=role(("*",)),
        detached=False,
        permission=underlying,
    )

    decision = asyncio.run(
        interceptor.before_tool(
            ToolCall(id="call-1", name="write_file", arguments={}),
            definition,
            plan_only=False,
            round_index=1,
        )
    )
    result = interceptor.denied_result(
        ToolCall(id="call-1", name="write_file", arguments={}),
        decision,
    )

    assert underlying.calls == 1
    assert decision.effect is PermissionEffect.DENY
    assert decision.reason_code == "approval_required_non_interactive"
    assert result.content["reason_code"] == "approval_required_non_interactive"


def test_permission_interceptor_policy_deny_skips_underlying_permission():
    definition = tool_definition("write_file", kind=ToolKind.WRITE)
    request = launch_request(parent_tools=(definition,))
    policy = SubAgentToolPolicy(
        tool_definitions=(definition,),
        background_allowed_tools=("read_file",),
    )
    underlying = FakePermission(permission_decision(PermissionEffect.ALLOW))
    interceptor = SubAgentPermissionInterceptor(
        tool_policy=policy,
        request=request,
        role=role(("*",)),
        detached=True,
        permission=underlying,
    )

    decision = asyncio.run(
        interceptor.before_tool(
            ToolCall(id="call-1", name="write_file", arguments={}),
            definition,
            plan_only=False,
            round_index=1,
        )
    )

    assert underlying.calls == 0
    assert decision.effect is PermissionEffect.DENY
    assert decision.reason_code == "background_tool_forbidden"


def test_task_permission_service_reloads_persistent_rules_without_parent_session_state(tmp_path):
    parent = PermissionService.create(tmp_path, home=tmp_path / "home")
    parent.set_session_mode(PermissionMode.PERMISSIVE)

    child = create_task_permission_service(
        workspace_root=tmp_path,
        home=tmp_path / "home",
    )

    assert parent.effective_mode()[0] is PermissionMode.PERMISSIVE
    assert child.effective_mode()[0] is PermissionMode.DEFAULT


def test_task_tool_registry_rebuilds_file_tools_with_isolated_cache(tmp_path):
    parent_registry = create_default_tool_registry(tmp_path)
    factory = TaskToolRegistryFactory(workspace_root=tmp_path)

    first = factory.create(parent_registry)
    second = factory.create(parent_registry)

    assert first.registry is not second.registry
    assert first.executor is not second.executor
    assert first.registry.get("read_file") is not second.registry.get("read_file")
    assert first.registry.get("read_file")._cache is not second.registry.get("read_file")._cache
    assert first.registry.get("read_file")._cache is not parent_registry.get("read_file")._cache


def test_task_tool_registry_preserves_shared_tools_and_replaces_parent_only_tools(tmp_path):
    class SharedTool:
        @property
        def definition(self):
            return tool_definition("shared")

        def execute(self, arguments):
            return ToolResult(ok=True, tool_name="shared", content={})

    class ParentTool:
        @property
        def definition(self):
            return parent_only_definition("read_memory_note")

        def execute(self, arguments):
            raise AssertionError("parent-only tool must be adapted")

    shared = SharedTool()
    parent_registry = ToolRegistry([shared, ParentTool()])
    runtime = TaskToolRegistryFactory(workspace_root=tmp_path).create(parent_registry)

    assert runtime.registry.get("shared") is shared
    assert runtime.registry.get("read_memory_note") is None


def test_task_tool_registry_reports_missing_task_local_factory(tmp_path):
    parent_registry = ToolRegistry([FakeTaskLocalTool()])
    factory = TaskToolRegistryFactory(workspace_root=tmp_path)

    with pytest.raises(RuntimeError, match="task_local_tool_factory_missing"):
        factory.create(parent_registry)


def test_task_tool_registry_rebuilds_load_skill_with_child_skill_runtime(tmp_path):
    workspace = tmp_path / "workspace"
    home = tmp_path / "home"
    builtin = tmp_path / "builtin"
    project_skills = workspace / ".mycode" / "skills"
    for root in (project_skills, home / ".mycode" / "skills", builtin):
        root.mkdir(parents=True)
    write_skill(project_skills, "review", allowed_tools=("read_file",))

    parent_registry = create_default_tool_registry(workspace)
    parent_runtime = object()
    parent_registry.register(SkillLoadTool(runtime=parent_runtime))

    def skill_catalog_factory(tool_names, workspace_root):
        return SkillCatalog(
            loader=SkillLoader(workspace_root=workspace_root, home=home, builtin_root=builtin),
            tool_names=tool_names,
            reserved_slash_names=frozenset(),
        )

    runtime = TaskToolRegistryFactory(
        workspace_root=workspace,
        home=home,
        skill_catalog_factory=skill_catalog_factory,
    ).create(
        parent_registry,
        llm=FakeLLM(),
        memory=InMemoryConversationMemory(),
        llm_config=LLMConfig(
            protocol="openai_chat",
            model="model",
            base_url="https://example.invalid",
            api_key="key",
            compact=CompactConfig(context_window_tokens=30_000),
        ),
        llm_factory=lambda config: FakeLLM(),
        permission=AllowPermission(),
        agent_config=AgentConfig(),
    )

    child_tool = runtime.registry.get("load_skill")

    assert child_tool is not parent_registry.get("load_skill")
    result = asyncio.run(child_tool.execute_async({"name": "review"}))
    assert result.ok is True
    assert runtime.skill_runtime.is_active("review") is True


def test_task_tool_registry_builds_project_skills_from_worktree_workspace(tmp_path):
    shared = tmp_path / "shared"
    worktree = tmp_path / "worktree"
    home = tmp_path / "home"
    builtin = tmp_path / "builtin"
    for root in (
        shared / ".mycode" / "skills",
        worktree / ".mycode" / "skills",
        home / ".mycode" / "skills",
        builtin,
    ):
        root.mkdir(parents=True)
    write_skill(shared / ".mycode" / "skills", "review", body="SHARED {{arguments}}")
    write_skill(worktree / ".mycode" / "skills", "review", body="WORKTREE {{arguments}}")

    parent_registry = create_default_tool_registry(shared)
    parent_registry.register(SkillLoadTool(runtime=object()))
    factory_roots = []

    def skill_catalog_factory(tool_names, workspace_root):
        factory_roots.append(workspace_root)
        return SkillCatalog(
            loader=SkillLoader(workspace_root=workspace_root, home=home, builtin_root=builtin),
            tool_names=tool_names,
            reserved_slash_names=frozenset(),
        )

    runtime = TaskToolRegistryFactory(
        workspace_root=shared,
        home=home,
        skill_catalog_factory=skill_catalog_factory,
    ).create(
        parent_registry,
        llm=FakeLLM(),
        memory=InMemoryConversationMemory(),
        llm_config=LLMConfig(
            protocol="openai_chat",
            model="model",
            base_url="https://example.invalid",
            api_key="key",
            compact=CompactConfig(context_window_tokens=30_000),
        ),
        llm_factory=lambda config: FakeLLM(),
        permission=AllowPermission(),
        agent_config=AgentConfig(),
        workspace_lease=worktree_lease("task-000001", "task-000001", root=worktree),
    )

    child_tool = runtime.registry.get("load_skill")
    result = asyncio.run(child_tool.execute_async({"name": "review", "arguments": "target"}))

    assert result.ok is True
    assert factory_roots == [worktree.resolve()]
    assert runtime.skill_runtime.definition("review").entry_path.is_relative_to(worktree)


def test_task_mcp_wrappers_and_tool_search_use_child_registry_discovery(tmp_path):
    remote = FakeRemoteTool(
        server_name="files",
        remote_name="echo",
        public_name="files__echo",
        description="Echo remotely.",
        parameters={"type": "object", "properties": {}, "required": []},
        kind=ToolKind.READ,
    )
    pool = FakeMCPPool((remote,), available={"files"})
    parent_registry = ToolRegistry([FakeMCPToolWrapper(remote, pool)])
    parent_registry.register(FakeToolSearch(parent_registry, pool))

    runtime = TaskToolRegistryFactory(workspace_root=tmp_path).create(parent_registry)
    child_wrapper = runtime.registry.get("files__echo")
    child_search = runtime.registry.get("tool_search")

    assert child_wrapper is not parent_registry.get("files__echo")
    assert child_search is not parent_registry.get("tool_search")
    assert [definition.name for definition in parent_registry.model_definitions()] == ["tool_search"]

    result = asyncio.run(child_search.execute_async({"name": "files__echo"}))

    assert result.ok is True
    assert [definition.name for definition in runtime.registry.model_definitions()] == [
        "files__echo",
        "tool_search",
    ]
    assert [definition.name for definition in parent_registry.model_definitions()] == ["tool_search"]


def test_task_registry_hides_shared_only_remote_tools_for_worktree_lease(tmp_path):
    remote = FakeRemoteTool(
        server_name="files",
        remote_name="echo",
        public_name="files__echo",
        description="Echo remotely.",
        parameters={"type": "object", "properties": {}, "required": []},
        kind=ToolKind.READ,
    )
    pool = FakeMCPPool((remote,), available={"files"})
    shared_root = tmp_path / "shared"
    shared_root.mkdir()
    parent_registry = create_default_tool_registry(shared_root)
    parent_registry.register(FakeMCPToolWrapper(remote, pool))
    parent_registry.register(FakeToolSearch(parent_registry, pool))

    runtime = TaskToolRegistryFactory(workspace_root=shared_root).create(
        parent_registry,
        workspace_lease=worktree_lease(
            "task-000001",
            "task-000001",
            root=tmp_path / "worktree",
        ),
    )

    assert runtime.registry.get("read_file") is not None
    assert runtime.registry.get("files__echo") is None
    assert runtime.registry.get("tool_search") is None
    assert parent_registry.get("files__echo") is not None
    assert parent_registry.get("tool_search") is not None


def test_fake_mcp_helpers_default_to_shared_only_workspace_scope():
    remote = FakeRemoteTool(
        server_name="files",
        remote_name="echo",
        public_name="files__echo",
        description="Echo remotely.",
        parameters={"type": "object", "properties": {}, "required": []},
        kind=ToolKind.READ,
    )
    pool = FakeMCPPool((remote,), available={"files"})

    assert FakeMCPToolWrapper(remote, pool).definition.workspace_scope is ToolWorkspaceScope.SHARED_ONLY
    assert FakeToolSearch(ToolRegistry(), pool).definition.workspace_scope is ToolWorkspaceScope.SHARED_ONLY


def test_task_registry_can_replace_parent_compact_tool_with_child_archive_tool(tmp_path):
    parent_registry = ToolRegistry([ReadCompactArtifactTool(FakeArchiveSession())])
    runtime = TaskToolRegistryFactory(workspace_root=tmp_path, home=tmp_path / "home").create(
        parent_registry,
        llm=FakeLLM(),
        memory=InMemoryConversationMemory(),
        llm_config=LLMConfig(
            protocol="openai_chat",
            model="model",
            base_url="https://example.invalid",
            api_key="key",
            compact=CompactConfig(context_window_tokens=30_000),
        ),
        llm_factory=lambda config: FakeLLM(),
        permission=AllowPermission(),
        agent_config=AgentConfig(),
    )

    child_tool = runtime.registry.get("read_compact_artifact")

    assert child_tool is None
    assert runtime.context_manager is not None


def worktree_lease(task_id: str, task_token: str, *, root: Path | None = None) -> WorkspaceLease:
    root = root or Path(f"C:/repo/.worktrees/general/{task_token}")
    root.mkdir(parents=True, exist_ok=True)
    identity = WorkspaceTaskIdentity(
        repository_id="repo-123",
        task_id=task_id,
        role_name="general",
        task_token=task_token,
        relative_name=f"general/{task_token}",
        branch_name=f"mycode/worktree/general/{task_token}",
        base_commit="a" * 40,
    )
    return WorkspaceLease(
        context=WorkspaceContext(
            kind=WorkspaceKind.WORKTREE,
            root=root,
            repository_root=root.parent.parent.parent,
            repository_id=identity.repository_id,
            task_identity=identity,
            branch_name=identity.branch_name,
            hooks_path=None,
        ),
        preparation=WorkspacePreparation.CREATED,
        metadata_path=Path(f"C:/repo/.worktrees/.metadata/general/{task_token}.json"),
        initialized_rules=(),
    )
