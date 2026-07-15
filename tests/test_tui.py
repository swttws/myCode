from io import StringIO

from rich.console import Console

from mycode.agent import (
    AgentEvent,
    AgentEventType,
    ApprovalDecisionType,
    ApprovalRequest,
)
from mycode import tui as tui_module
from mycode.tui import ChatTUI
from mycode.tool import ToolCall, ToolResult


class FakeSession:
    def __init__(self, scripts=None):
        self.scripts = list(scripts or [])
        self.sent: list[str] = []
        self.send_kwargs = []
        self.clear_count = 0
        self.plan_only = False

    async def send(self, user_text, **kwargs):
        self.sent.append(user_text)
        self.send_kwargs.append(kwargs)
        for event in self.scripts.pop(0):
            yield event

    def clear(self):
        self.clear_count += 1

    def set_plan_only(self, enabled):
        self.plan_only = enabled

    def is_plan_only(self):
        return self.plan_only


def make_console():
    output = StringIO()
    console = Console(file=output, force_terminal=False, color_system=None, width=100)
    return console, output


def test_tui_streams_assistant_text_and_exits():
    console, output = make_console()
    session = FakeSession(
        [[AgentEvent(AgentEventType.TEXT_DELTA, "hi"), AgentEvent(AgentEventType.FINAL_RESPONSE, "hi")]]
    )
    inputs = iter(["hello", "/exit"])
    tui = ChatTUI(session=session, console=console, input_func=lambda: next(inputs))

    import asyncio

    exit_code = asyncio.run(tui.run())

    assert exit_code == 0
    assert session.sent == ["hello"]
    assert "hi" in output.getvalue()


def test_tui_announces_stage_03_agent_mode():
    console, output = make_console()
    session = FakeSession()
    inputs = iter(["/exit"])
    tui = ChatTUI(session=session, console=console, input_func=lambda: next(inputs))

    import asyncio

    assert asyncio.run(tui.run()) == 0
    text = output.getvalue()
    assert "Stage 03" in text
    assert "Agent" in text
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


def test_tui_plan_only_status_command_does_not_send_to_llm():
    console, output = make_console()
    session = FakeSession()
    session.set_plan_only(True)
    inputs = iter(["/plan-only", "/exit"])
    tui = ChatTUI(session=session, console=console, input_func=lambda: next(inputs))

    import asyncio

    assert asyncio.run(tui.run()) == 0
    assert session.sent == []
    assert "开启" in output.getvalue()


def test_tui_plan_only_on_command_enables_mode():
    console, output = make_console()
    session = FakeSession()
    inputs = iter(["/plan-only on", "/exit"])
    tui = ChatTUI(session=session, console=console, input_func=lambda: next(inputs))

    import asyncio

    assert asyncio.run(tui.run()) == 0
    assert session.plan_only is True
    assert "开启" in output.getvalue()


def test_tui_plan_only_off_command_disables_mode():
    console, output = make_console()
    session = FakeSession()
    session.set_plan_only(True)
    inputs = iter(["/plan-only off", "/exit"])
    tui = ChatTUI(session=session, console=console, input_func=lambda: next(inputs))

    import asyncio

    assert asyncio.run(tui.run()) == 0
    assert session.plan_only is False
    assert "关闭" in output.getvalue()


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
    session = FakeSession([[AgentEvent(AgentEventType.ERROR, "network failed")]])
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
                AgentEvent(AgentEventType.THINKING_DELTA, "hidden"),
                AgentEvent(AgentEventType.TEXT_DELTA, "visible"),
                AgentEvent(AgentEventType.FINAL_RESPONSE, "visible"),
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
                AgentEvent(AgentEventType.THINKING_DELTA, "thinking"),
                AgentEvent(AgentEventType.FINAL_RESPONSE, ""),
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


def test_tui_prints_tool_call_started():
    console, output = make_console()
    session = FakeSession(
        [[AgentEvent(AgentEventType.TOOL_CALL_STARTED, tool_call=ToolCall(id="call-1", name="read_file", arguments={}))]]
    )
    inputs = iter(["hello", "/exit"])
    tui = ChatTUI(session=session, console=console, input_func=lambda: next(inputs))

    import asyncio

    assert asyncio.run(tui.run()) == 0
    text = output.getvalue()
    assert "read_file" in text
    assert "开始" in text


def test_tui_prints_successful_tool_result():
    console, output = make_console()
    session = FakeSession(
        [[AgentEvent(AgentEventType.TOOL_RESULT, tool_result=ToolResult(True, "read_file", {}))]]
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
                AgentEvent(
                    AgentEventType.TOOL_RESULT,
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


def test_tui_prints_cancelled_event():
    console, output = make_console()
    session = FakeSession([[AgentEvent(AgentEventType.CANCELLED, "cancelled")]])
    inputs = iter(["hello", "/exit"])
    tui = ChatTUI(session=session, console=console, input_func=lambda: next(inputs))

    import asyncio

    assert asyncio.run(tui.run()) == 0
    assert "取消" in output.getvalue()


def test_tui_send_passes_approval_provider():
    console, _ = make_console()
    session = FakeSession([[AgentEvent(AgentEventType.FINAL_RESPONSE, "")]])
    inputs = iter(["hello", "/exit"])
    tui = ChatTUI(session=session, console=console, input_func=lambda: next(inputs))

    import asyncio

    assert asyncio.run(tui.run()) == 0
    assert session.send_kwargs[0]["approval_provider"] == tui._approval_provider


def test_tui_approval_provider_accepts_yes():
    console, _ = make_console()
    inputs = iter(["y"])
    tui = ChatTUI(session=FakeSession(), console=console, input_func=lambda: next(inputs))
    request = ApprovalRequest(
        id="approval-call-1",
        tool_call=ToolCall(id="call-1", name="write_file", arguments={}),
        reason="plan-only",
        plan_only=True,
        round_index=1,
    )

    import asyncio

    decision = asyncio.run(tui._approval_provider(request))

    assert decision.type == ApprovalDecisionType.APPROVE_ONCE


def test_tui_approval_provider_accepts_no():
    console, _ = make_console()
    inputs = iter(["n"])
    tui = ChatTUI(session=FakeSession(), console=console, input_func=lambda: next(inputs))
    request = ApprovalRequest(
        id="approval-call-1",
        tool_call=ToolCall(id="call-1", name="write_file", arguments={}),
        reason="plan-only",
        plan_only=True,
        round_index=1,
    )

    import asyncio

    decision = asyncio.run(tui._approval_provider(request))

    assert decision.type == ApprovalDecisionType.REJECT


def test_tui_approval_provider_accepts_cancel():
    console, _ = make_console()
    inputs = iter(["c"])
    tui = ChatTUI(session=FakeSession(), console=console, input_func=lambda: next(inputs))
    request = ApprovalRequest(
        id="approval-call-1",
        tool_call=ToolCall(id="call-1", name="write_file", arguments={}),
        reason="plan-only",
        plan_only=True,
        round_index=1,
    )

    import asyncio

    decision = asyncio.run(tui._approval_provider(request))

    assert decision.type == ApprovalDecisionType.CANCEL


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
