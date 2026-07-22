from __future__ import annotations

import importlib
import json
from hashlib import sha256

from mycode.compact.archive import ArchiveSession
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
