from __future__ import annotations

import importlib

from mycode.llm import ChatMessage


def test_select_recent_messages_keeps_tail_within_token_budget():
    history = [_ascii_message(index) for index in range(12)]

    recent = _module().select_recent_messages(
        history,
        keep_recent_tokens=10_000,
        min_recent_messages=1,
    )

    assert recent == tuple(history[-10:])


def test_select_recent_messages_keeps_at_least_minimum_count_when_budget_is_too_small():
    history = [_ascii_message(index) for index in range(8)]

    recent = _module().select_recent_messages(
        history,
        keep_recent_tokens=1,
        min_recent_messages=5,
    )

    assert recent == tuple(history[-5:])


def test_select_recent_messages_returns_all_history_when_shorter_than_minimum():
    history = [
        ChatMessage(role="user", content="first"),
        ChatMessage(role="assistant", content="second"),
        ChatMessage(role="user", content="third"),
    ]

    recent = _module().select_recent_messages(history, keep_recent_tokens=1, min_recent_messages=5)

    assert recent == tuple(history)


def test_select_recent_messages_includes_exact_budget_boundary():
    history = [_ascii_message(index) for index in range(5)]

    recent = _module().select_recent_messages(
        history,
        keep_recent_tokens=3_000,
        min_recent_messages=1,
    )

    assert recent == tuple(history[-3:])


def test_select_recent_messages_closes_tool_call_group_when_result_is_retained():
    history = [
        ChatMessage(role="user", content="old" * 1000),
        ChatMessage(role="assistant", content="", tool_call_id="call-a", tool_name="read_a", tool_arguments="{}"),
        ChatMessage(role="assistant", content="", tool_call_id="call-b", tool_name="read_b", tool_arguments="{}"),
        ChatMessage(role="tool", content="A" * 1000, tool_call_id="call-a"),
        ChatMessage(role="tool", content="B", tool_call_id="call-b"),
        ChatMessage(role="assistant", content="after"),
        ChatMessage(role="user", content="latest"),
    ]

    recent = _module().select_recent_messages(
        history,
        keep_recent_tokens=6,
        min_recent_messages=1,
    )

    assert recent == tuple(history[1:])


def _module():
    return importlib.import_module("mycode.compact.summary")


def _ascii_message(index):
    return ChatMessage(role="user", content=f"{index:04d}" + ("a" * 3_996))
