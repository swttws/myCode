import asyncio

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
from mycode.tool import ToolKind


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


def test_cli_loads_config_builds_session_and_runs_tui(tmp_path, monkeypatch):
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

    class FakeAgentLoop:
        def __init__(self, *, llm, memory, tool_executor, tool_registry, permission):
            created["agent_kwargs"] = {
                "llm": llm,
                "memory": memory,
                "tool_executor": tool_executor,
                "tool_registry": tool_registry,
                "permission": permission,
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
    assert created["agent_kwargs"]["tool_registry"].get("read_file")._path_guard is permission_service.path_guard
    assert created["permission_workspace"] == tmp_path
    assert created["session_agent"].__class__ is FakeAgentLoop
    assert created["session_permissions"] is permission_service
    assert created["session"].__class__ is FakeChatSession


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

    class FakeTUI:
        def __init__(self, **kwargs):
            pass

        async def run(self):
            created["tui_loop"] = asyncio.get_running_loop()
            return 0

    monkeypatch.setattr(cli, "create_llm", lambda config: object())
    monkeypatch.setattr(cli, "PermissionService", FakePermissionFactory)
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
    assert callable(created["tools_listener"])
    assert created["registry"].get("remote__echo") is not None
    assert created["registry"].get("tool_search") is not None
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

    class FakePool:
        tools = ()

        def add_tools_listener(self, listener):
            pass

        async def initialize_all(self):
            return ()

        async def close(self):
            closed.append(asyncio.get_running_loop())

    class FakeTUI:
        def __init__(self, **kwargs):
            pass

        async def run(self):
            raise RuntimeError("tui failed")

    monkeypatch.setattr(cli, "create_llm", lambda config: object())
    monkeypatch.setattr(cli, "ChatTUI", FakeTUI)
    monkeypatch.setattr(cli, "load_mcp_config", lambda *args, **kwargs: (MCPConfig(()), ()))
    monkeypatch.setattr(cli, "MCPServerPool", lambda config: FakePool())

    with pytest.raises(RuntimeError, match="tui failed"):
        cli.main(["--config", str(primary)])

    assert len(closed) == 1


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
