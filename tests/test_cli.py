from mycode import cli
from mycode.permission.models import PermissionConfigError
from mycode.permission.pathing import PathGuard


def write_config(path, text):
    path.write_text(text, encoding="utf-8")


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
