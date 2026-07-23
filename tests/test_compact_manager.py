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
from mycode.llm import BaseLLM, ChatMessage, MessageOrigin, StreamEvent, StreamEventType, UsageObservation
from mycode.memory import InMemoryConversationMemory
from mycode.tool import ToolCall


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


def test_context_manager_retries_heavy_compaction_and_commits_only_success(tmp_path):
    from mycode.compact.manager import ContextManager
    from mycode.compact.summary_prompt import SUMMARY_SECTIONS

    config = CompactConfig(
        context_window_tokens=16_001,
        tool_result_threshold_tokens=2_001,
        tool_batch_threshold_tokens=2_002,
    )
    policy = CompactPolicy(keep_recent_tokens=1, min_recent_messages=5)
    memory = CountingMemory(
        [
            ChatMessage(role="assistant", content="甲" * 6_000),
            *[ChatMessage(role="user", content=f"recent-{index}") for index in range(5)],
        ]
    )
    llm = RecordingLLM(
        scripts=[
            [StreamEvent(StreamEventType.TEXT_DELTA, "bad format"), StreamEvent(StreamEventType.DONE)],
            [
                StreamEvent(
                    StreamEventType.TOOL_CALL,
                    tool_call=ToolCall(id="call-1", name="read_compact_artifact", arguments={}),
                ),
                StreamEvent(StreamEventType.DONE),
            ],
            [
                StreamEvent(
                    StreamEventType.TEXT_DELTA,
                    "<analysis-draft>草稿</analysis-draft><summary>"
                    + "\n\n".join(f"## {section}\n最终摘要" for section in SUMMARY_SECTIONS)
                    + "</summary>",
                ),
                StreamEvent(StreamEventType.DONE),
            ],
        ]
    )
    builder = RecordingRequestBuilder()
    store = RecordingArchiveSession(tmp_path / "workspace", home=tmp_path / "home", session_id="session")
    manager = ContextManager(llm=llm, memory=memory, config=config, store=store, policy=policy)

    prepared = asyncio.run(manager.prepare_auto(build_request=builder, run_deadline=None))

    assert len(llm.requests) == 3
    assert memory.replace_calls == 1
    assert memory.messages()[0].origin is MessageOrigin.COMPACT_SUMMARY
    assert prepared.report.status is CompactStatus.COMPACTED
    assert prepared.report.actions == (CompactAction.HEAVY,)
    assert prepared.report.attempts == 3
    assert manager._failure_count == 0
    assert [transaction.rolled_back for transaction in store.transactions[:3]] == [True, True, True]
    assert store.transactions[3].committed is True
    assert builder.calls[-1] == tuple(memory.messages())

    store.close()


def test_context_manager_opens_circuit_and_commits_emergency_after_three_heavy_failures(tmp_path):
    from mycode.compact.manager import ContextManager

    config = CompactConfig(
        context_window_tokens=16_001,
        tool_result_threshold_tokens=2_001,
        tool_batch_threshold_tokens=2_002,
    )
    policy = CompactPolicy(keep_recent_tokens=1, min_recent_messages=5)
    memory = CountingMemory(
        [
            ChatMessage(role="assistant", content="甲" * 6_000),
            *[ChatMessage(role="user", content=f"recent-{index}") for index in range(5)],
        ]
    )
    llm = RecordingLLM(
        scripts=[
            [StreamEvent(StreamEventType.TEXT_DELTA, "bad format"), StreamEvent(StreamEventType.DONE)],
            [StreamEvent(StreamEventType.TEXT_DELTA, "bad format"), StreamEvent(StreamEventType.DONE)],
            [StreamEvent(StreamEventType.TEXT_DELTA, "bad format"), StreamEvent(StreamEventType.DONE)],
        ]
    )
    builder = RecordingRequestBuilder()
    store = RecordingArchiveSession(tmp_path / "workspace", home=tmp_path / "home", session_id="session")
    manager = ContextManager(llm=llm, memory=memory, config=config, store=store, policy=policy)

    prepared = asyncio.run(manager.prepare_auto(build_request=builder, run_deadline=None))

    assert len(llm.requests) == 3
    assert manager._circuit_open is True
    assert prepared.report.circuit_open is True
    assert prepared.report.status is CompactStatus.COMPACTED
    assert prepared.report.actions == (CompactAction.HEAVY, CompactAction.EMERGENCY)
    assert prepared.report.attempts == 3
    assert memory.replace_calls == 1
    assert memory.messages()[0].origin is MessageOrigin.COMPACT_SUMMARY
    assert "emergency_history_index" in memory.messages()[0].content
    assert store.transactions[-1].committed is True

    store.close()


def test_context_manager_uses_emergency_without_summary_llm_while_circuit_is_open(tmp_path):
    from mycode.compact.manager import ContextManager

    config = CompactConfig(
        context_window_tokens=16_001,
        tool_result_threshold_tokens=2_001,
        tool_batch_threshold_tokens=2_002,
    )
    policy = CompactPolicy(keep_recent_tokens=1, min_recent_messages=5)
    memory = CountingMemory(
        [
            ChatMessage(role="assistant", content="甲" * 6_000),
            *[ChatMessage(role="user", content=f"recent-{index}") for index in range(5)],
        ]
    )
    llm = RecordingLLM()
    builder = RecordingRequestBuilder()
    store = RecordingArchiveSession(tmp_path / "workspace", home=tmp_path / "home", session_id="session")
    manager = ContextManager(llm=llm, memory=memory, config=config, store=store, policy=policy)
    manager._circuit_open = True

    prepared = asyncio.run(manager.prepare_auto(build_request=builder, run_deadline=None))

    assert llm.requests == []
    assert prepared.report.circuit_open is True
    assert prepared.report.actions == (CompactAction.EMERGENCY,)
    assert memory.replace_calls == 1
    assert store.transactions[-1].committed is True

    store.close()


def test_context_manager_manual_compaction_noops_when_there_is_no_old_history(tmp_path):
    from mycode.compact.manager import ContextManager

    memory = CountingMemory([ChatMessage(role="user", content="only recent")])
    llm = RecordingLLM()
    store = ArchiveSession(tmp_path / "workspace", home=tmp_path / "home", session_id="session")
    manager = ContextManager(
        llm=llm,
        memory=memory,
        config=CompactConfig(context_window_tokens=100_000),
        store=store,
    )

    report = asyncio.run(manager.compact_manual(build_request=_fake_build_request, run_deadline=None))

    assert report.status is CompactStatus.NO_OP
    assert report.actions == (CompactAction.NONE,)
    assert llm.requests == []
    assert memory.replace_calls == 0

    store.close()


def test_context_manager_manual_success_resets_open_circuit(tmp_path):
    from mycode.compact.manager import ContextManager

    memory = CountingMemory(
        [
            ChatMessage(role="assistant", content="old enough to summarize"),
            *[ChatMessage(role="user", content=f"recent-{index}") for index in range(5)],
        ]
    )
    llm = RecordingLLM(scripts=[[StreamEvent(StreamEventType.TEXT_DELTA, _summary_output("手动摘要")), StreamEvent(StreamEventType.DONE)]])
    store = ArchiveSession(tmp_path / "workspace", home=tmp_path / "home", session_id="session")
    manager = ContextManager(
        llm=llm,
        memory=memory,
        config=CompactConfig(context_window_tokens=100_000),
        store=store,
        policy=CompactPolicy(keep_recent_tokens=1, min_recent_messages=5),
    )
    manager._circuit_open = True
    manager._failure_count = 3

    report = asyncio.run(manager.compact_manual(build_request=_fake_build_request, run_deadline=None))

    assert report.status is CompactStatus.COMPACTED
    assert report.actions == (CompactAction.HEAVY,)
    assert report.circuit_open is False
    assert manager._circuit_open is False
    assert manager._failure_count == 0
    assert memory.replace_calls == 1

    store.close()


def test_context_manager_manual_failure_keeps_open_circuit(tmp_path):
    from mycode.compact.manager import ContextManager

    memory = CountingMemory(
        [
            ChatMessage(role="assistant", content="old enough to summarize"),
            *[ChatMessage(role="user", content=f"recent-{index}") for index in range(5)],
        ]
    )
    llm = RecordingLLM(
        scripts=[
            [StreamEvent(StreamEventType.TEXT_DELTA, "bad"), StreamEvent(StreamEventType.DONE)]
            for _ in range(3)
        ]
    )
    store = ArchiveSession(tmp_path / "workspace", home=tmp_path / "home", session_id="session")
    manager = ContextManager(
        llm=llm,
        memory=memory,
        config=CompactConfig(context_window_tokens=100_000),
        store=store,
        policy=CompactPolicy(keep_recent_tokens=1, min_recent_messages=5),
    )
    manager._circuit_open = True

    report = asyncio.run(manager.compact_manual(build_request=_fake_build_request, run_deadline=None))

    assert report.status is CompactStatus.FAILED
    assert report.circuit_open is True
    assert manager._circuit_open is True
    assert memory.replace_calls == 0

    store.close()


def test_context_manager_lifecycle_record_usage_clear_close_artifact_tool_and_factory(tmp_path):
    from mycode.compact import create_context_manager
    from mycode.compact.manager import ContextManager

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    memory = CountingMemory([ChatMessage(role="user", content="hello")])
    manager = create_context_manager(
        workspace_root=workspace,
        home=tmp_path / "home",
        llm=RecordingLLM(),
        memory=memory,
        config=CompactConfig(context_window_tokens=100_000),
        model_timeout_seconds=1.0,
    )

    assert isinstance(manager, ContextManager)
    assert manager.artifact_tool.definition.name == "read_compact_artifact"
    assert manager.artifact_tool.definition.grant_arguments == ()

    snapshot = manager._estimator.snapshot([ChatMessage(role="user", content="hello")], [])
    manager.record_usage(snapshot, UsageObservation(provider="test", input_tokens=123))
    assert manager._estimator.estimate(snapshot).source == "usage_delta"

    first_session_dir = manager._store.session_dir
    manager._failure_count = 2
    manager._circuit_open = True

    manager.clear()

    assert memory.messages() == []
    assert manager._failure_count == 0
    assert manager._circuit_open is False
    assert manager._estimator.estimate(snapshot).source == "full_chars"
    assert not first_session_dir.exists()
    assert manager._store.session_dir.exists()
    assert manager._store.session_dir != first_session_dir

    second_session_dir = manager._store.session_dir
    manager.close()
    assert not second_session_dir.exists()


class RecordingLLM(BaseLLM):
    def __init__(self, scripts=None):
        self.requests = []
        self.scripts = list(scripts or [])

    async def stream_chat(self, messages, tools=None):
        self.requests.append((list(messages), tools))
        if self.scripts:
            for event in self.scripts.pop(0):
                yield event
            return
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


def _fake_build_request(messages):
    return FakePromptRequest(messages=tuple(messages), tools=())


class CountingMemory(InMemoryConversationMemory):
    def __init__(self, messages):
        super().__init__()
        self.replace_calls = 0
        for message in messages:
            self.append(message)

    def replace(self, messages):
        self.replace_calls += 1
        super().replace(messages)


class RecordingArchiveSession(ArchiveSession):
    def __init__(self, *args, **kwargs):
        self.transactions = []
        super().__init__(*args, **kwargs)

    def begin(self):
        transaction = RecordingTransaction(super().begin())
        self.transactions.append(transaction)
        return transaction


class RecordingTransaction:
    def __init__(self, inner):
        self._inner = inner
        self.committed = False
        self.rolled_back = False

    def archive_text(self, *, kind, text):
        return self._inner.archive_text(kind=kind, text=text)

    def commit(self):
        self.committed = True
        return self._inner.commit()

    def rollback(self):
        self.rolled_back = True
        return self._inner.rollback()


def _summary_output(section_text):
    from mycode.compact.summary_prompt import SUMMARY_SECTIONS

    return (
        "<analysis-draft>草稿</analysis-draft><summary>"
        + "\n\n".join(f"## {section}\n{section_text}" for section in SUMMARY_SECTIONS)
        + "</summary>"
    )
