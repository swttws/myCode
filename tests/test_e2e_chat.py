from io import StringIO

from rich.console import Console

from mycode import cli
from mycode.llm import BaseLLM, ChatMessage, StreamEvent, StreamEventType
from mycode.tui import ChatTUI


class ScriptedLLM(BaseLLM):
    def __init__(self, scripts):
        self.scripts = list(scripts)
        self.requests: list[list[ChatMessage]] = []

    async def stream_chat(self, messages):
        self.requests.append(list(messages))
        for event in self.scripts.pop(0):
            yield event


def write_config(path):
    path.write_text(
        """
protocol: anthropic
model: claude-test
base_url: https://api.anthropic.test
api_key: sk-test
""",
        encoding="utf-8",
    )


def patch_tui(monkeypatch, inputs, output):
    def fake_tui_factory(*, session, show_thinking):
        console = Console(file=output, force_terminal=False, color_system=None, width=100)
        return ChatTUI(
            session=session,
            console=console,
            input_func=lambda: next(inputs),
            show_thinking=show_thinking,
        )

    monkeypatch.setattr(cli, "ChatTUI", fake_tui_factory)


def test_e2e_cli_tui_session_memory_streams_and_sends_previous_context(tmp_path, monkeypatch):
    config_path = tmp_path / "mycode.yaml"
    write_config(config_path)
    output = StringIO()
    inputs = iter(["hello", "second", "/exit"])
    llm = ScriptedLLM(
        [
            [StreamEvent(StreamEventType.TEXT_DELTA, "hi"), StreamEvent(StreamEventType.DONE)],
            [StreamEvent(StreamEventType.TEXT_DELTA, "again"), StreamEvent(StreamEventType.DONE)],
        ]
    )
    monkeypatch.setattr(cli, "create_llm", lambda config: llm)
    patch_tui(monkeypatch, inputs, output)

    exit_code = cli.main(["--config", str(config_path)])

    assert exit_code == 0
    assert "hi" in output.getvalue()
    assert llm.requests[1] == [
        ChatMessage(role="user", content="hello"),
        ChatMessage(role="assistant", content="hi"),
        ChatMessage(role="user", content="second"),
    ]


def test_e2e_clear_removes_previous_context_before_next_request(tmp_path, monkeypatch):
    config_path = tmp_path / "mycode.yaml"
    write_config(config_path)
    output = StringIO()
    inputs = iter(["hello", "/clear", "after clear", "/exit"])
    llm = ScriptedLLM(
        [
            [StreamEvent(StreamEventType.TEXT_DELTA, "hi"), StreamEvent(StreamEventType.DONE)],
            [StreamEvent(StreamEventType.TEXT_DELTA, "fresh"), StreamEvent(StreamEventType.DONE)],
        ]
    )
    monkeypatch.setattr(cli, "create_llm", lambda config: llm)
    patch_tui(monkeypatch, inputs, output)

    exit_code = cli.main(["--config", str(config_path)])

    assert exit_code == 0
    assert llm.requests[1] == [ChatMessage(role="user", content="after clear")]
