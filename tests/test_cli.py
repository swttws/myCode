from mycode import cli


def write_config(path, text):
    path.write_text(text, encoding="utf-8")


def test_cli_loads_config_builds_session_and_runs_tui(tmp_path, monkeypatch):
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

    class FakeAgentLoop:
        def __init__(self, *, llm, memory, tool_executor, tool_registry):
            created["agent_kwargs"] = {
                "llm": llm,
                "memory": memory,
                "tool_executor": tool_executor,
                "tool_registry": tool_registry,
            }

    class FakeChatSession:
        def __init__(self, *, agent):
            created["session_agent"] = agent

    def fake_create_llm(config):
        created["config"] = config
        return FakeLLM()

    monkeypatch.setattr(cli, "create_llm", fake_create_llm)
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
    assert created["session_agent"].__class__ is FakeAgentLoop
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
