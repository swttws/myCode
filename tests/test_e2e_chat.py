from io import StringIO

from rich.console import Console

from mycode import cli
from mycode.agent import AgentConfig
from mycode.llm import BaseLLM, ChatMessage, StreamEvent, StreamEventType
from mycode.tui import ChatTUI
from mycode.tool import ToolCall


class ScriptedLLM(BaseLLM):
    def __init__(self, scripts):
        self.scripts = list(scripts)
        self.requests: list[list[ChatMessage]] = []
        self.tool_requests = []

    async def stream_chat(self, messages, tools=None):
        self.requests.append(list(messages))
        self.tool_requests.append(tools)
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
        ChatMessage(role="system", content=AgentConfig().minimal_system_prompt),
        ChatMessage(role="user", content="hello"),
        ChatMessage(role="assistant", content="hi"),
        ChatMessage(role="user", content="second"),
    ]
    assert llm.tool_requests[0] is not None


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
    assert llm.requests[1] == [
        ChatMessage(role="system", content=AgentConfig().minimal_system_prompt),
        ChatMessage(role="user", content="after clear"),
    ]


def test_e2e_tool_call_result_is_stored_for_next_request(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    config_path = tmp_path / "mycode.yaml"
    write_config(config_path)
    (tmp_path / "note.txt").write_text("tool text", encoding="utf-8")
    output = StringIO()
    inputs = iter(["read note", "/exit"])
    llm = ScriptedLLM(
        [
            [
                StreamEvent(
                    StreamEventType.TOOL_CALL,
                    tool_call=ToolCall(
                        id="call-1",
                        name="read_file",
                        arguments={"path": "note.txt"},
                        raw_arguments='{"path":"note.txt"}',
                    ),
                ),
                StreamEvent(StreamEventType.DONE),
            ],
            [StreamEvent(StreamEventType.TEXT_DELTA, "final"), StreamEvent(StreamEventType.DONE)],
        ]
    )
    monkeypatch.setattr(cli, "create_llm", lambda config: llm)
    patch_tui(monkeypatch, inputs, output)

    exit_code = cli.main(["--config", str(config_path)])

    assert exit_code == 0
    assert "工具已执行" in output.getvalue()
    second_request = llm.requests[1]
    assert second_request[0] == ChatMessage(role="system", content=AgentConfig().minimal_system_prompt)
    assert second_request[1] == ChatMessage(role="user", content="read note")
    assert second_request[2] == ChatMessage(
        role="assistant",
        content="",
        tool_call_id="call-1",
        tool_name="read_file",
        tool_arguments='{"path":"note.txt"}',
    )
    assert second_request[3].role == "tool"
    assert second_request[3].tool_call_id == "call-1"
    assert "tool text" in second_request[3].content


def test_e2e_failed_edit_tool_call_returns_structured_error_and_continues(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    config_path = tmp_path / "mycode.yaml"
    write_config(config_path)
    (tmp_path / "note.txt").write_text("same\nsame\n", encoding="utf-8")
    output = StringIO()
    inputs = iter(["edit note", "/exit"])
    llm = ScriptedLLM(
        [
            [
                StreamEvent(
                    StreamEventType.TOOL_CALL,
                    tool_call=ToolCall(
                        id="call-1",
                        name="edit_file",
                        arguments={
                            "path": "note.txt",
                            "old_text": "same",
                            "new_text": "changed",
                        },
                        raw_arguments='{"path":"note.txt","old_text":"same","new_text":"changed"}',
                    ),
                ),
                StreamEvent(StreamEventType.DONE),
            ],
            [StreamEvent(StreamEventType.TEXT_DELTA, "still here"), StreamEvent(StreamEventType.DONE)],
        ]
    )
    monkeypatch.setattr(cli, "create_llm", lambda config: llm)
    patch_tui(monkeypatch, inputs, output)

    exit_code = cli.main(["--config", str(config_path)])

    assert exit_code == 0
    text = output.getvalue()
    assert "工具失败" in text
    assert "expected exactly one match, found 2" in text
    assert "still here" in text
    assert (tmp_path / "note.txt").read_text(encoding="utf-8") == "same\nsame\n"
    assert len(llm.requests) == 2


def test_e2e_next_turn_sends_previous_tool_history_to_llm(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    config_path = tmp_path / "mycode.yaml"
    write_config(config_path)
    (tmp_path / "note.txt").write_text("tool text", encoding="utf-8")
    output = StringIO()
    inputs = iter(["read note", "summarize result", "/exit"])
    llm = ScriptedLLM(
        [
            [
                StreamEvent(
                    StreamEventType.TOOL_CALL,
                    tool_call=ToolCall(
                        id="call-1",
                        name="read_file",
                        arguments={"path": "note.txt"},
                        raw_arguments='{"path":"note.txt"}',
                    ),
                ),
                StreamEvent(StreamEventType.DONE),
            ],
            [StreamEvent(StreamEventType.TEXT_DELTA, "summary"), StreamEvent(StreamEventType.DONE)],
            [StreamEvent(StreamEventType.TEXT_DELTA, "next"), StreamEvent(StreamEventType.DONE)],
        ]
    )
    monkeypatch.setattr(cli, "create_llm", lambda config: llm)
    patch_tui(monkeypatch, inputs, output)

    exit_code = cli.main(["--config", str(config_path)])

    assert exit_code == 0
    assert len(llm.requests) == 3
    second_request = llm.requests[1]
    assert second_request[0] == ChatMessage(role="system", content=AgentConfig().minimal_system_prompt)
    assert second_request[1] == ChatMessage(role="user", content="read note")
    assert second_request[2] == ChatMessage(
        role="assistant",
        content="",
        tool_call_id="call-1",
        tool_name="read_file",
        tool_arguments='{"path":"note.txt"}',
    )
    assert second_request[3].role == "tool"
    assert second_request[3].tool_call_id == "call-1"
    assert "tool text" in second_request[3].content
    third_request = llm.requests[2]
    assert third_request[-2] == ChatMessage(role="assistant", content="summary")
    assert third_request[-1] == ChatMessage(role="user", content="summarize result")


def test_e2e_clear_removes_tool_history_before_next_request(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    config_path = tmp_path / "mycode.yaml"
    write_config(config_path)
    (tmp_path / "note.txt").write_text("tool text", encoding="utf-8")
    output = StringIO()
    inputs = iter(["read note", "/clear", "after clear", "/exit"])
    llm = ScriptedLLM(
        [
            [
                StreamEvent(
                    StreamEventType.TOOL_CALL,
                    tool_call=ToolCall(
                        id="call-1",
                        name="read_file",
                        arguments={"path": "note.txt"},
                        raw_arguments='{"path":"note.txt"}',
                    ),
                ),
                StreamEvent(StreamEventType.DONE),
            ],
            [StreamEvent(StreamEventType.TEXT_DELTA, "done"), StreamEvent(StreamEventType.DONE)],
            [StreamEvent(StreamEventType.TEXT_DELTA, "fresh"), StreamEvent(StreamEventType.DONE)],
        ]
    )
    monkeypatch.setattr(cli, "create_llm", lambda config: llm)
    patch_tui(monkeypatch, inputs, output)

    exit_code = cli.main(["--config", str(config_path)])

    assert exit_code == 0
    assert llm.requests[2] == [
        ChatMessage(role="system", content=AgentConfig().minimal_system_prompt),
        ChatMessage(role="user", content="after clear"),
    ]
