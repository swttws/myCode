import asyncio
from types import SimpleNamespace

import pytest

from mycode import cli
from mycode.mcp import (
    MCPConfig,
    MCPConfigError,
    MCPDiagnostic,
    MCPServerConfig,
    MCPTransportKind,
    RemoteTool,
)
from mycode.permission.models import PermissionConfigError
from mycode.permission.pathing import PathGuard
from mycode.tool import ToolDefinition, ToolKind


def write_config(path, text):
    path.write_text(text, encoding="utf-8")


def write_primary_config(path):
    write_config(
        path,
        """
protocol: anthropic
model: claude-test
base_url: https://api.anthropic.test
api_key: sk-test
compact:
  context_window_tokens: 128000
""",
    )


class FakeCompactArtifactTool:
    @property
    def definition(self):
        return ToolDefinition(
            name="read_compact_artifact",
            description="Read compact artifact.",
            parameters={"type": "object", "properties": {}, "required": []},
            kind=ToolKind.READ,
        )


class FakeContextManager:
    def __init__(self, operations=None):
        self.artifact_tool = FakeCompactArtifactTool()
        self.operations = operations
        self.closed = False

    def close(self):
        self.closed = True
        if self.operations is not None:
            self.operations.append("context_close")


def test_cli_loads_config_builds_session_and_runs_tui(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(cli.Path, "home", staticmethod(lambda: home))
    config_path = tmp_path / "mycode.yaml"
    write_config(
        config_path,
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
    )
    created = {}

    class FakeLLM:
        pass

    class FakeTUI:
        def __init__(self, *, session, show_thinking):
            created["session"] = session
            created["show_thinking"] = show_thinking

        async def run(self):
            return 0

    class FakePermissionService:
        def __init__(self):
            self.path_guard = PathGuard(tmp_path)

    permission_service = FakePermissionService()

    class FakePermissionFactory:
        @classmethod
        def create(cls, workspace_root, **kwargs):
            created["permission_workspace"] = workspace_root
            return permission_service

    fake_context = FakeContextManager()

    def fake_create_context_manager(**kwargs):
        created["context_kwargs"] = kwargs
        return fake_context

    class FakeAgentLoop:
        def __init__(
            self,
            *,
            llm,
            memory,
            tool_executor,
            tool_registry,
            permission,
            context_manager,
            config,
            project_memory=None,
        ):
            created["agent_kwargs"] = {
                "llm": llm,
                "memory": memory,
                "tool_executor": tool_executor,
                "tool_registry": tool_registry,
                "permission": permission,
                "context_manager": context_manager,
                "config": config,
                "project_memory": project_memory,
            }

    class FakeChatSession:
        def __init__(self, *, agent, permissions):
            created["session_agent"] = agent
            created["session_permissions"] = permissions

    def fake_create_llm(config):
        created["config"] = config
        return FakeLLM()

    monkeypatch.setattr(cli, "create_llm", fake_create_llm)
    monkeypatch.setattr(cli, "PermissionService", FakePermissionFactory)
    monkeypatch.setattr(cli, "create_context_manager", fake_create_context_manager, raising=False)
    monkeypatch.setattr(cli, "AgentLoop", FakeAgentLoop)
    monkeypatch.setattr(cli, "ChatSession", FakeChatSession)
    monkeypatch.setattr(cli, "ChatTUI", FakeTUI)

    exit_code = cli.main(["--config", str(config_path)])

    assert exit_code == 0
    assert created["config"].protocol == "anthropic"
    assert created["show_thinking"] is True
    assert created["agent_kwargs"]["llm"].__class__ is FakeLLM
    assert created["agent_kwargs"]["memory"] is not None
    assert created["agent_kwargs"]["tool_executor"] is not None
    assert created["agent_kwargs"]["tool_registry"] is not None
    assert created["agent_kwargs"]["permission"]._service is permission_service
    assert created["agent_kwargs"]["context_manager"] is fake_context
    assert created["context_kwargs"]["workspace_root"] == tmp_path
    assert created["context_kwargs"]["home"] == home
    assert created["context_kwargs"]["llm"].__class__ is FakeLLM
    assert created["context_kwargs"]["memory"] is created["agent_kwargs"]["memory"]
    assert created["context_kwargs"]["config"] is created["config"].compact
    assert created["context_kwargs"]["model_timeout_seconds"] is None
    assert created["agent_kwargs"]["tool_registry"].get("read_file")._path_guard is permission_service.path_guard
    assert created["agent_kwargs"]["tool_registry"].get("read_compact_artifact") is fake_context.artifact_tool
    assert created["permission_workspace"] == tmp_path
    assert created["session_agent"].__class__ is FakeAgentLoop
    assert created["session_permissions"] is permission_service
    assert created["session"].__class__ is FakeChatSession


def test_cli_builds_project_memory_and_passes_it_into_agent_loop(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(cli.Path, "home", staticmethod(lambda: home))
    config_path = tmp_path / "mycode.yaml"
    write_primary_config(config_path)
    created = {}

    class FakeLLM:
        pass

    class FakeProjectMemory:
        async def close(self):
            return None

    fake_project_memory = FakeProjectMemory()

    class FakePermissionService:
        def __init__(self):
            self.path_guard = PathGuard(tmp_path)

    class FakePermissionFactory:
        @classmethod
        def create(cls, workspace_root, **kwargs):
            return FakePermissionService()

    class FakeContextManager:
        def __init__(self):
            self.artifact_tool = FakeCompactArtifactTool()

        def close(self):
            created["context_closed"] = True

    class FakePool:
        tools = ()

        def add_tools_listener(self, listener):
            pass

        async def initialize_all(self):
            return ()

        async def close(self):
            created["pool_closed"] = True

    class FakeAgentLoop:
        def __init__(self, *, project_memory=None, **kwargs):
            created["agent_project_memory"] = project_memory

    class FakeTUI:
        def __init__(self, **kwargs):
            pass

        async def run(self):
            return 0

    def fake_create_project_memory_manager(**kwargs):
        created["project_kwargs"] = kwargs
        return fake_project_memory

    monkeypatch.setattr(cli, "create_llm", lambda config: FakeLLM())
    monkeypatch.setattr(cli, "PermissionService", FakePermissionFactory)
    monkeypatch.setattr(cli, "create_project_memory_manager", fake_create_project_memory_manager)
    monkeypatch.setattr(cli, "create_context_manager", lambda **kwargs: FakeContextManager(), raising=False)
    monkeypatch.setattr(cli, "AgentLoop", FakeAgentLoop)
    monkeypatch.setattr(cli, "ChatTUI", FakeTUI)
    monkeypatch.setattr(cli, "load_mcp_config", lambda *args, **kwargs: (MCPConfig(()), ()))
    monkeypatch.setattr(cli, "MCPServerPool", lambda config: FakePool())

    exit_code = cli.main(["--config", str(config_path)])

    assert exit_code == 0
    assert created["project_kwargs"]["workspace_root"] == tmp_path
    assert created["project_kwargs"]["home"] == home
    assert created["agent_project_memory"] is fake_project_memory


def test_cli_returns_error_before_tui_when_config_is_invalid(tmp_path, capsys):
    config_path = tmp_path / "mycode.yaml"
    write_config(
        config_path,
        """
protocol: anthropic
model: claude-test
base_url: https://api.anthropic.test
api_key: ${MYCODE_MISSING_SECRET}
""",
    )

    exit_code = cli.main(["--config", str(config_path)])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "MYCODE_MISSING_SECRET" in captured.err
    assert "sk-" not in captured.err


def test_cli_returns_chinese_error_before_tui_when_permission_config_is_invalid(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.chdir(tmp_path)
    config_path = tmp_path / "mycode.yaml"
    write_config(
        config_path,
        """
protocol: anthropic
model: claude-test
base_url: https://api.anthropic.test
api_key: sk-test
compact:
  context_window_tokens: 128000
""",
    )

    class FailingPermissionFactory:
        @classmethod
        def create(cls, workspace_root, **kwargs):
            raise PermissionConfigError("仓库权限配置包含禁止字段")

    class ForbiddenTUI:
        def __init__(self, **kwargs):
            raise AssertionError("权限配置失败后不能启动 TUI")

    monkeypatch.setattr(cli, "create_llm", lambda config: object())
    monkeypatch.setattr(cli, "PermissionService", FailingPermissionFactory)
    monkeypatch.setattr(cli, "ChatTUI", ForbiddenTUI)

    exit_code = cli.main(["--config", str(config_path)])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "权限配置错误" in captured.err
    assert "禁止字段" in captured.err


def test_cli_parser_accepts_mcp_config_with_primary_config(tmp_path):
    args = cli.build_parser().parse_args(
        ["--config", str(tmp_path / "main.yaml"), "--mcp-config", str(tmp_path / "mcp.yaml")]
    )

    assert args.config == tmp_path / "main.yaml"
    assert args.mcp_config == tmp_path / "mcp.yaml"


def test_cli_initializes_registers_reports_and_closes_mcp_in_same_loop(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.chdir(tmp_path)
    primary = tmp_path / "mycode.yaml"
    write_primary_config(primary)
    server_config = MCPServerConfig(
        name="remote",
        transport=MCPTransportKind.STDIO,
        timeout_seconds=1,
        command="unused",
        args=(),
        env={},
        url=None,
        headers={},
        read_tools=frozenset(),
    )
    remote_tool = RemoteTool(
        server_name="remote",
        remote_name="echo",
        public_name="remote__echo",
        description="Remote echo.",
        parameters={"type": "object", "properties": {}},
        kind=ToolKind.WRITE,
    )
    created = {}
    operations = []

    class FakePool:
        tools = (remote_tool,)

        def add_tools_listener(self, listener):
            created["tools_listener"] = listener

        async def initialize_all(self):
            created["initialize_loop"] = asyncio.get_running_loop()
            return (
                MCPDiagnostic(
                    "broken",
                    "connection",
                    "safe failure",
                    transport=MCPTransportKind.STDIO,
                ),
            )

        async def close(self):
            created["close_loop"] = asyncio.get_running_loop()
            created["closed"] = True
            operations.append("pool_close")

        def is_available(self, server_name):
            return True

        async def ensure_available(self, server_name):
            return True

        async def call_tool(self, server_name, remote_name, arguments):
            raise AssertionError("CLI lifecycle test should not call tools")

    pool = FakePool()

    class FakePermissionService:
        def __init__(self):
            self.path_guard = PathGuard(tmp_path)

    class FakePermissionFactory:
        @classmethod
        def create(cls, workspace_root, **kwargs):
            return FakePermissionService()

    class FakeAgentLoop:
        def __init__(self, **kwargs):
            created["registry"] = kwargs["tool_registry"]
            created["context_manager"] = kwargs["context_manager"]

    class FakeTUI:
        def __init__(self, **kwargs):
            pass

        async def run(self):
            created["tui_loop"] = asyncio.get_running_loop()
            return 0

    monkeypatch.setattr(cli, "create_llm", lambda config: object())
    monkeypatch.setattr(cli, "PermissionService", FakePermissionFactory)
    fake_context = FakeContextManager(operations)
    monkeypatch.setattr(
        cli,
        "create_context_manager",
        lambda **kwargs: fake_context,
        raising=False,
    )
    monkeypatch.setattr(cli, "AgentLoop", FakeAgentLoop)
    monkeypatch.setattr(cli, "ChatTUI", FakeTUI)
    monkeypatch.setattr(
        cli,
        "load_mcp_config",
        lambda *args, **kwargs: (
            MCPConfig((server_config,)),
            (
                MCPDiagnostic(
                    "invalid",
                    "config",
                    "safe config issue",
                    transport=MCPTransportKind.STREAMABLE_HTTP,
                ),
            ),
        ),
    )
    monkeypatch.setattr(cli, "MCPServerPool", lambda config: pool)

    exit_code = cli.main(["--config", str(primary), "--mcp-config", "mcp.yaml"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert created["initialize_loop"] is created["tui_loop"] is created["close_loop"]
    assert created["closed"] is True
    assert fake_context.closed is True
    assert operations == ["context_close", "pool_close"]
    assert callable(created["tools_listener"])
    assert created["registry"].get("remote__echo") is not None
    assert created["registry"].get("tool_search") is not None
    assert created["registry"].get("read_compact_artifact") is fake_context.artifact_tool
    assert created["context_manager"] is fake_context
    assert "safe config issue" in captured.err
    assert "safe failure" in captured.err
    assert "config" in captured.err
    assert "streamable_http" in captured.err
    assert "connection" in captured.err
    assert "stdio" in captured.err


def test_cli_closes_mcp_pool_when_tui_raises(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    primary = tmp_path / "mycode.yaml"
    write_primary_config(primary)
    closed = []
    operations = []

    class FakePool:
        tools = ()

        def add_tools_listener(self, listener):
            pass

        async def initialize_all(self):
            return ()

        async def close(self):
            closed.append(asyncio.get_running_loop())
            operations.append("pool_close")

    class FakeTUI:
        def __init__(self, **kwargs):
            pass

        async def run(self):
            raise RuntimeError("tui failed")

    monkeypatch.setattr(cli, "create_llm", lambda config: object())
    fake_context = FakeContextManager(operations)
    monkeypatch.setattr(
        cli,
        "create_context_manager",
        lambda **kwargs: fake_context,
        raising=False,
    )
    monkeypatch.setattr(cli, "ChatTUI", FakeTUI)
    monkeypatch.setattr(cli, "load_mcp_config", lambda *args, **kwargs: (MCPConfig(()), ()))
    monkeypatch.setattr(cli, "MCPServerPool", lambda config: FakePool())

    with pytest.raises(RuntimeError, match="tui failed"):
        cli.main(["--config", str(primary)])

    assert len(closed) == 1
    assert fake_context.closed is True
    assert operations == ["context_close", "pool_close"]


def test_cli_closes_context_and_pool_when_mcp_initialize_raises(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    operations = []

    class FakePool:
        tools = ()

        async def initialize_all(self):
            operations.append("initialize")
            raise RuntimeError("mcp init failed")

        async def close(self):
            operations.append("pool_close")

    class FakePermissionService:
        path_guard = PathGuard(tmp_path)

    fake_context = FakeContextManager(operations)
    monkeypatch.setattr(cli, "MCPServerPool", lambda config: FakePool())
    monkeypatch.setattr(
        cli,
        "create_context_manager",
        lambda **kwargs: fake_context,
        raising=False,
    )

    with pytest.raises(RuntimeError, match="mcp init failed"):
        asyncio.run(
            cli._run_application(
                config=SimpleNamespace(compact=object()),
                llm=object(),
                permissions=FakePermissionService(),
                mcp_config=MCPConfig(()),
                mcp_config_diagnostics=(),
            )
        )

    assert fake_context.closed is True
    assert operations == ["initialize", "context_close", "pool_close"]


def test_cli_returns_error_when_context_cache_creation_fails(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.chdir(tmp_path)
    primary = tmp_path / "mycode.yaml"
    write_primary_config(primary)
    operations = []

    class FakePool:
        tools = ()

        async def initialize_all(self):
            return ()

        async def close(self):
            operations.append("pool_close")

    class FakePermissionService:
        path_guard = PathGuard(tmp_path)

    class FakePermissionFactory:
        @classmethod
        def create(cls, workspace_root, **kwargs):
            return FakePermissionService()

    class FakeAgentLoop:
        def __init__(self, **kwargs):
            pass

    class FakeTUI:
        def __init__(self, **kwargs):
            pass

        async def run(self):
            return 0

    monkeypatch.setattr(cli, "create_llm", lambda config: object())
    monkeypatch.setattr(cli, "PermissionService", FakePermissionFactory)
    monkeypatch.setattr(cli, "MCPServerPool", lambda config: FakePool())
    monkeypatch.setattr(cli, "load_mcp_config", lambda *args, **kwargs: (MCPConfig(()), ()))
    monkeypatch.setattr(
        cli,
        "create_context_manager",
        lambda **kwargs: (_ for _ in ()).throw(OSError("cache unavailable")),
        raising=False,
    )
    monkeypatch.setattr(cli, "AgentLoop", FakeAgentLoop)
    monkeypatch.setattr(cli, "ChatTUI", FakeTUI)

    exit_code = cli.main(["--config", str(primary)])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "上下文缓存错误" in captured.err
    assert "cache unavailable" in captured.err
    assert operations == ["pool_close"]


def test_cli_returns_error_before_tui_for_explicit_invalid_mcp_config(
    tmp_path, monkeypatch, capsys
):
    primary = tmp_path / "mycode.yaml"
    write_primary_config(primary)

    class ForbiddenTUI:
        def __init__(self, **kwargs):
            raise AssertionError("invalid MCP config must stop before TUI")

    monkeypatch.setattr(cli, "create_llm", lambda config: object())
    monkeypatch.setattr(
        cli,
        "load_mcp_config",
        lambda *args, **kwargs: (_ for _ in ()).throw(MCPConfigError("MCP file not found")),
        raising=False,
    )
    monkeypatch.setattr(cli, "ChatTUI", ForbiddenTUI)

    exit_code = cli.main(["--config", str(primary), "--mcp-config", "missing.yaml"])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "MCP 配置错误" in captured.err
    assert "MCP file not found" in captured.err
