from __future__ import annotations

import importlib
import json

from mycode.compact.archive import ArchiveSession
from mycode.llm import ChatMessage, MessageOrigin


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


def test_build_compacted_history_preserves_old_users_and_summarizes_old_model_messages(tmp_path):
    old_user = ChatMessage(role="user", content="逐字保留用户要求")
    old_assistant = ChatMessage(role="assistant", content="旧回答")
    old_tool = ChatMessage(role="tool", content="旧工具结果", tool_call_id="call-1")
    recent = ChatMessage(role="user", content="近期问题")
    history = [old_user, old_assistant, old_tool, recent]
    session = ArchiveSession(tmp_path / "workspace", home=tmp_path / "home", session_id="session")
    transaction = session.begin()
    module = _module()

    assert module.summary_input_messages(history, [recent]) == (old_assistant, old_tool)

    result = module.build_compacted_history(
        history,
        recent_messages=[recent],
        summary="正式摘要",
        transaction=transaction,
    )

    assert result.history[0] is old_user
    assert result.history[1] == ChatMessage(
        role="assistant",
        content="正式摘要",
        origin=MessageOrigin.COMPACT_SUMMARY,
    )
    assert result.history[2].role == "user"
    assert result.history[2].origin is MessageOrigin.COMPACT_BOUNDARY
    assert "重新读取归档" in result.history[2].content
    assert result.history[3] is recent
    assert result.artifacts == ()

    transaction.rollback()
    session.close()


def test_build_compacted_history_archives_earliest_old_user_when_user_budget_blocks_recovery(tmp_path):
    first_user = ChatMessage(role="user", content="甲" * 200)
    second_user = ChatMessage(role="user", content="short")
    recent = ChatMessage(role="assistant", content="recent")
    history = [first_user, second_user, ChatMessage(role="assistant", content="old answer"), recent]
    session = ArchiveSession(tmp_path / "workspace", home=tmp_path / "home", session_id="session")
    transaction = session.begin()

    result = _module().build_compacted_history(
        history,
        recent_messages=[recent],
        summary="正式摘要",
        transaction=transaction,
        preserve_user_tokens=10,
    )

    preview = json.loads(result.history[0].content)
    assert result.history[0].role == "user"
    assert result.history[0].origin is MessageOrigin.COMPACT_PREVIEW
    assert preview["kind"] == "user_message"
    assert preview["truncated"] is True
    assert preview["path"] == result.artifacts[0].path
    assert result.history[1] is second_user

    transaction.commit()
    assert _read_all(session, preview["path"]) == first_user.content

    session.close()


def _module():
    return importlib.import_module("mycode.compact.summary")


def _ascii_message(index):
    return ChatMessage(role="user", content=f"{index:04d}" + ("a" * 3_996))


def _read_all(session, path):
    chunks = []
    offset = 0
    while True:
        artifact_slice = session.read(path, offset=offset)
        chunks.append(artifact_slice.text)
        offset = artifact_slice.next_offset
        if artifact_slice.eof:
            return "".join(chunks)
