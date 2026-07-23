from __future__ import annotations

import asyncio
import importlib
import json
import time

from mycode.compact.archive import ArchiveSession
from mycode.compact.models import CompactConfig, CompactError, CompactFailureCode
from mycode.compact.summary_prompt import SUMMARY_SECTIONS
from mycode.llm import BaseLLM, ChatMessage, MessageOrigin, StreamEvent, StreamEventType, UsageObservation
from mycode.tool import ToolCall


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


def test_collect_summary_sends_one_user_prompt_without_tools_and_ignores_draft():
    output = _summary_output(section_text="正式内容")
    llm = ScriptedSummaryLLM(
        [
            [
                StreamEvent(StreamEventType.THINKING_DELTA, "隐藏思考"),
                StreamEvent(StreamEventType.TEXT_DELTA, output[:60]),
                StreamEvent(StreamEventType.TEXT_DELTA, output[60:]),
                StreamEvent(StreamEventType.DONE),
            ]
        ]
    )
    source = [ChatMessage(role="assistant", content="旧回答")]

    summary = asyncio.run(_module().collect_summary(llm, source))

    assert len(llm.requests) == 1
    assert llm.requests[0][0].role == "user"
    assert len(llm.requests[0]) == 1
    assert llm.tool_requests == [[]]
    assert "旧回答" in llm.requests[0][0].content
    assert summary == _summary_text("正式内容")
    assert "草稿内容" not in summary
    assert "隐藏思考" not in summary


def test_collect_summary_fails_when_model_attempts_tool_call():
    llm = ScriptedSummaryLLM(
        [
            [
                StreamEvent(
                    StreamEventType.TOOL_CALL,
                    tool_call=ToolCall(id="call-1", name="read_compact_artifact", arguments={}),
                ),
                StreamEvent(StreamEventType.DONE),
            ]
        ]
    )

    error = _collect_error(llm)

    assert error.report.failure_code is CompactFailureCode.TOOL_ATTEMPT


def test_collect_summary_maps_error_event_to_llm_error():
    llm = ScriptedSummaryLLM([[StreamEvent(StreamEventType.ERROR, "provider failed")]])

    error = _collect_error(llm)

    assert error.report.failure_code is CompactFailureCode.LLM_ERROR


def test_collect_summary_fails_when_stream_ends_without_done():
    llm = ScriptedSummaryLLM([[StreamEvent(StreamEventType.TEXT_DELTA, _summary_output())]])

    error = _collect_error(llm)

    assert error.report.failure_code is CompactFailureCode.INVALID_FORMAT


def test_collect_summary_respects_model_timeout():
    llm = HangingSummaryLLM()

    error = _collect_error(llm, model_timeout_seconds=0.01)

    assert error.report.failure_code is CompactFailureCode.TIMEOUT


def test_collect_summary_respects_run_deadline():
    llm = HangingSummaryLLM()

    error = _collect_error(llm, run_deadline=time.monotonic() - 0.01)

    assert error.report.failure_code is CompactFailureCode.TIMEOUT


def test_collect_summary_maps_cancellation():
    llm = CancelledSummaryLLM()

    error = _collect_error(llm)

    assert error.report.failure_code is CompactFailureCode.CANCELLED


def test_collect_summary_rejects_summary_over_manual_reserve():
    llm = ScriptedSummaryLLM(
        [
            [
                StreamEvent(StreamEventType.TEXT_DELTA, _summary_output(section_text="甲" * 5_000)),
                StreamEvent(StreamEventType.DONE),
            ]
        ]
    )

    error = _collect_error(llm)

    assert error.report.failure_code is CompactFailureCode.SUMMARY_TOO_LARGE


def test_collect_summary_records_valid_usage_for_summary_request():
    module = _module()
    estimator = module.TokenEstimator()
    llm = ScriptedSummaryLLM(
        [
            [
                StreamEvent(StreamEventType.TEXT_DELTA, _summary_output()),
                StreamEvent(
                    StreamEventType.DONE,
                    usage=UsageObservation(provider="test", input_tokens=777),
                ),
            ]
        ]
    )

    asyncio.run(module.collect_summary(llm, [ChatMessage(role="assistant", content="old")], estimator=estimator))

    snapshot = estimator.snapshot(llm.requests[0], [])
    estimate = estimator.estimate(snapshot)
    assert estimate.source == "usage_delta"
    assert estimate.tokens == 777


def test_summarize_oversized_message_archives_original_and_summarizes_budgeted_chunks(tmp_path):
    module = _module()
    config = CompactConfig(
        context_window_tokens=16_001,
        tool_result_threshold_tokens=2_001,
        tool_batch_threshold_tokens=2_002,
    )
    original = "甲" * 45_000
    message = ChatMessage(role="user", content=original)
    llm = ScriptedSummaryLLM(
        [
            [StreamEvent(StreamEventType.TEXT_DELTA, _summary_output(section_text=f"临时摘要 {index}")), StreamEvent(StreamEventType.DONE)]
            for index in range(10)
        ]
    )
    session = ArchiveSession(tmp_path / "workspace", home=tmp_path / "home", session_id="session")
    transaction = session.begin()

    result = asyncio.run(
        module.summarize_oversized_message(
            llm,
            message,
            config=config,
            transaction=transaction,
        )
    )

    assert len(llm.requests) > 1
    budget = config.context_window_tokens - module.CompactPolicy().manual_reserve_tokens
    for request in llm.requests:
        request_estimator = module.TokenEstimator()
        snapshot = request_estimator.snapshot(request, [])
        assert request_estimator.estimate(snapshot).tokens <= budget

    assert result.artifact.kind == "user_message"
    assert result.artifact.original_chars == len(original)
    assert result.message.role == "user"
    assert result.message.origin is MessageOrigin.COMPACT_PREVIEW
    assert original not in result.message.content
    preview = json.loads(result.message.content)
    assert preview["path"] == result.artifact.path
    assert preview["temporary_summaries"] == list(result.temporary_summaries)
    assert "草稿内容" not in result.message.content

    transaction.commit()
    assert _read_all(session, result.artifact.path) == original

    session.close()


def test_summarize_oversized_message_fails_when_replacement_does_not_shrink(tmp_path):
    config = CompactConfig(
        context_window_tokens=16_001,
        tool_result_threshold_tokens=2_001,
        tool_batch_threshold_tokens=2_002,
    )
    message = ChatMessage(role="user", content="short")
    llm = ScriptedSummaryLLM(
        [
            [
                StreamEvent(StreamEventType.TEXT_DELTA, _summary_output(section_text="临时摘要内容" * 20)),
                StreamEvent(StreamEventType.DONE),
            ]
        ]
    )
    session = ArchiveSession(tmp_path / "workspace", home=tmp_path / "home", session_id="session")
    transaction = session.begin()

    error = _force_error(llm, message, config=config, transaction=transaction)

    assert error.report.failure_code is CompactFailureCode.BUDGET_NOT_RECOVERED

    transaction.rollback()
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


def _summary_text(section_text="内容"):
    return "\n\n".join(f"## {section}\n{section_text}" for section in SUMMARY_SECTIONS)


def _summary_output(section_text="内容"):
    return (
        "<analysis-draft>\n草稿内容\n</analysis-draft>\n"
        f"<summary>\n{_summary_text(section_text)}\n</summary>"
    )


def _collect_error(llm, **kwargs):
    try:
        asyncio.run(_module().collect_summary(llm, [ChatMessage(role="assistant", content="old")], **kwargs))
    except CompactError as exc:
        return exc
    raise AssertionError("collect_summary did not fail")


def _force_error(llm, message, **kwargs):
    try:
        asyncio.run(_module().summarize_oversized_message(llm, message, **kwargs))
    except CompactError as exc:
        return exc
    raise AssertionError("summarize_oversized_message did not fail")


class ScriptedSummaryLLM(BaseLLM):
    def __init__(self, scripts):
        self.scripts = list(scripts)
        self.requests = []
        self.tool_requests = []

    async def stream_chat(self, messages, tools=None):
        self.requests.append(list(messages))
        self.tool_requests.append(tools)
        script = self.scripts.pop(0)
        for event in script:
            yield event


class HangingSummaryLLM(BaseLLM):
    async def stream_chat(self, messages, tools=None):
        await asyncio.sleep(1)
        yield StreamEvent(StreamEventType.DONE)


class CancelledSummaryLLM(BaseLLM):
    async def stream_chat(self, messages, tools=None):
        raise asyncio.CancelledError
        yield StreamEvent(StreamEventType.DONE)
