from io import StringIO

import pytest
from rich.console import Console

from mycode.agent import (
    AgentEvent,
    AgentEventType,
)
from mycode import tui as tui_module
from mycode.compact.models import (
    CompactAction,
    CompactFailureCode,
    CompactReport,
    CompactStatus,
)
from mycode.permission.models import (
    ApprovalDecisionType,
    ApprovalRequest,
    PermissionDecision,
    PermissionEffect,
    PermissionMode,
    RuleSource,
)
from mycode.tui import ChatTUI
from mycode.tool import ToolCall, ToolResult


class FakeSession:
    def __init__(self, scripts=None, compact_scripts=None):
        self.scripts = list(scripts or [])
        self.compact_scripts = list(compact_scripts or [])
        self.sent: list[str] = []
        self.send_kwargs = []
        self.compact_count = 0
        self.clear_count = 0
        self.plan_only = False
        self.permission = (PermissionMode.DEFAULT, None)

    async def send(self, user_text, **kwargs):
        self.sent.append(user_text)
        self.send_kwargs.append(kwargs)
        for event in self.scripts.pop(0):
            yield event

    async def compact(self):
        self.compact_count += 1
        for event in self.compact_scripts.pop(0):
            yield event

    def clear(self):
        self.clear_count += 1

    def set_plan_only(self, enabled):
        self.plan_only = enabled

    def is_plan_only(self):
        return self.plan_only

    def permission_mode(self):
        return self.permission

    def set_permission_mode(self, mode):
        self.permission = (mode, RuleSource.SESSION)


def approval_request(*, options=None, plan_only=True):
    call = ToolCall(id="call-1", name="write_file", arguments={"path": "README.md"})
    return ApprovalRequest(
        id="approval-call-1",
        tool_call=call,
        decision=PermissionDecision(
            effect=PermissionEffect.ASK,
            reason_code="plan_only_write" if plan_only else "risky_workspace_delete",
            message_zh="该写操作需要人工确认。",
            mode=PermissionMode.DEFAULT,
            display_arguments={"path": "README.md", "token": "<已脱敏>"},
            source=RuleSource.REPOSITORY_PROJECT,
            rule_id="review-write",
        ),
        options=options
        or (
            ApprovalDecisionType.APPROVE_ONCE,
            ApprovalDecisionType.REJECT,
            ApprovalDecisionType.CANCEL,
        ),
        candidate_grant=None,
        plan_only=plan_only,
        round_index=1,
    )


def make_console():
    output = StringIO()
    console = Console(file=output, force_terminal=False, color_system=None, width=100)
    return console, output


def compact_report(
    *,
    status=CompactStatus.COMPACTED,
    actions=(CompactAction.HEAVY,),
    before=80_000,
    after=20_000,
    archived=3,
    attempts=1,
    circuit=False,
    failure_code=None,
    message="",
):
    return CompactReport(
        status=status,
        actions=actions,
        before_tokens=before,
        after_tokens=after,
        archived_count=archived,
        attempts=attempts,
        circuit_open=circuit,
        failure_code=failure_code,
        message_zh=message,
    )


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


def test_tui_announces_stage_07_context_management():
    console, output = make_console()
    session = FakeSession()
    inputs = iter(["/exit"])
    tui = ChatTUI(session=session, console=console, input_func=lambda: next(inputs))

    import asyncio

    assert asyncio.run(tui.run()) == 0
    text = output.getvalue()
    assert "Stage 07" in text
    assert "Agent" in text
    assert "/compact" in text
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


def test_tui_compact_command_uses_session_compact_without_send():
    console, output = make_console()
    report = compact_report(message="手动压缩完成。")
    session = FakeSession(
        compact_scripts=[[AgentEvent(AgentEventType.COMPACTION, compaction=report)]]
    )
    inputs = iter(["/compact", "/exit"])
    tui = ChatTUI(session=session, console=console, input_func=lambda: next(inputs))

    import asyncio

    assert asyncio.run(tui.run()) == 0
    assert session.compact_count == 1
    assert session.sent == []
    assert "手动压缩完成" in output.getvalue()


@pytest.mark.parametrize(
    ("report", "expected"),
    [
        (
            compact_report(message="上下文已压缩。"),
            ("上下文已压缩", "归档 3", "80000 → 20000"),
        ),
        (
            compact_report(
                status=CompactStatus.NO_OP,
                actions=(CompactAction.NONE,),
                before=12_000,
                after=12_000,
                archived=0,
                attempts=0,
                message="没有可压缩的旧历史。",
            ),
            ("无需压缩", "没有可压缩的旧历史"),
        ),
        (
            compact_report(
                actions=(CompactAction.HEAVY, CompactAction.EMERGENCY),
                attempts=3,
                circuit=True,
                message="已执行应急压缩。",
            ),
            ("应急压缩", "熔断已打开", "尝试 3"),
        ),
        (
            compact_report(
                status=CompactStatus.FAILED,
                actions=(CompactAction.HEAVY,),
                before=80_000,
                after=80_000,
                archived=0,
                attempts=3,
                failure_code=CompactFailureCode.ARCHIVE_ERROR,
                message="归档写入失败。",
            ),
            ("压缩失败", "archive_error", "归档写入失败"),
        ),
        (
            compact_report(
                actions=(CompactAction.EMERGENCY,),
                attempts=0,
                circuit=True,
                message="摘要熔断中，已执行本地应急压缩。",
            ),
            ("熔断已打开", "应急压缩"),
        ),
    ],
)
def test_tui_renders_compaction_status_in_chinese(report, expected):
    console, output = make_console()
    session = FakeSession(
        [[AgentEvent(AgentEventType.COMPACTION, compaction=report)]]
    )
    inputs = iter(["hello", "/exit"])
    tui = ChatTUI(session=session, console=console, input_func=lambda: next(inputs))

    import asyncio

    assert asyncio.run(tui.run()) == 0
    text = output.getvalue()
    for snippet in expected:
        assert snippet in text


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


def test_tui_permission_status_shows_mode_and_source_without_llm_request():
    console, output = make_console()
    session = FakeSession()
    session.permission = (PermissionMode.STRICT, RuleSource.LOCAL_PROJECT)
    inputs = iter(["/permission", "/exit"])

    import asyncio

    assert asyncio.run(ChatTUI(session=session, console=console, input_func=lambda: next(inputs)).run()) == 0
    assert session.sent == []
    assert "严格" in output.getvalue()
    assert "本地项目" in output.getvalue()


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ("/permission strict", PermissionMode.STRICT),
        ("/permission default", PermissionMode.DEFAULT),
        ("/permission permissive", PermissionMode.PERMISSIVE),
    ],
)
def test_tui_permission_command_sets_session_mode_without_llm(command, expected):
    console, output = make_console()
    session = FakeSession()
    inputs = iter([command, "/exit"])

    import asyncio

    assert asyncio.run(ChatTUI(session=session, console=console, input_func=lambda: next(inputs)).run()) == 0
    assert session.permission == (expected, RuleSource.SESSION)
    assert session.sent == []
    assert "已设置" in output.getvalue()


def test_tui_invalid_permission_command_prints_chinese_usage_without_llm():
    console, output = make_console()
    session = FakeSession()
    inputs = iter(["/permission unsafe", "/exit"])

    import asyncio

    assert asyncio.run(ChatTUI(session=session, console=console, input_func=lambda: next(inputs)).run()) == 0
    assert session.sent == []
    assert "用法" in output.getvalue()
    assert "strict|default|permissive" in output.getvalue()


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
    assert "工具请求" in text


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
    request = approval_request()

    import asyncio

    decision = asyncio.run(tui._approval_provider(request))

    assert decision.type == ApprovalDecisionType.APPROVE_ONCE


def test_tui_approval_provider_accepts_no():
    console, _ = make_console()
    inputs = iter(["n"])
    tui = ChatTUI(session=FakeSession(), console=console, input_func=lambda: next(inputs))
    request = approval_request()

    import asyncio

    decision = asyncio.run(tui._approval_provider(request))

    assert decision.type == ApprovalDecisionType.REJECT


def test_tui_approval_provider_accepts_cancel():
    console, _ = make_console()
    inputs = iter(["c"])
    tui = ChatTUI(session=FakeSession(), console=console, input_func=lambda: next(inputs))
    request = approval_request()

    import asyncio

    decision = asyncio.run(tui._approval_provider(request))

    assert decision.type == ApprovalDecisionType.CANCEL


@pytest.mark.parametrize(
    ("answer", "expected"),
    [
        ("o", ApprovalDecisionType.APPROVE_ONCE),
        ("y", ApprovalDecisionType.APPROVE_ONCE),
        ("s", ApprovalDecisionType.APPROVE_SESSION),
        ("p", ApprovalDecisionType.APPROVE_PROJECT),
        ("n", ApprovalDecisionType.REJECT),
        ("c", ApprovalDecisionType.CANCEL),
    ],
)
def test_tui_approval_provider_supports_scoped_options(answer, expected):
    console, output = make_console()
    tui = ChatTUI(session=FakeSession(), console=console, input_func=lambda: answer)
    request = approval_request(
        plan_only=False,
        options=(
            ApprovalDecisionType.APPROVE_ONCE,
            ApprovalDecisionType.APPROVE_SESSION,
            ApprovalDecisionType.APPROVE_PROJECT,
            ApprovalDecisionType.REJECT,
            ApprovalDecisionType.CANCEL,
        ),
    )

    import asyncio

    decision = asyncio.run(tui._approval_provider(request))

    assert decision.type is expected
    rendered = output.getvalue()
    assert "该写操作需要人工确认" in rendered
    assert "README.md" in rendered
    assert "<已脱敏>" in rendered
    assert "仓库项目" in rendered


def test_tui_rejects_session_scope_when_plan_only_does_not_offer_it():
    console, output = make_console()
    tui = ChatTUI(session=FakeSession(), console=console, input_func=lambda: "s")

    import asyncio

    decision = asyncio.run(tui._approval_provider(approval_request()))

    assert decision.type is ApprovalDecisionType.CANCEL
    assert "无效" in output.getvalue()


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
