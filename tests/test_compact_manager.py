from dataclasses import FrozenInstanceError

import asyncio

import pytest

from mycode.compact.archive import ArchiveSession
from mycode.compact.models import (
    ArchivedArtifact,
    ArtifactSlice,
    CompactAction,
    CompactConfig,
    CompactError,
    CompactFailureCode,
    CompactPolicy,
    CompactReport,
    CompactStatus,
    HeavyCompactResult,
    LightCompactResult,
    PreparedContext,
    RequestSnapshot,
    TokenEstimate,
)
from mycode.llm import BaseLLM, ChatMessage, StreamEvent, StreamEventType
from mycode.memory import InMemoryConversationMemory


def test_compact_config_and_policy_have_approved_defaults_and_are_frozen():
    config = CompactConfig(context_window_tokens=128_000)
    policy = CompactPolicy()

    assert config.tool_result_threshold_tokens == 8_000
    assert config.tool_batch_threshold_tokens == 12_000
    assert policy.preview_tokens == 2_000
    assert policy.auto_reserve_tokens == 13_000
    assert policy.manual_reserve_tokens == 3_000
    assert policy.keep_recent_tokens == 10_000
    assert policy.min_recent_messages == 5
    assert policy.max_attempts == 3
    assert policy.stale_after_seconds == 86_400

    with pytest.raises(FrozenInstanceError):
        config.context_window_tokens = 1


def test_compact_value_models_retain_their_context_fields():
    snapshot = RequestSnapshot(ascii_chars=12, non_ascii_chars=3, fingerprint="abc123")
    estimate = TokenEstimate(
        tokens=9,
        source="usage_delta",
        anchor_input_tokens=4,
        delta_tokens=5,
    )
    artifact = ArchivedArtifact(
        path=".mycode/archives/tool.txt",
        kind="tool_result",
        original_chars=42,
        estimated_tokens=11,
        sha256="digest",
    )
    slice_ = ArtifactSlice(path=artifact.path, text="payload", next_offset=7, eof=False)

    assert snapshot.fingerprint == "abc123"
    assert estimate.source == "usage_delta"
    assert estimate.anchor_input_tokens == 4
    assert artifact.kind == "tool_result"
    assert slice_.next_offset == 7
    assert not slice_.eof


def test_token_estimate_defaults_usage_anchor_to_none():
    estimate = TokenEstimate(tokens=1, source="full_chars", delta_tokens=1)

    assert estimate.anchor_input_tokens is None


def test_compact_enums_expose_the_approved_wire_values():
    assert {action.value for action in CompactAction} == {
        "none",
        "light",
        "heavy",
        "force",
        "emergency",
    }
    assert {status.value for status in CompactStatus} == {
        "safe",
        "compacted",
        "no_op",
        "failed",
    }
    assert {code.value for code in CompactFailureCode} == {
        "llm_error",
        "tool_attempt",
        "invalid_format",
        "summary_too_large",
        "budget_not_recovered",
        "archive_error",
        "timeout",
        "cancelled",
    }


def test_compact_report_results_and_error_preserve_outcome_details():
    report = CompactReport(
        status=CompactStatus.FAILED,
        actions=(CompactAction.HEAVY,),
        before_tokens=20_000,
        after_tokens=8_000,
        archived_count=2,
        attempts=3,
        circuit_open=True,
    )
    light = LightCompactResult(history=("message",), artifacts=(), changed=True)
    heavy = HeavyCompactResult(
        history=("summary",),
        artifacts=(),
        actions=(CompactAction.HEAVY,),
        summary="summary",
    )

    assert report.failure_code is None
    assert report.message_zh == ""
    assert light.changed
    assert heavy.summary == "summary"

    error = CompactError(report)

    assert isinstance(error, RuntimeError)
    assert error.report is report


def test_context_manager_prepare_auto_returns_safe_request_without_summary_call(tmp_path):
    from mycode.compact.manager import ContextManager

    memory = InMemoryConversationMemory()
    memory.append(ChatMessage(role="user", content="hello"))
    llm = RecordingLLM()
    builder = RecordingRequestBuilder()
    store = ArchiveSession(tmp_path / "workspace", home=tmp_path / "home", session_id="session")
    manager = ContextManager(
        llm=llm,
        memory=memory,
        config=CompactConfig(context_window_tokens=100_000),
        store=store,
    )

    prepared = asyncio.run(manager.prepare_auto(build_request=builder, run_deadline=None))

    assert isinstance(prepared, PreparedContext)
    assert llm.requests == []
    assert builder.calls == [tuple(memory.messages())]
    assert prepared.request.messages == tuple(memory.messages())
    assert prepared.report.status is CompactStatus.SAFE
    assert prepared.report.actions == (CompactAction.NONE,)
    assert prepared.report.archived_count == 0

    store.close()


def test_context_manager_prepare_auto_commits_light_compaction_before_rebuilding_request(tmp_path):
    from mycode.compact.manager import ContextManager

    memory = InMemoryConversationMemory()
    original_tool = ChatMessage(role="tool", content="x" * 9_000, tool_call_id="call-1")
    memory.append(original_tool)
    llm = RecordingLLM()
    builder = RecordingRequestBuilder()
    store = ArchiveSession(tmp_path / "workspace", home=tmp_path / "home", session_id="session")
    config = CompactConfig(
        context_window_tokens=16_001,
        tool_result_threshold_tokens=2_001,
        tool_batch_threshold_tokens=2_002,
    )
    manager = ContextManager(llm=llm, memory=memory, config=config, store=store)

    prepared = asyncio.run(manager.prepare_auto(build_request=builder, run_deadline=None))

    compacted_history = tuple(memory.messages())
    assert llm.requests == []
    assert len(builder.calls) == 1
    assert builder.calls[0] == compacted_history
    assert compacted_history[0].origin.value == "compact_preview"
    assert original_tool.content not in compacted_history[0].content
    assert prepared.report.status is CompactStatus.COMPACTED
    assert prepared.report.actions == (CompactAction.LIGHT,)
    assert prepared.report.archived_count == 1

    preview_path = __import__("json").loads(compacted_history[0].content)["path"]
    store.read(preview_path)

    store.close()


class RecordingLLM(BaseLLM):
    def __init__(self):
        self.requests = []

    async def stream_chat(self, messages, tools=None):
        self.requests.append((list(messages), tools))
        yield StreamEvent(StreamEventType.DONE)


class RecordingRequestBuilder:
    def __init__(self):
        self.calls = []

    def __call__(self, history):
        history = tuple(history)
        self.calls.append(history)
        return FakePromptRequest(messages=history, tools=())


class FakePromptRequest:
    def __init__(self, *, messages, tools):
        self.messages = messages
        self.tools = tools
