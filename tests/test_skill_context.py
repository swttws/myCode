from __future__ import annotations

import pytest

from mycode.agent.history import (
    make_assistant_text_message,
    make_assistant_tool_call_message,
    make_tool_result_message,
    make_user_message,
)
from mycode.compact.models import CompactStatus
from mycode.skill.context import (
    EphemeralContextManager,
    SkillContextTooLarge,
    build_summary_prompt,
    select_completed_turns,
)
from mycode.tool import ToolCall, ToolResult


def test_select_completed_turns_returns_recent_closed_user_turns():
    history = (
        make_user_message("u1"),
        make_assistant_text_message("a1"),
        make_user_message("u2"),
        make_assistant_text_message("a2"),
        make_user_message("u3"),
        make_assistant_text_message("a3"),
    )

    selected = select_completed_turns(history, 2)

    assert [message.content for message in selected] == ["u2", "a2", "u3", "a3"]


def test_select_completed_turns_excludes_incomplete_tool_chains_and_current_turn():
    call = ToolCall(id="call-1", name="read_file", arguments={})
    history = (
        make_user_message("done"),
        make_assistant_tool_call_message(call),
        make_tool_result_message(call, ToolResult(ok=True, tool_name="read_file", content={"text": "ok"})),
        make_assistant_text_message("final"),
        make_user_message("incomplete"),
        make_assistant_tool_call_message(ToolCall(id="call-2", name="read_file", arguments={})),
        make_user_message("current"),
    )

    selected = select_completed_turns(history, 3)

    assert [message.content for message in selected] == [
        "done",
        "",
        '{"ok": true, "tool_name": "read_file", "content": {"text": "ok"}, "error": null}',
        "final",
    ]


def test_build_summary_prompt_contains_fixed_chinese_constraints():
    prompt = build_summary_prompt((make_user_message("目标"), make_assistant_text_message("进展")))

    assert "用户目标" in prompt
    assert "已确认约束" in prompt
    assert "关键技术事实" in prompt
    assert "不要添加新结论" in prompt
    assert "目标" in prompt


def test_ephemeral_context_manager_builds_request_without_archiving_or_usage_state():
    memory = [make_user_message("hello")]
    manager = EphemeralContextManager(memory, max_chars=100)

    prepared = manager.prepare_now(build_request=lambda history: {"history": history})

    assert prepared.request == {"history": tuple(memory)}
    assert prepared.report.status is CompactStatus.SAFE
    assert manager.record_usage_calls == []
    manager.record_usage(prepared.snapshot, object())
    assert len(manager.record_usage_calls) == 1
    manager.clear()
    assert memory == []


def test_ephemeral_context_manager_rejects_context_that_is_too_large():
    manager = EphemeralContextManager([make_user_message("x" * 101)], max_chars=100)

    with pytest.raises(SkillContextTooLarge):
        manager.prepare_now(build_request=lambda history: {"history": history})
