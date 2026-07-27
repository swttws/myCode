from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from mycode import cli
from mycode.mcp import MCPConfig
from mycode.permission.pathing import PathGuard
from mycode.skill.models import SkillStartupError
from mycode.tool import ToolKind


def _write_config(path: Path) -> None:
    path.write_text(
        """
protocol: anthropic
model: claude-test
base_url: https://api.anthropic.test
api_key: sk-test
compact:
  context_window_tokens: 128000
""",
        encoding="utf-8",
    )


class _FakeTool:
    def __init__(self, name: str) -> None:
        self.definition = SimpleNamespace(name=name, kind=ToolKind.READ)


class _FakeToolRegistry:
    def __init__(self, events: list[tuple[str, object]]) -> None:
        self._events = events
        self._tools: list[object] = []

    def register(self, tool) -> None:
        self._tools.append(tool)
        self._events.append(("register_tool", getattr(tool.definition, "name", type(tool).__name__)))

    def definitions(self):
        return tuple(tool.definition for tool in self._tools)

    def model_definitions(self, **kwargs):
        return ()

    def deferred_summaries(self):
        return ()


def test_cli_wires_skill_stack_after_tool_registration_and_before_agent(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(cli.Path, "home", staticmethod(lambda: home))
    monkeypatch.setattr(cli.Path, "cwd", staticmethod(lambda: tmp_path))

    config_path = tmp_path / "mycode.yaml"
    _write_config(config_path)

    events: list[tuple[str, object]] = []
    created: dict[str, object] = {}

    class FakeSlashRegistry:
        _static_commands = (
            SimpleNamespace(name="help", aliases=("h",)),
            SimpleNamespace(name="clear", aliases=("cls",)),
            SimpleNamespace(name="exit", aliases=("quit",)),
        )

        def replace_dynamic_commands(self, commands):
            created["dynamic_commands"] = tuple(commands)

    class FakeDispatcher:
        def __init__(self, registry, *, before_dispatch=None):
            created["dispatcher_registry"] = registry
            created["before_dispatch"] = before_dispatch
            self.registry = registry

    class FakeCompleter:
        def __init__(self, registry, *, before_complete=None):
            created["completer_registry"] = registry
            created["before_complete"] = before_complete

    class FakeLLM:
        pass

    class FakePermissionService:
        def __init__(self):
            self.path_guard = PathGuard(tmp_path)

    class FakePermissionFactory:
        @classmethod
        def create(cls, workspace_root, **kwargs):
            created["permission_workspace"] = workspace_root
            return FakePermissionService()

    class FakeContextManager:
        def __init__(self):
            self.artifact_tool = _FakeTool("artifact")
            self.closed = False

        def close(self):
            self.closed = True

    class FakeProjectMemory:
        def __init__(self):
            self.memory_note_tool = _FakeTool("memory_note")

        async def close(self):
            created["project_memory_closed"] = True

    class FakePool:
        tools = ()

        async def initialize_all(self):
            events.append(("mcp_initialized", None))
            return ()

        async def close(self):
            created["pool_closed"] = True

    class FakeSkillLoader:
        def __init__(self, **kwargs):
            created["loader_kwargs"] = kwargs

    class FakeSkillCatalog:
        def __init__(self, *, loader, tool_names, reserved_slash_names):
            created["catalog_loader"] = loader
            created["reserved_slash_names"] = reserved_slash_names
            self._tool_names = tool_names

        def initialize(self):
            events.append(("catalog_initialize", self._tool_names()))
            return SimpleNamespace(definitions=(), diagnostics=(), generation=1)

        def snapshot(self):
            return SimpleNamespace(definitions=(), diagnostics=(), generation=1)

    class FakeSkillRuntime:
        LOAD_TOOL_NAME = "load_skill"

        def __init__(self, catalog):
            created["runtime_catalog"] = catalog

    class FakeSkillExecutor:
        def __init__(self, **kwargs):
            created["skill_executor_kwargs"] = kwargs

    class FakeSkillLoadTool:
        def __init__(self, *, runtime, executor):
            created["load_tool_runtime"] = runtime
            created["load_tool_executor"] = executor
            self.definition = SimpleNamespace(name="load_skill")

    class FakeSkillSlashBridge:
        def __init__(self, *, runtime, registry):
            created["bridge_runtime"] = runtime
            created["bridge_registry"] = registry

        def refresh(self):
            events.append(("bridge_refresh", None))
            return ()

        def refresh_silent(self):
            events.append(("bridge_refresh_silent", None))

    class FakeAgentLoop:
        def __init__(self, **kwargs):
            events.append(("agent_created", None))
            created["agent_kwargs"] = kwargs

    class FakeChatSession:
        def __init__(self, **kwargs):
            created["session_kwargs"] = kwargs

    class FakeTUI:
        def __init__(self, **kwargs):
            created["tui_kwargs"] = kwargs

        async def run(self):
            return 0

    def fake_create_default_tool_registry(workspace_root, *, path_guard):
        created["tool_workspace"] = workspace_root
        created["tool_path_guard"] = path_guard
        registry = _FakeToolRegistry(events)
        registry.register(_FakeTool("read_file"))
        return registry

    def fake_register_mcp_tools(pool, tool_registry):
        events.append(("register_mcp_tools", None))
        tool_registry.register(_FakeTool("mcp_tool"))

    monkeypatch.setattr(cli, "create_llm", lambda config: FakeLLM())
    monkeypatch.setattr(cli, "create_default_slash_registry", lambda: FakeSlashRegistry())
    monkeypatch.setattr(cli, "SlashCommandDispatcher", FakeDispatcher)
    monkeypatch.setattr(cli, "SlashCommandCompleter", FakeCompleter)
    monkeypatch.setattr(cli, "PermissionService", FakePermissionFactory)
    monkeypatch.setattr(cli, "create_default_tool_registry", fake_create_default_tool_registry)
    monkeypatch.setattr(cli, "create_context_manager", lambda **kwargs: FakeContextManager(), raising=False)
    monkeypatch.setattr(cli, "create_project_memory_manager", lambda **kwargs: FakeProjectMemory())
    monkeypatch.setattr(cli, "MCPServerPool", lambda config: FakePool())
    monkeypatch.setattr(cli, "register_mcp_tools", fake_register_mcp_tools)
    monkeypatch.setattr(cli, "SkillLoader", FakeSkillLoader, raising=False)
    monkeypatch.setattr(cli, "SkillCatalog", FakeSkillCatalog, raising=False)
    monkeypatch.setattr(cli, "SkillRuntime", FakeSkillRuntime, raising=False)
    monkeypatch.setattr(cli, "SkillExecutor", FakeSkillExecutor, raising=False)
    monkeypatch.setattr(cli, "SkillLoadTool", FakeSkillLoadTool, raising=False)
    monkeypatch.setattr(cli, "SkillSlashBridge", FakeSkillSlashBridge, raising=False)
    monkeypatch.setattr(cli, "AgentLoop", FakeAgentLoop)
    monkeypatch.setattr(cli, "ChatSession", FakeChatSession)
    monkeypatch.setattr(cli, "ChatTUI", FakeTUI)
    monkeypatch.setattr(cli, "load_mcp_config", lambda *args, **kwargs: (MCPConfig(()), ()))

    exit_code = cli.main(["--config", str(config_path)])

    assert exit_code == 0
    assert events.index(("register_mcp_tools", None)) < events.index(("register_tool", "load_skill"))
    initialize_event = next(event for event in events if event[0] == "catalog_initialize")
    assert {"read_file", "artifact", "memory_note", "mcp_tool", "load_skill"}.issubset(initialize_event[1])
    assert events.index(("register_tool", "load_skill")) < events.index(("catalog_initialize", initialize_event[1]))
    assert events.index(("catalog_initialize", initialize_event[1])) < events.index(("agent_created", None))
    assert created["reserved_slash_names"] == frozenset({"help", "h", "clear", "cls", "exit", "quit"})
    assert created["loader_kwargs"]["workspace_root"] == tmp_path
    assert created["loader_kwargs"]["home"] == home
    assert created["skill_executor_kwargs"]["llm_factory"] is cli.create_llm
    assert created["agent_kwargs"]["skill_runtime"] is created["load_tool_runtime"]
    assert created["session_kwargs"]["skill_runtime"] is created["load_tool_runtime"]
    assert created["session_kwargs"]["skill_executor"] is created["load_tool_executor"]
    assert created["before_dispatch"].__name__ == "refresh"
    assert created["before_complete"].__name__ == "refresh_silent"
    assert created["before_dispatch"].__self__ is created["before_complete"].__self__
    assert created["tui_kwargs"]["dispatcher"].registry is created["bridge_registry"]
    assert created["tui_kwargs"]["completer"].__class__ is FakeCompleter


def test_cli_returns_error_when_skill_startup_validation_fails(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(cli.Path, "home", staticmethod(lambda: home))
    monkeypatch.setattr(cli.Path, "cwd", staticmethod(lambda: tmp_path))

    config_path = tmp_path / "mycode.yaml"
    _write_config(config_path)

    class FakePermissionService:
        path_guard = PathGuard(tmp_path)

    class FakeContextManager:
        artifact_tool = _FakeTool("artifact")

        def close(self):
            pass

    class FakeProjectMemory:
        memory_note_tool = _FakeTool("memory_note")

        async def close(self):
            pass

    class FakePool:
        async def initialize_all(self):
            return ()

        async def close(self):
            pass

    class FakeSkillCatalog:
        def __init__(self, **kwargs):
            pass

        def initialize(self):
            raise SkillStartupError("bad: 未知工具 ghost")

        def snapshot(self):
            return SimpleNamespace(definitions=(), diagnostics=(), generation=0)

    class ForbiddenAgentLoop:
        def __init__(self, **kwargs):
            raise AssertionError("Agent must not start after skill startup failure")

    monkeypatch.setattr(cli, "create_llm", lambda config: object())
    monkeypatch.setattr(cli, "PermissionService", SimpleNamespace(create=lambda workspace_root: FakePermissionService()))
    monkeypatch.setattr(cli, "create_context_manager", lambda **kwargs: FakeContextManager(), raising=False)
    monkeypatch.setattr(cli, "create_project_memory_manager", lambda **kwargs: FakeProjectMemory())
    monkeypatch.setattr(cli, "MCPServerPool", lambda config: FakePool())
    monkeypatch.setattr(cli, "register_mcp_tools", lambda pool, registry: None)
    monkeypatch.setattr(cli, "SkillCatalog", FakeSkillCatalog, raising=False)
    monkeypatch.setattr(cli, "AgentLoop", ForbiddenAgentLoop)
    monkeypatch.setattr(cli, "load_mcp_config", lambda *args, **kwargs: (MCPConfig(()), ()))

    exit_code = cli.main(["--config", str(config_path)])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "Skill" in captured.err
    assert "bad" in captured.err
    assert "ghost" in captured.err
