from __future__ import annotations

import asyncio
from pathlib import Path

from mycode import cli
from mycode.mcp import MCPConfig
from mycode.permission.pathing import PathGuard
from mycode.slash import SlashCommandRegistrationError


def _write_config(path: Path) -> None:
    path.write_text(
        """
protocol: anthropic
model: claude-test
base_url: https://api.anthropic.test
api_key: sk-test
compact:
  context_window_tokens: 128000
thinking:
  show: true
""",
        encoding="utf-8",
    )


def test_cli_builds_slash_stack_and_injects_it_into_tui(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(cli.Path, "home", staticmethod(lambda: home))
    monkeypatch.setattr(cli.Path, "cwd", staticmethod(lambda: tmp_path))

    config_path = tmp_path / "mycode.yaml"
    _write_config(config_path)

    created = {}

    class FakeRegistry:
        def __init__(self):
            self.registered = []

        def register(self, item):
            self.registered.append(item)

        def get(self, name):
            for item in self.registered:
                definition = getattr(item, "definition", None)
                if definition is not None and getattr(definition, "name", None) == name:
                    return item
            return None

        def unregister(self, name):
            for index, item in enumerate(list(self.registered)):
                definition = getattr(item, "definition", None)
                if definition is not None and getattr(definition, "name", None) == name:
                    del self.registered[index]
                    return True
            return False

        def definitions(self):
            return tuple(
                item.definition
                for item in self.registered
                if getattr(item, "definition", None) is not None
            )

        def model_definitions(self):
            return ()

        def deferred_summaries(self):
            return ()

    fake_registry = FakeRegistry()

    class FakeDispatcher:
        def __init__(self, registry):
            created["dispatcher_registry"] = registry
            self.registry = registry

    class FakeCompleter:
        def __init__(self, registry):
            created["completer_registry"] = registry

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

    class FakeToolRegistry(FakeRegistry):
        pass

    class FakeContextManager:
        def __init__(self):
            self.artifact_tool = object()
            self.closed = False

        def close(self):
            self.closed = True

    class FakeProjectMemory:
        def __init__(self):
            self.memory_note_tool = object()

        async def close(self):
            return None

    class FakePool:
        tools = ()

        def __init__(self):
            self.listener = None
            self.closed = False

        def add_tools_listener(self, listener):
            self.listener = listener

        async def initialize_all(self):
            created["initialize_loop"] = asyncio.get_running_loop()
            return ()

        async def close(self):
            self.closed = True
            created["pool_closed"] = True

    class FakeAgentLoop:
        def __init__(self, **kwargs):
            created["agent_kwargs"] = kwargs

    class FakeChatSession:
        def __init__(self, *, agent, permissions):
            created["session_agent"] = agent
            created["session_permissions"] = permissions

    class FakeTUI:
        def __init__(self, **kwargs):
            created["tui_kwargs"] = kwargs

        async def run(self):
            created["tui_loop"] = asyncio.get_running_loop()
            return 0

    def fake_create_llm(config):
        created["config"] = config
        return FakeLLM()

    def fake_create_default_slash_registry():
        created["registry_created"] = True
        return fake_registry

    def fake_create_default_tool_registry(workspace_root, *, path_guard):
        created["tool_workspace"] = workspace_root
        created["tool_path_guard"] = path_guard
        return FakeToolRegistry()

    def fake_create_context_manager(**kwargs):
        created["context_kwargs"] = kwargs
        return FakeContextManager()

    def fake_create_project_memory_manager(**kwargs):
        created["project_kwargs"] = kwargs
        return FakeProjectMemory()

    monkeypatch.setattr(cli, "create_llm", fake_create_llm)
    monkeypatch.setattr(cli, "create_default_slash_registry", fake_create_default_slash_registry)
    monkeypatch.setattr(cli, "SlashCommandDispatcher", FakeDispatcher)
    monkeypatch.setattr(cli, "SlashCommandCompleter", FakeCompleter)
    monkeypatch.setattr(cli, "PermissionService", FakePermissionFactory)
    monkeypatch.setattr(cli, "create_default_tool_registry", fake_create_default_tool_registry)
    monkeypatch.setattr(cli, "create_context_manager", fake_create_context_manager, raising=False)
    monkeypatch.setattr(cli, "create_project_memory_manager", fake_create_project_memory_manager)
    monkeypatch.setattr(cli, "MCPServerPool", lambda config: FakePool())
    monkeypatch.setattr(cli, "AgentLoop", FakeAgentLoop)
    monkeypatch.setattr(cli, "ChatSession", FakeChatSession)
    monkeypatch.setattr(cli, "ChatTUI", FakeTUI)
    monkeypatch.setattr(cli, "load_mcp_config", lambda *args, **kwargs: (MCPConfig(()), ()))

    exit_code = cli.main(["--config", str(config_path)])

    assert exit_code == 0
    assert created["registry_created"] is True
    assert created["dispatcher_registry"] is fake_registry
    assert created["completer_registry"] is fake_registry
    assert created["permission_workspace"] == tmp_path
    assert created["tool_workspace"] == tmp_path
    assert created["tool_path_guard"].workspace_root == tmp_path
    assert created["context_kwargs"]["workspace_root"] == tmp_path
    assert created["project_kwargs"]["workspace_root"] == tmp_path
    assert created["agent_kwargs"]["tool_registry"].__class__ is FakeToolRegistry
    assert created["tui_kwargs"]["dispatcher"].registry is fake_registry
    assert created["tui_kwargs"]["registry"] is fake_registry
    assert created["tui_kwargs"]["completer"].__class__ is FakeCompleter
    assert isinstance(created["tui_kwargs"]["mcp_pool"], FakePool)
    assert created["tui_kwargs"]["workspace_root"] == tmp_path
    assert created["tui_kwargs"]["show_thinking"] is True
    assert created["session_agent"].__class__ is FakeAgentLoop
    assert created["session_permissions"].path_guard.workspace_root == tmp_path


def test_cli_returns_error_when_slash_registry_registration_fails(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.chdir(tmp_path)
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(cli.Path, "home", staticmethod(lambda: home))
    monkeypatch.setattr(cli.Path, "cwd", staticmethod(lambda: tmp_path))

    config_path = tmp_path / "mycode.yaml"
    _write_config(config_path)

    class ForbiddenPermissionService:
        @classmethod
        def create(cls, workspace_root, **kwargs):
            raise AssertionError("permission service must not start after registry failure")

    class ForbiddenTUI:
        def __init__(self, **kwargs):
            raise AssertionError("TUI must not start after registry failure")

    monkeypatch.setattr(cli, "create_llm", lambda config: object())
    monkeypatch.setattr(
        cli,
        "create_default_slash_registry",
        lambda: (_ for _ in ()).throw(
            SlashCommandRegistrationError("duplicate slash command identifier 'help'")
        ),
    )
    monkeypatch.setattr(cli, "PermissionService", ForbiddenPermissionService)
    monkeypatch.setattr(cli, "ChatTUI", ForbiddenTUI)

    exit_code = cli.main(["--config", str(config_path)])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "slash 命令注册冲突" in captured.err
    assert "help" in captured.err
