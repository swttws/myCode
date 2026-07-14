from io import StringIO

from rich.console import Console

from mycode import tui as tui_module
from mycode.llm import StreamEvent, StreamEventType
from mycode.tui import ChatTUI
from mycode.tool import ToolResult


class FakeSession:
    def __init__(self, scripts=None):
        self.scripts = list(scripts or [])
        self.sent: list[str] = []
        self.clear_count = 0

    async def send(self, user_text):
        self.sent.append(user_text)
        for event in self.scripts.pop(0):
            yield event

    def clear(self):
        self.clear_count += 1


def make_console():
    output = StringIO()
    console = Console(file=output, force_terminal=False, color_system=None, width=100)
    return console, output


def test_tui_streams_assistant_text_and_exits():
    console, output = make_console()
    session = FakeSession(
        [[StreamEvent(StreamEventType.TEXT_DELTA, "hi"), StreamEvent(StreamEventType.DONE)]]
    )
    inputs = iter(["hello", "/exit"])
    tui = ChatTUI(session=session, console=console, input_func=lambda: next(inputs))

    import asyncio

    exit_code = asyncio.run(tui.run())

    assert exit_code == 0
    assert session.sent == ["hello"]
    assert "hi" in output.getvalue()


def test_tui_announces_stage_02_tool_mode():
    console, output = make_console()
    session = FakeSession()
    inputs = iter(["/exit"])
    tui = ChatTUI(session=session, console=console, input_func=lambda: next(inputs))

    import asyncio

    assert asyncio.run(tui.run()) == 0
    text = output.getvalue()
    assert "Stage 02" in text
    assert "工具" in text
    assert "纯对话模式" not in text


def test_tui_clear_command_clears_memory_without_llm_request():
    console, _ = make_console()
    session = FakeSession()
    inputs = iter(["/clear", "/exit"])
    tui = ChatTUI(session=session, console=console, input_func=lambda: next(inputs))

    import asyncio

    assert asyncio.run(tui.run()) == 0
    assert session.clear_count == 1
    assert session.sent == []


def test_tui_ignores_empty_input():
    console, _ = make_console()
    session = FakeSession()
    inputs = iter(["", "   ", "/exit"])
    tui = ChatTUI(session=session, console=console, input_func=lambda: next(inputs))

    import asyncio

    assert asyncio.run(tui.run()) == 0
    assert session.sent == []


def test_tui_prints_error_event_and_continues():
    console, output = make_console()
    session = FakeSession([[StreamEvent(StreamEventType.ERROR, "network failed")]])
    inputs = iter(["hello", "/exit"])
    tui = ChatTUI(session=session, console=console, input_func=lambda: next(inputs))

    import asyncio

    assert asyncio.run(tui.run()) == 0
    assert "network failed" in output.getvalue()


def test_tui_hides_thinking_by_default():
    console, output = make_console()
    session = FakeSession(
        [
            [
                StreamEvent(StreamEventType.THINKING_DELTA, "hidden"),
                StreamEvent(StreamEventType.TEXT_DELTA, "visible"),
                StreamEvent(StreamEventType.DONE),
            ]
        ]
    )
    inputs = iter(["hello", "/exit"])
    tui = ChatTUI(session=session, console=console, input_func=lambda: next(inputs))

    import asyncio

    assert asyncio.run(tui.run()) == 0
    text = output.getvalue()
    assert "visible" in text
    assert "hidden" not in text


def test_tui_can_show_thinking_when_enabled():
    console, output = make_console()
    session = FakeSession(
        [
            [
                StreamEvent(StreamEventType.THINKING_DELTA, "thinking"),
                StreamEvent(StreamEventType.DONE),
            ]
        ]
    )
    inputs = iter(["hello", "/exit"])
    tui = ChatTUI(
        session=session,
        console=console,
        input_func=lambda: next(inputs),
        show_thinking=True,
    )

    import asyncio

    assert asyncio.run(tui.run()) == 0
    assert "thinking" in output.getvalue()


def test_tui_prints_successful_tool_result():
    console, output = make_console()
    session = FakeSession(
        [[StreamEvent(StreamEventType.TOOL_RESULT, tool_result=ToolResult(True, "read_file", {}))]]
    )
    inputs = iter(["hello", "/exit"])
    tui = ChatTUI(session=session, console=console, input_func=lambda: next(inputs))

    import asyncio

    assert asyncio.run(tui.run()) == 0
    text = output.getvalue()
    assert "read_file" in text
    assert "已执行" in text


def test_tui_prints_failed_tool_result():
    console, output = make_console()
    session = FakeSession(
        [
            [
                StreamEvent(
                    StreamEventType.TOOL_RESULT,
                    tool_result=ToolResult(False, "edit_file", {}, "expected exactly one match"),
                )
            ]
        ]
    )
    inputs = iter(["hello", "/exit"])
    tui = ChatTUI(session=session, console=console, input_func=lambda: next(inputs))

    import asyncio

    assert asyncio.run(tui.run()) == 0
    text = output.getvalue()
    assert "edit_file" in text
    assert "expected exactly one match" in text


def test_tui_falls_back_to_plain_input_when_prompt_toolkit_has_no_console(monkeypatch):
    class NoConsolePromptSession:
        def __init__(self):
            raise tui_module.NoConsoleScreenBufferError

    monkeypatch.setattr(tui_module, "PromptSession", NoConsolePromptSession)
    monkeypatch.setattr("builtins.input", lambda prompt: "/exit")

    console, _ = make_console()
    session = FakeSession()
    tui = ChatTUI(session=session, console=console)

    import asyncio

    assert asyncio.run(tui._read_input()) == "/exit"
