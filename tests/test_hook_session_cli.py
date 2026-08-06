from __future__ import annotations

import asyncio
import importlib
import sys
import types
from pathlib import Path
from types import SimpleNamespace

try:
    import httpx  # noqa: F401
except ModuleNotFoundError:
    httpx_module = types.ModuleType("httpx")

    class HTTPError(Exception):
        pass

    class AsyncByteStream:
        async def __aiter__(self):
            if False:
                yield b""

        async def aclose(self):
            return None

    class AsyncClient:
        is_closed = False

        def __init__(self, *args, **kwargs):
            pass

        async def aclose(self):
            self.is_closed = True

    class Response:
        pass

    class Headers(dict):
        pass

    httpx_module.HTTPError = HTTPError
    httpx_module.AsyncByteStream = AsyncByteStream
    httpx_module.AsyncClient = AsyncClient
    httpx_module.Response = Response
    httpx_module.Headers = Headers
    sys.modules["httpx"] = httpx_module

try:
    from rich.console import Console as _Console  # noqa: F401
except ModuleNotFoundError:
    rich_module = types.ModuleType("rich")
    console_module = types.ModuleType("rich.console")

    class Console:
        def __init__(self, *args, file=None, **kwargs):
            self.file = file or sys.stdout

        def print(self, *args, sep=" ", end="\n", **kwargs):
            self.file.write(sep.join(str(item) for item in args) + end)

    console_module.Console = Console
    sys.modules["rich"] = rich_module
    sys.modules["rich.console"] = console_module

from mycode.agent import AgentEvent, AgentEventType, AgentMode
from mycode.hook.models import HookConfig, HookContext, HookEvent, HookTriggerResult
from mycode.permission.models import PermissionMode, RuleSource
from mycode.permission.pathing import PathGuard
from mycode.session import ChatSession
from mycode.slash import SlashDispatchKind, SlashDispatchResult
from mycode.skill.models import SkillMode
from mycode.subagent.models import AgentModelTier, SubAgentConfig
from mycode.tool import ToolKind
from mycode.workspace import WorkspaceContext, WorkspaceKind


async def collect_async(async_iterable):
    return [item async for item in async_iterable]


class FakeAgent:
    def __init__(self, events=None, operations=None) -> None:
        self.events = events or []
        self.runs = []
        self.clear_count = 0
        self.operations = operations

    async def run(self, user_text, *, mode, approval_provider=None, initial_skill_scope=None):
        self.runs.append(
            {
                "user_text": user_text,
                "mode": mode,
                "approval_provider": approval_provider,
                "initial_skill_scope": initial_skill_scope,
            }
        )
        for event in self.events:
            yield event

    def clear_memory(self):
        self.clear_count += 1
        if self.operations is not None:
            self.operations.append("agent")


class FakePermissions:
    def __init__(self, operations=None) -> None:
        self.mode = (PermissionMode.DEFAULT, None)
        self.clear_count = 0
        self.operations = operations

    def effective_mode(self):
        return self.mode

    def set_session_mode(self, mode):
        self.mode = (mode, RuleSource.SESSION)

    def clear_session(self):
        self.clear_count += 1
        self.mode = (PermissionMode.DEFAULT, None)
        if self.operations is not None:
            self.operations.append("permissions")


class RecordingMode(AgentMode):
    def __init__(self, operations):
        super().__init__()
        self._operations = operations

    def reset(self):
        self._operations.append("mode")
        super().reset()


class RecordingHookRuntime:
    def __init__(self, operations=None) -> None:
        self.contexts: list[HookContext] = []
        self.operations = operations

    async def trigger(self, context: HookContext) -> HookTriggerResult:
        self.contexts.append(context)
        if self.operations is not None:
            self.operations.append(f"hook:{context.event.value}")
        return HookTriggerResult(actions=())

    def prompt_blocks(self):
        return ()

    def clear_request_state(self):
        return None


class FakeSkillRuntime:
    def __init__(self) -> None:
        self.refresh_count = 0
        self.scope_name = None

    def refresh(self):
        self.refresh_count += 1

    def definition(self, name):
        metadata = SimpleNamespace(mode=SkillMode.SHARED)
        return SimpleNamespace(metadata=metadata)

    def activate(self, name, arguments):
        return SimpleNamespace(rendered_instruction=f"skill {name} {arguments}".strip())

    def set_current_scope(self, name):
        self.scope_name = name
        return SimpleNamespace(name=name)

    def clear(self):
        return None


def test_chat_session_send_and_send_skill_trigger_session_start_once() -> None:
    hook_runtime = RecordingHookRuntime()
    skill_runtime = FakeSkillRuntime()
    session = ChatSession(
        agent=FakeAgent([AgentEvent(AgentEventType.FINAL_RESPONSE, "ok")]),
        permissions=FakePermissions(),
        hook_runtime=hook_runtime,
        skill_runtime=skill_runtime,
    )

    asyncio.run(collect_async(session.send_skill("review", "main")))
    asyncio.run(collect_async(session.send("hello")))

    assert [context.event for context in hook_runtime.contexts] == [HookEvent.SESSION_START]
    assert hook_runtime.contexts[0].plan_only is False
    assert skill_runtime.refresh_count == 1
    assert session._agent.runs[0]["user_text"] == "skill review main"
    assert session._agent.runs[1]["user_text"] == "hello"


def test_chat_session_clear_triggers_session_clear_before_existing_state_reset() -> None:
    operations = []
    hook_runtime = RecordingHookRuntime(operations)
    agent = FakeAgent(operations=operations)
    permissions = FakePermissions(operations)
    mode = RecordingMode(operations)
    session = ChatSession(
        agent=agent,
        permissions=permissions,
        mode=mode,
        hook_runtime=hook_runtime,
    )
    session.set_plan_only(True)

    session.clear()

    assert [context.event for context in hook_runtime.contexts] == [HookEvent.SESSION_CLEAR]
    assert operations == ["hook:session_clear", "agent", "mode", "permissions"]
    assert session.is_plan_only() is False


def test_chat_session_clear_can_be_awaited_inside_running_event_loop() -> None:
    async def scenario():
        operations = []
        session = ChatSession(
            agent=FakeAgent(operations=operations),
            permissions=FakePermissions(operations),
            mode=RecordingMode(operations),
            hook_runtime=RecordingHookRuntime(operations),
        )
        result = session.clear()
        if hasattr(result, "__await__"):
            await result
        return operations

    assert asyncio.run(scenario()) == ["hook:session_clear", "agent", "mode", "permissions"]


def test_chat_session_close_triggers_session_end_once() -> None:
    hook_runtime = RecordingHookRuntime()
    session = ChatSession(
        agent=FakeAgent(),
        permissions=FakePermissions(),
        hook_runtime=hook_runtime,
    )

    asyncio.run(session.close())
    asyncio.run(session.close())

    assert [context.event for context in hook_runtime.contexts] == [HookEvent.SESSION_END]


def test_cli_default_missing_hook_config_starts_with_empty_config(
    tmp_path,
    monkeypatch,
) -> None:
    cli = _import_cli(monkeypatch)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli.Path, "cwd", staticmethod(lambda: tmp_path))
    monkeypatch.setattr(cli, "load_config", lambda path: _fake_config())
    monkeypatch.setattr(cli, "load_mcp_config", lambda path: (cli.MCPConfig(()), ()))
    monkeypatch.setattr(cli, "create_llm", lambda config: object())
    monkeypatch.setattr(cli, "create_default_slash_registry", lambda: _FakeRegistry())
    monkeypatch.setattr(cli, "PermissionService", _FakePermissionFactory(tmp_path))
    captured = {}

    async def fake_run_application(**kwargs):
        captured.update(kwargs)
        return 0

    monkeypatch.setattr(cli, "_run_application", fake_run_application)

    assert cli.main(["--config", str(tmp_path / "mycode.yaml")]) == 0

    assert captured["hook_config"] == HookConfig(version=1, rules=(), path=None)


def test_cli_explicit_missing_hook_config_returns_startup_error(
    tmp_path,
    monkeypatch,
    capsys,
) -> None:
    cli = _import_cli(monkeypatch)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli.Path, "cwd", staticmethod(lambda: tmp_path))
    monkeypatch.setattr(cli, "load_config", lambda path: _fake_config())
    monkeypatch.setattr(cli, "load_mcp_config", lambda path: (cli.MCPConfig(()), ()))
    monkeypatch.setattr(
        cli,
        "create_llm",
        lambda config: (_ for _ in ()).throw(AssertionError("LLM must not start")),
    )

    exit_code = cli.main(
        [
            "--config",
            str(tmp_path / "mycode.yaml"),
            "--hook-config",
            str(tmp_path / "missing.hooks.yaml"),
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "Hook 配置错误" in captured.err
    assert "missing.hooks.yaml" in captured.err


def test_cli_hidden_team_worker_argument_delegates_to_worker(monkeypatch):
    cli = _import_cli(monkeypatch)
    captured = {}

    def fake_worker_main(argv):
        captured["argv"] = argv
        return 17

    monkeypatch.setattr(cli.team_worker, "main", fake_worker_main)

    assert cli.main(["--team-worker", "team-a/dev"]) == 17
    assert captured["argv"] == ["team-a/dev"]


def test_run_application_shares_hook_runtime_and_triggers_app_events(
    tmp_path,
    monkeypatch,
) -> None:
    cli = _import_cli(monkeypatch)
    created = {}

    class FakeHookRuntime:
        def __init__(self, *, config, workspace_root, path_guard):
            self.config = config
            self.workspace_root = workspace_root
            self.path_guard = path_guard
            self.contexts = []
            created["hook_runtime"] = self

        async def trigger(self, context):
            self.contexts.append(context)
            return HookTriggerResult(actions=())

        def prompt_blocks(self):
            return ()

        def clear_request_state(self):
            return None

    class FakeAgentLoop:
        def __init__(self, **kwargs):
            created["agent_kwargs"] = kwargs
            created["agent_hook_runtime"] = kwargs["hook_runtime"]

    class FakeChatSession:
        def __init__(self, **kwargs):
            created["session_hook_runtime"] = kwargs["hook_runtime"]

        async def close(self):
            created["session_closed"] = True

    class FakeTUI:
        def __init__(self, **kwargs):
            created["tui_session"] = kwargs["session"]

        async def run(self):
            return 0

    class FakeWorktreeService:
        def __init__(self, workspace_root):
            self.shared_workspace = WorkspaceContext(
                kind=WorkspaceKind.SHARED,
                root=workspace_root,
                repository_root=workspace_root,
                repository_id="repo-123",
                task_identity=None,
                branch_name=None,
                hooks_path=None,
            )

        @classmethod
        def create(cls, workspace_root):
            created["worktree_service_root"] = workspace_root
            service = cls(workspace_root)
            created["worktree_service"] = service
            return service

    class FakeWorktreeCleaner:
        def __init__(self, **kwargs):
            created["cleaner_kwargs"] = kwargs

        async def start(self):
            created["cleaner_started"] = True

        async def close(self):
            created.setdefault("cleanup_order", []).append("cleaner")

    class FakeSubAgentService:
        def __init__(self, **kwargs):
            created["subagent_service_kwargs"] = kwargs

        async def close(self):
            return None

    monkeypatch.setattr(cli, "HookRuntime", FakeHookRuntime)
    monkeypatch.setattr(cli, "create_default_tool_registry", lambda *args, **kwargs: _FakeRegistry())
    monkeypatch.setattr(cli, "create_context_manager", lambda **kwargs: _FakeContextManager())
    monkeypatch.setattr(cli, "create_project_memory_manager", lambda **kwargs: _FakeProjectMemory())
    monkeypatch.setattr(cli, "register_mcp_tools", lambda pool, registry: ())
    monkeypatch.setattr(cli, "SkillLoader", lambda **kwargs: object())
    monkeypatch.setattr(cli, "SkillCatalog", _FakeSkillCatalog)
    monkeypatch.setattr(cli, "SkillRuntime", _FakeSkillRuntime)
    monkeypatch.setattr(cli, "SkillExecutor", lambda **kwargs: object())
    monkeypatch.setattr(cli, "SkillLoadTool", lambda **kwargs: _FakeTool("load_skill"))
    monkeypatch.setattr(cli, "SkillSlashBridge", _FakeSkillSlashBridge)
    monkeypatch.setattr(cli, "SlashCommandDispatcher", lambda registry, **kwargs: SimpleNamespace(registry=registry))
    monkeypatch.setattr(cli, "SlashCommandCompleter", lambda registry, **kwargs: object())
    monkeypatch.setattr(cli, "AgentLoop", FakeAgentLoop)
    monkeypatch.setattr(cli, "ChatSession", FakeChatSession)
    monkeypatch.setattr(cli, "ChatTUI", FakeTUI)
    monkeypatch.setattr(cli, "WorktreeService", FakeWorktreeService, raising=False)
    monkeypatch.setattr(cli, "WorktreeCleaner", FakeWorktreeCleaner, raising=False)
    monkeypatch.setattr(cli, "SubAgentService", FakeSubAgentService)
    monkeypatch.setattr(cli.Path, "home", staticmethod(lambda: tmp_path / "home"))

    permissions = SimpleNamespace(path_guard=PathGuard(tmp_path))
    pool = _FakePool()
    monkeypatch.setattr(cli, "MCPServerPool", lambda config: pool)
    hook_config = HookConfig(version=1, rules=(), path=tmp_path / "hooks.yaml")

    exit_code = asyncio.run(
        cli._run_application(
            config=_fake_config(),
            llm=object(),
            permissions=permissions,
            mcp_config=cli.MCPConfig(()),
            mcp_config_diagnostics=(),
            workspace_root=tmp_path,
            registry=_FakeRegistry(),
            hook_config=hook_config,
        )
    )

    assert exit_code == 0
    assert created["agent_hook_runtime"] is created["hook_runtime"]
    assert created["session_hook_runtime"] is created["hook_runtime"]
    assert created["hook_runtime"].config is hook_config
    assert created["hook_runtime"].path_guard is permissions.path_guard
    assert created["agent_kwargs"]["workspace"].root == tmp_path
    assert created["agent_kwargs"]["workspace"] is created["worktree_service"].shared_workspace
    assert created["subagent_service_kwargs"]["worktree_service"] is created["worktree_service"]
    assert created["cleaner_kwargs"]["worktree_service"] is created["worktree_service"]
    assert created["cleaner_kwargs"]["is_workspace_active"].__self__ is created["subagent_service_kwargs"]["task_manager"]
    assert created["cleaner_started"] is True
    assert created["cleanup_order"][0] == "cleaner"
    assert [context.event for context in created["hook_runtime"].contexts] == [
        HookEvent.APP_STARTED,
        HookEvent.HOOKS_LOADED,
    ]


def test_run_application_registers_all_team_tool_views(tmp_path, monkeypatch):
    cli = _import_cli(monkeypatch)
    created = {}

    class FakeHookRuntime:
        def __init__(self, **kwargs):
            pass

        async def trigger(self, context):
            return HookTriggerResult(actions=())

        def prompt_blocks(self):
            return ()

    class FakeAgentLoop:
        def __init__(self, **kwargs):
            created["visible_provider"] = kwargs["visible_tool_names_provider"]

    class FakeChatSession:
        def __init__(self, **kwargs):
            pass

        async def close(self):
            return None

    class FakeTUI:
        def __init__(self, **kwargs):
            pass

        async def run(self):
            return 0

    class FakeWorktreeService:
        def __init__(self, workspace_root):
            self.shared_workspace = WorkspaceContext(
                kind=WorkspaceKind.SHARED,
                root=workspace_root,
                repository_root=workspace_root,
                repository_id="repo-123",
                task_identity=None,
                branch_name=None,
                hooks_path=None,
            )

        @classmethod
        def create(cls, workspace_root):
            return cls(workspace_root)

    class FakeWorktreeCleaner:
        def __init__(self, **kwargs):
            pass

        async def start(self):
            return None

        async def close(self):
            return None

    class FakeSubAgentService:
        def __init__(self, **kwargs):
            pass

        async def close(self):
            return None

    registry = _FakeRegistry()
    monkeypatch.setattr(cli, "HookRuntime", FakeHookRuntime)
    monkeypatch.setattr(cli, "create_default_tool_registry", lambda *args, **kwargs: registry)
    monkeypatch.setattr(cli, "create_context_manager", lambda **kwargs: _FakeContextManager())
    monkeypatch.setattr(cli, "create_project_memory_manager", lambda **kwargs: _FakeProjectMemory())
    monkeypatch.setattr(cli, "register_mcp_tools", lambda pool, registry: ())
    monkeypatch.setattr(cli, "SkillLoader", lambda **kwargs: object())
    monkeypatch.setattr(cli, "SkillCatalog", _FakeSkillCatalog)
    monkeypatch.setattr(cli, "SkillRuntime", _FakeSkillRuntime)
    monkeypatch.setattr(cli, "SkillExecutor", lambda **kwargs: object())
    monkeypatch.setattr(cli, "SkillLoadTool", lambda **kwargs: _FakeTool("load_skill"))
    monkeypatch.setattr(cli, "SkillSlashBridge", _FakeSkillSlashBridge)
    monkeypatch.setattr(cli, "SlashCommandDispatcher", lambda registry, **kwargs: SimpleNamespace(registry=registry))
    monkeypatch.setattr(cli, "SlashCommandCompleter", lambda registry, **kwargs: object())
    monkeypatch.setattr(cli, "AgentLoop", FakeAgentLoop)
    monkeypatch.setattr(cli, "ChatSession", FakeChatSession)
    monkeypatch.setattr(cli, "ChatTUI", FakeTUI)
    monkeypatch.setattr(cli, "WorktreeService", FakeWorktreeService, raising=False)
    monkeypatch.setattr(cli, "WorktreeCleaner", FakeWorktreeCleaner, raising=False)
    monkeypatch.setattr(cli, "SubAgentService", FakeSubAgentService)
    monkeypatch.setattr(cli.Path, "home", staticmethod(lambda: tmp_path / "home"))
    monkeypatch.setattr(cli, "_current_branch", lambda worktree_service: "main")

    permissions = SimpleNamespace(path_guard=PathGuard(tmp_path))
    monkeypatch.setattr(cli, "MCPServerPool", lambda config: _FakePool())

    exit_code = asyncio.run(
        cli._run_application(
            config=_fake_config(),
            llm=object(),
            permissions=permissions,
            mcp_config=cli.MCPConfig(()),
            mcp_config_diagnostics=(),
            workspace_root=tmp_path,
            registry=_FakeRegistry(),
            hook_config=HookConfig(version=1, rules=(), path=None),
        )
    )

    registered_names = [item.definition.name for item in registry.registered]
    assert exit_code == 0
    assert "team" in registered_names
    assert "team_lead" in registered_names
    assert "team_member" in registered_names
    assert created["visible_provider"] is not None


def test_tui_exit_path_closes_session(tmp_path, monkeypatch):
    _install_import_stubs(monkeypatch)
    sys.modules.pop("mycode.tui", None)
    tui_module = importlib.import_module("mycode.tui")

    class ClosingSession:
        def __init__(self):
            self.close_count = 0

        async def close(self):
            self.close_count += 1

    class ExitDispatcher:
        async def dispatch(self, text, controller):
            return SlashDispatchResult(kind=SlashDispatchKind.EXIT)

    session = ClosingSession()
    tui = tui_module.ChatTUI(
        session=session,
        dispatcher=ExitDispatcher(),
        registry=_FakeRegistry(),
        input_func=lambda: "/exit",
        workspace_root=tmp_path,
    )

    assert asyncio.run(tui.run()) == 0
    assert session.close_count == 1


def test_readme_documents_hook_contract() -> None:
    readme = (Path(__file__).resolve().parents[1] / "README.md").read_text(encoding="utf-8")
    required_fragments = [
        "mycode.hooks.yaml",
        "--hook-config",
        "event",
        "if",
        "action",
        "session_start",
        "session_end",
        "session_clear",
        "model_round_start",
        "model_round_end",
        "user_message",
        "assistant_message",
        "tool_result_message",
        "tool_before",
        "tool_after",
        "app_started",
        "hooks_loaded",
        "runtime_error",
        "all",
        "any",
        "glob:",
        "re:",
        "regex",
        "not: true",
        "!",
        "command",
        "prompt",
        "http",
        "sub_agent",
        "once",
        "background",
        "timeout_seconds",
        "hook_blocked",
        "子 Agent 真实运行",
        "once 持久化",
        "显式优先级",
        "热加载",
    ]

    missing = [fragment for fragment in required_fragments if fragment not in readme]

    assert missing == []


def _import_cli(monkeypatch):
    _install_import_stubs(monkeypatch)
    sys.modules.pop("mycode.cli", None)
    return importlib.import_module("mycode.cli")


def _install_import_stubs(monkeypatch) -> None:
    if "rich.console" not in sys.modules:
        rich_module = types.ModuleType("rich")
        console_module = types.ModuleType("rich.console")

        class Console:
            def __init__(self, *args, file=None, **kwargs):
                self.file = file or sys.stdout

            def print(self, *args, sep=" ", end="\n", **kwargs):
                self.file.write(sep.join(str(item) for item in args) + end)

        console_module.Console = Console
        monkeypatch.setitem(sys.modules, "rich", rich_module)
        monkeypatch.setitem(sys.modules, "rich.console", console_module)
    if "httpx" not in sys.modules:
        httpx_module = types.ModuleType("httpx")

        class HTTPError(Exception):
            pass

        class AsyncByteStream:
            async def __aiter__(self):
                if False:
                    yield b""

            async def aclose(self):
                return None

        class AsyncClient:
            is_closed = False

            def __init__(self, *args, **kwargs):
                pass

            async def aclose(self):
                self.is_closed = True

        class Response:
            pass

        class Headers(dict):
            pass

        httpx_module.HTTPError = HTTPError
        httpx_module.AsyncByteStream = AsyncByteStream
        httpx_module.AsyncClient = AsyncClient
        httpx_module.Response = Response
        httpx_module.Headers = Headers
        monkeypatch.setitem(sys.modules, "httpx", httpx_module)


def _fake_config():
    return SimpleNamespace(
        model="test-model",
        compact=SimpleNamespace(context_window_tokens=128000),
        thinking=SimpleNamespace(show=False),
        sub_agent=SubAgentConfig(
            model_map={
                AgentModelTier.HAIKU: "test-model",
                AgentModelTier.SONNET: "test-model",
                AgentModelTier.OPUS: "test-model",
            },
            background_allowed_tools=("artifact", "memory_note", "load_skill"),
        ),
    )


class _FakeRegistry:
    def __init__(self):
        self.registered = []

    def register(self, item):
        self.registered.append(item)

    def get(self, name):
        for item in self.registered:
            definition = getattr(item, "definition", None)
            if definition is not None and definition.name == name:
                return item
        return None

    def definitions(self):
        return tuple(
            item.definition
            for item in self.registered
            if getattr(item, "definition", None) is not None
        )

    def model_definitions(self, *args, **kwargs):
        return ()

    def deferred_summaries(self):
        return ()

    def public_commands(self):
        return ()


class _FakePermissionFactory:
    def __init__(self, workspace_root: Path) -> None:
        self.workspace_root = workspace_root

    def create(self, workspace_root):
        return SimpleNamespace(path_guard=PathGuard(self.workspace_root))


class _FakeTool:
    def __init__(self, name):
        self.definition = SimpleNamespace(name=name, kind=ToolKind.READ)


class _FakeContextManager:
    def __init__(self):
        self.artifact_tool = _FakeTool("artifact")
        self.closed = False

    def close(self):
        self.closed = True


class _FakeProjectMemory:
    def __init__(self):
        self.memory_note_tool = _FakeTool("memory_note")
        self.closed = False

    async def close(self):
        self.closed = True


class _FakePool:
    tools = ()
    server_names = ()

    async def initialize_all(self):
        return ()

    async def close(self):
        return None

    def add_tools_listener(self, listener):
        return None


class _FakeSkillCatalog:
    def __init__(self, **kwargs):
        pass

    def initialize(self):
        return None


class _FakeSkillRuntime:
    LOAD_TOOL_NAME = "load_skill"

    def __init__(self, catalog):
        pass

    def prompt_blocks(self):
        return ()

    def visible_tool_names(self):
        return None


class _FakeSkillSlashBridge:
    def __init__(self, **kwargs):
        pass

    def refresh(self):
        return ()

    def refresh_silent(self):
        return ()
