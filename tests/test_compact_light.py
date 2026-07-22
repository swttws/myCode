from __future__ import annotations

import importlib
import json
from hashlib import sha256

from mycode.compact.archive import ArchiveSession
from mycode.compact import archive as archive_module
from mycode.compact.estimator import TokenEstimator
from mycode.compact.models import CompactConfig, CompactPolicy
from mycode.llm import ChatMessage, MessageOrigin


def test_single_oversized_tool_result_is_archived_with_structured_preview(tmp_path):
    original = "0123456789" * 1_000
    config = CompactConfig(
        context_window_tokens=20_000,
        tool_result_threshold_tokens=2_100,
        tool_batch_threshold_tokens=2_200,
    )
    session = ArchiveSession(tmp_path / "workspace", home=tmp_path / "home", session_id="session")
    transaction = session.begin()
    message = ChatMessage(role="tool", content=original, tool_call_id="call-1")
    compactor = _make_compactor(config)

    result = compactor.compact([message], transaction)

    assert result.changed is True
    assert len(result.artifacts) == 1
    artifact = result.artifacts[0]
    compacted = result.history[0]
    preview = json.loads(compacted.content)

    assert compacted.role == "tool"
    assert compacted.tool_call_id == "call-1"
    assert compacted.origin is MessageOrigin.COMPACT_PREVIEW
    assert artifact.path == preview["path"]
    assert preview["kind"] == "tool_result"
    assert preview["tool_call_id"] == "call-1"
    assert preview["original_chars"] == len(original)
    assert preview["estimated_tokens"] == TokenEstimator().estimate_text(original)
    assert preview["sha256"] == sha256(original.encode("utf-8")).hexdigest()
    assert preview["truncated"] is True
    assert original.startswith(preview["head"])
    assert original.endswith(preview["tail"])
    assert TokenEstimator().estimate_text(compacted.content) <= CompactPolicy().preview_tokens

    transaction.commit()
    assert _read_all(session, artifact.path) == original

    session.close()


def test_single_tool_result_at_threshold_is_not_archived(tmp_path):
    original = "a" * 8_400
    config = CompactConfig(
        context_window_tokens=20_000,
        tool_result_threshold_tokens=2_100,
        tool_batch_threshold_tokens=2_200,
    )
    session = ArchiveSession(tmp_path / "workspace", home=tmp_path / "home", session_id="session")
    transaction = session.begin()
    message = ChatMessage(role="tool", content=original, tool_call_id="call-1")
    compactor = _make_compactor(config)

    result = compactor.compact([message], transaction)

    assert result.changed is False
    assert result.history == (message,)
    assert result.artifacts == ()

    transaction.rollback()
    session.close()


def test_batch_archives_largest_result_and_reestimates_before_continuing(tmp_path):
    first = "a" * 26_000
    second = "b" * 28_000
    session = ArchiveSession(tmp_path / "workspace", home=tmp_path / "home", session_id="session")
    transaction = session.begin()
    compactor = _make_compactor(
        CompactConfig(
            context_window_tokens=30_000,
            tool_result_threshold_tokens=8_000,
            tool_batch_threshold_tokens=12_000,
        )
    )
    history = [
        ChatMessage(role="tool", content=first, tool_call_id="call-first"),
        ChatMessage(role="tool", content=second, tool_call_id="call-second"),
    ]

    result = compactor.compact(history, transaction)

    assert result.changed is True
    assert len(result.artifacts) == 1
    assert result.artifacts[0].original_chars == len(second)
    assert result.history[0] == history[0]
    assert result.history[1].origin is MessageOrigin.COMPACT_PREVIEW
    assert json.loads(result.history[1].content)["tool_call_id"] == "call-second"

    transaction.rollback()
    session.close()


def test_batch_uses_original_order_when_estimated_sizes_are_equal(tmp_path):
    content = "x" * 26_000
    session = ArchiveSession(tmp_path / "workspace", home=tmp_path / "home", session_id="session")
    transaction = session.begin()
    compactor = _make_compactor(
        CompactConfig(
            context_window_tokens=30_000,
            tool_result_threshold_tokens=8_000,
            tool_batch_threshold_tokens=12_000,
        )
    )
    history = [
        ChatMessage(role="tool", content=content, tool_call_id="call-first"),
        ChatMessage(role="tool", content=content, tool_call_id="call-second"),
    ]

    result = compactor.compact(history, transaction)

    assert len(result.artifacts) == 1
    assert result.history[0].origin is MessageOrigin.COMPACT_PREVIEW
    assert result.history[1] == history[1]
    assert json.loads(result.history[0].content)["tool_call_id"] == "call-first"

    transaction.rollback()
    session.close()


def test_batch_does_not_combine_tool_results_across_round_boundaries(tmp_path):
    content = "x" * 26_000
    session = ArchiveSession(tmp_path / "workspace", home=tmp_path / "home", session_id="session")
    transaction = session.begin()
    compactor = _make_compactor(
        CompactConfig(
            context_window_tokens=30_000,
            tool_result_threshold_tokens=8_000,
            tool_batch_threshold_tokens=12_000,
        )
    )
    history = [
        ChatMessage(role="tool", content=content, tool_call_id="call-first"),
        ChatMessage(role="assistant", content="next round"),
        ChatMessage(role="tool", content=content, tool_call_id="call-second"),
    ]

    result = compactor.compact(history, transaction)

    assert result.changed is False
    assert result.history == tuple(history)
    assert result.artifacts == ()

    transaction.rollback()
    session.close()


def test_compact_preview_and_small_tool_results_are_idempotent(tmp_path):
    preview = ChatMessage(
        role="tool",
        content='{"path":"already-archived"}',
        tool_call_id="call-preview",
        origin=MessageOrigin.COMPACT_PREVIEW,
    )
    small = ChatMessage(role="tool", content="small", tool_call_id="call-small")
    session = ArchiveSession(tmp_path / "workspace", home=tmp_path / "home", session_id="session")
    transaction = session.begin()
    compactor = _make_compactor(
        CompactConfig(
            context_window_tokens=30_000,
            tool_result_threshold_tokens=8_000,
            tool_batch_threshold_tokens=12_000,
        )
    )

    result = compactor.compact([preview, small], transaction)

    assert result.changed is False
    assert result.history == (preview, small)
    assert result.history[0] is preview
    assert result.history[1] is small
    assert result.artifacts == ()

    transaction.rollback()
    session.close()


def test_archive_failure_returns_original_history_without_partial_replacements(
    tmp_path,
    monkeypatch,
):
    first = "a" * 34_000
    second = "b" * 36_000
    session = ArchiveSession(tmp_path / "workspace", home=tmp_path / "home", session_id="session")
    transaction = session.begin()
    compactor = _make_compactor(
        CompactConfig(
            context_window_tokens=40_000,
            tool_result_threshold_tokens=8_000,
            tool_batch_threshold_tokens=12_000,
        )
    )
    history = [
        ChatMessage(role="tool", content=first, tool_call_id="call-first"),
        ChatMessage(role="tool", content=second, tool_call_id="call-second"),
    ]
    real_archive_text = archive_module.ArchiveTransaction.archive_text
    calls = {"count": 0}

    def fail_on_second_archive(self, *, kind, text):
        calls["count"] += 1
        if calls["count"] == 2:
            raise OSError("disk full")
        return real_archive_text(self, kind=kind, text=text)

    monkeypatch.setattr(
        archive_module.ArchiveTransaction,
        "archive_text",
        fail_on_second_archive,
    )

    result = compactor.compact(history, transaction)

    assert result.changed is False
    assert result.history == tuple(history)
    assert result.artifacts == ()
    assert not list((session.session_dir / "tmp").glob("*"))

    session.close()


def _make_compactor(config):
    light_module = importlib.import_module("mycode.compact.light")
    return light_module.ToolResultCompactor(config=config)


def _read_all(session, path):
    chunks = []
    offset = 0
    while True:
        artifact_slice = session.read(path, offset=offset)
        chunks.append(artifact_slice.text)
        offset = artifact_slice.next_offset
        if artifact_slice.eof:
            return "".join(chunks)
