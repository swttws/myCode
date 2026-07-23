import asyncio
import json
from hashlib import sha256

from mycode.agent import AgentConfig, AgentErrorCode, AgentEventType, AgentLoop
from mycode.compact.archive import ArchiveSession
from mycode.compact.estimator import TokenEstimator
from mycode.compact.manager import ContextManager
from mycode.compact.models import (
    ArchivedArtifact,
    CompactAction,
    CompactConfig,
    CompactFailureCode,
    CompactPolicy,
)
from mycode.compact.summary_prompt import (
    DRAFT_CLOSE,
    DRAFT_OPEN,
    SUMMARY_CLOSE,
    SUMMARY_OPEN,
    SUMMARY_SECTIONS,
)
from mycode.llm import BaseLLM, ChatMessage, MessageOrigin, StreamEvent, StreamEventType
from mycode.memory import InMemoryConversationMemory
from mycode.permission.models import PermissionDecision, PermissionEffect, PermissionMode
from mycode.prompt import create_default_prompt_builder
from mycode.tool import ToolCall, ToolDefinition, ToolExecutor, ToolKind, ToolRegistry, ToolResult


SECRET_DETAIL = "SECRET-DETAIL-42"
HUGE_TOOL_TEXT = "A" * 10_000 + SECRET_DETAIL + "B" * 20_000
OLD_ASSISTANT_TEXT = "old assistant context " + "C" * 90_000
FAILURE_OLD_TEXT = "old failure context " + "D" * 24_000
UNSAFE_TOOL_TEXT = "unsafe oversized tool output " + "E" * 20_000


class EndToEndLLM(BaseLLM):
    def __init__(self):
        self.normal_requests = []
        self.summary_requests = []
        self.preview_path = None
        self.recovered_detail_seen = False
        self.read_offsets = []

    async def stream_chat(self, messages, tools=None):
        tool_names = [tool.name for tool in tools or []]
        if tools == []:
            self.summary_requests.append(list(messages))
            yield StreamEvent(StreamEventType.TEXT_DELTA, _summary_output())
            yield StreamEvent(StreamEventType.DONE)
            return

        self.normal_requests.append((list(messages), list(tools or [])))
        request_index = len(self.normal_requests)
        if request_index == 1:
            yield StreamEvent(
                StreamEventType.TOOL_CALL,
                tool_call=ToolCall("huge-1", "huge_result", {}, "{}"),
            )
            yield StreamEvent(StreamEventType.DONE)
            return

        if request_index == 2:
            assert "read_compact_artifact" in tool_names
            previews = [
                message
                for message in messages
                if message.origin is MessageOrigin.COMPACT_PREVIEW
            ]
            assert previews
            assert SECRET_DETAIL not in previews[0].content
            self.preview_path = json.loads(previews[0].content)["path"]
            self.read_offsets.append(0)
            yield StreamEvent(
                StreamEventType.TOOL_CALL,
                tool_call=ToolCall(
                    "read-1",
                    "read_compact_artifact",
                    {"path": self.preview_path, "offset": 0, "max_tokens": 2_000},
                    "{}",
                ),
            )
            yield StreamEvent(StreamEventType.DONE)
            return

        if request_index == 3:
            read_results = _serialized_tool_results(messages, "read_compact_artifact")
            self.recovered_detail_seen = _results_include_secret(read_results)
            assert self.recovered_detail_seen is False
            last_content = read_results[-1]["content"]
            assert last_content["eof"] is False
            next_offset = last_content["next_offset"]
            self.read_offsets.append(next_offset)
            yield StreamEvent(
                StreamEventType.TOOL_CALL,
                tool_call=ToolCall(
                    "read-2",
                    "read_compact_artifact",
                    {
                        "path": self.preview_path,
                        "offset": next_offset,
                        "max_tokens": 2_000,
                    },
                    "{}",
                ),
            )
            yield StreamEvent(StreamEventType.DONE)
            return

        if request_index == 4:
            read_results = _serialized_tool_results(messages, "read_compact_artifact")
            self.recovered_detail_seen = _results_include_secret(read_results)
            assert self.recovered_detail_seen is True
            yield StreamEvent(StreamEventType.TEXT_DELTA, f"restored {SECRET_DETAIL}")
            yield StreamEvent(StreamEventType.DONE)
            return

        if request_index == 5:
            assert any(
                message.origin is MessageOrigin.COMPACT_SUMMARY for message in messages
            )
            assert OLD_ASSISTANT_TEXT not in "\n".join(message.content for message in messages)
            yield StreamEvent(StreamEventType.TEXT_DELTA, "continued after summary")
            yield StreamEvent(StreamEventType.DONE)
            return

        raise AssertionError(f"unexpected normal request #{request_index}")


class HugeResultTool:
    @property
    def definition(self):
        return ToolDefinition(
            name="huge_result",
            description="Return a huge result with recoverable detail.",
            parameters={"type": "object", "properties": {}, "required": []},
            kind=ToolKind.READ,
        )

    def execute(self, arguments):
        return ToolResult(ok=True, tool_name="huge_result", content={"text": HUGE_TOOL_TEXT})


class FailingThenRecoveringLLM(BaseLLM):
    def __init__(self):
        self.normal_requests = []
        self.summary_requests = []

    async def stream_chat(self, messages, tools=None):
        if tools == []:
            self.summary_requests.append(list(messages))
            request_index = len(self.summary_requests)
            if request_index == 1:
                yield StreamEvent(StreamEventType.ERROR, content="summary api failed")
                return
            if request_index in {2, 3}:
                yield StreamEvent(StreamEventType.TEXT_DELTA, "bad summary format")
                yield StreamEvent(StreamEventType.DONE)
                return
            yield StreamEvent(StreamEventType.TEXT_DELTA, _summary_output())
            yield StreamEvent(StreamEventType.DONE)
            return

        self.normal_requests.append((list(messages), list(tools or [])))
        joined = "\n".join(message.content for message in messages)
        assert FAILURE_OLD_TEXT not in joined
        assert any(
            message.origin is MessageOrigin.COMPACT_SUMMARY
            and "emergency_history_index" in message.content
            for message in messages
        )
        yield StreamEvent(StreamEventType.TEXT_DELTA, "continued after emergency")
        yield StreamEvent(StreamEventType.DONE)


class RecordingNormalLLM(BaseLLM):
    def __init__(self):
        self.normal_requests = []
        self.summary_requests = []

    async def stream_chat(self, messages, tools=None):
        if tools == []:
            self.summary_requests.append(list(messages))
            yield StreamEvent(StreamEventType.TEXT_DELTA, _summary_output())
            yield StreamEvent(StreamEventType.DONE)
            return

        self.normal_requests.append((list(messages), list(tools or [])))
        yield StreamEvent(StreamEventType.TEXT_DELTA, "unsafe request was sent")
        yield StreamEvent(StreamEventType.DONE)


class AllowPermission:
    async def before_tool(self, call, definition, *, plan_only, round_index):
        return PermissionDecision(
            effect=PermissionEffect.ALLOW,
            reason_code="test_allow",
            message_zh="允许",
            mode=PermissionMode.DEFAULT,
            display_arguments={},
        )

    async def after_tool(self, call, result):
        return result


async def collect_async(async_iterable):
    return [event async for event in async_iterable]


def _serialized_tool_results(messages, tool_name):
    results = []
    for message in messages:
        if message.role != "tool":
            continue
        payload = json.loads(message.content)
        if payload.get("tool_name") == tool_name:
            results.append(payload)
    return results


def _results_include_secret(results):
    return any(
        SECRET_DETAIL in result.get("content", {}).get("text", "")
        for result in results
    )


def _read_all(store, path):
    offset = 0
    parts = []
    while True:
        artifact_slice = store.read(path, offset=offset, max_tokens=2_000)
        parts.append(artifact_slice.text)
        if artifact_slice.eof:
            return "".join(parts)
        offset = artifact_slice.next_offset


class CommitFailArchiveSession:
    def __init__(self):
        self.transactions = []

    def begin(self):
        transaction = CommitFailTransaction()
        self.transactions.append(transaction)
        return transaction

    def reset_session(self):
        return None

    def close(self):
        return None


class CommitFailTransaction:
    def __init__(self):
        self.committed = False
        self.rolled_back = False

    def archive_text(self, *, kind, text):
        digest = sha256(text.encode("utf-8")).hexdigest()
        return ArchivedArtifact(
            path=f"failed://{digest}.json",
            kind=kind,
            original_chars=len(text),
            estimated_tokens=TokenEstimator().estimate_text(text),
            sha256=digest,
        )

    def commit(self):
        self.committed = True
        raise OSError("disk full")

    def rollback(self):
        self.rolled_back = True


def test_context_compaction_e2e_normal_long_session_archives_summarizes_and_recovers(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    memory = InMemoryConversationMemory()
    llm = EndToEndLLM()
    config = CompactConfig(
        context_window_tokens=28_000,
        tool_result_threshold_tokens=3_000,
        tool_batch_threshold_tokens=5_000,
    )
    manager = ContextManager(
        llm=llm,
        memory=memory,
        config=config,
        store=ArchiveSession(workspace, home=tmp_path / "home"),
    )
    registry = ToolRegistry([HugeResultTool(), manager.artifact_tool])
    agent = AgentLoop(
        llm=llm,
        memory=memory,
        tool_executor=ToolExecutor(registry),
        tool_registry=registry,
        permission=AllowPermission(),
        context_manager=manager,
        config=AgentConfig(max_rounds=6),
        prompt_builder=create_default_prompt_builder(workspace),
    )

    try:
        first_events = asyncio.run(
            collect_async(agent.run("collect huge details", mode=_mode()))
        )

        tool_flow = [
            (event.type, event.tool_call.name if event.tool_call else event.tool_result.tool_name)
            for event in first_events
            if event.type in {AgentEventType.TOOL_CALL_STARTED, AgentEventType.TOOL_RESULT}
        ]
        assert tool_flow == [
            (AgentEventType.TOOL_CALL_STARTED, "huge_result"),
            (AgentEventType.TOOL_RESULT, "huge_result"),
            (AgentEventType.TOOL_CALL_STARTED, "read_compact_artifact"),
            (AgentEventType.TOOL_RESULT, "read_compact_artifact"),
            (AgentEventType.TOOL_CALL_STARTED, "read_compact_artifact"),
            (AgentEventType.TOOL_RESULT, "read_compact_artifact"),
        ]
        assert llm.preview_path is not None
        assert llm.read_offsets[0] == 0
        assert llm.read_offsets[1] > llm.read_offsets[0]
        assert llm.recovered_detail_seen is True
        assert any(
            event.type is AgentEventType.COMPACTION
            and CompactAction.LIGHT in event.compaction.actions
            for event in first_events
        )
        assert first_events[-1].content == f"restored {SECRET_DETAIL}"

        memory.append(ChatMessage(role="assistant", content=OLD_ASSISTANT_TEXT))
        for index in range(6):
            role = "user" if index % 2 == 0 else "assistant"
            memory.append(ChatMessage(role=role, content=f"recent small message {index}"))

        second_events = asyncio.run(
            collect_async(agent.run("continue after growth", mode=_mode()))
        )

        assert llm.summary_requests
        assert any(
            event.type is AgentEventType.COMPACTION
            and CompactAction.HEAVY in event.compaction.actions
            for event in second_events
        )
        assert second_events[-1].content == "continued after summary"

        estimator = TokenEstimator()
        safety_line = config.context_window_tokens - CompactPolicy().auto_reserve_tokens
        for messages, tools in llm.normal_requests:
            estimate = estimator.estimate(estimator.snapshot(messages, tools))
            assert estimate.tokens < safety_line
    finally:
        manager.close()


def test_context_compaction_e2e_summary_failures_open_circuit_then_manual_compact_recovers(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    memory = InMemoryConversationMemory()
    memory.append(ChatMessage(role="assistant", content=FAILURE_OLD_TEXT))
    for index in range(5):
        memory.append(ChatMessage(role="user", content=f"recent failure message {index}"))

    llm = FailingThenRecoveringLLM()
    config = CompactConfig(
        context_window_tokens=18_000,
        tool_result_threshold_tokens=3_000,
        tool_batch_threshold_tokens=4_000,
    )
    manager = ContextManager(
        llm=llm,
        memory=memory,
        config=config,
        store=ArchiveSession(workspace, home=tmp_path / "home"),
        policy=CompactPolicy(keep_recent_tokens=1, min_recent_messages=5),
    )
    registry = ToolRegistry([manager.artifact_tool])
    agent = AgentLoop(
        llm=llm,
        memory=memory,
        tool_executor=ToolExecutor(registry),
        tool_registry=registry,
        permission=AllowPermission(),
        context_manager=manager,
        config=AgentConfig(max_rounds=3),
        prompt_builder=create_default_prompt_builder(workspace),
    )

    try:
        events = asyncio.run(
            collect_async(agent.run("continue despite summary failures", mode=_mode()))
        )

        assert len(llm.summary_requests) == 3
        assert all(
            FAILURE_OLD_TEXT[:80] in request[0].content
            for request in llm.summary_requests
        )
        compaction_events = [
            event for event in events if event.type is AgentEventType.COMPACTION
        ]
        assert len(compaction_events) == 1
        report = compaction_events[0].compaction
        assert report.actions == (CompactAction.HEAVY, CompactAction.EMERGENCY)
        assert report.attempts == 3
        assert report.circuit_open is True
        assert report.archived_count >= 1
        assert manager._circuit_open is True
        assert manager._failure_count == 3
        assert len(llm.normal_requests) == 1
        assert events[-1].content == "continued after emergency"

        index_message = next(
            message
            for message in memory.messages()
            if message.origin is MessageOrigin.COMPACT_SUMMARY
            and "emergency_history_index" in message.content
        )
        archived_history = _read_all(manager._store, json.loads(index_message.content)["path"])
        assert FAILURE_OLD_TEXT in archived_history

        manual_events = asyncio.run(collect_async(agent.compact(mode=_mode())))

        assert manual_events[-1].type is AgentEventType.COMPACTION
        assert manual_events[-1].compaction.actions == (CompactAction.HEAVY,)
        assert manual_events[-1].compaction.circuit_open is False
        assert manager._circuit_open is False
        assert manager._failure_count == 0
        assert len(llm.summary_requests) == 4
    finally:
        manager.close()


def test_context_compaction_e2e_archive_commit_failure_blocks_unsafe_request_and_preserves_memory(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    memory = InMemoryConversationMemory()
    memory.append(
        ChatMessage(role="tool", content=UNSAFE_TOOL_TEXT, tool_call_id="unsafe-tool")
    )
    llm = RecordingNormalLLM()
    config = CompactConfig(
        context_window_tokens=18_000,
        tool_result_threshold_tokens=3_000,
        tool_batch_threshold_tokens=4_000,
    )
    store = CommitFailArchiveSession()
    manager = ContextManager(llm=llm, memory=memory, config=config, store=store)
    registry = ToolRegistry([])
    agent = AgentLoop(
        llm=llm,
        memory=memory,
        tool_executor=ToolExecutor(registry),
        tool_registry=registry,
        permission=AllowPermission(),
        context_manager=manager,
        config=AgentConfig(max_rounds=1),
        prompt_builder=create_default_prompt_builder(workspace),
    )

    events = asyncio.run(collect_async(agent.run("do not send unsafe request", mode=_mode())))

    assert llm.normal_requests == []
    assert store.transactions[0].committed is True
    assert store.transactions[0].rolled_back is True
    assert any(message.content == UNSAFE_TOOL_TEXT for message in memory.messages())
    error_events = [event for event in events if event.type is AgentEventType.ERROR]
    assert len(error_events) == 1
    assert error_events[0].error_code is AgentErrorCode.COMPACTION_ERROR
    assert error_events[0].compaction.failure_code is CompactFailureCode.ARCHIVE_ERROR


def _mode():
    from mycode.agent import AgentMode

    return AgentMode()


def _summary_output() -> str:
    body = "\n\n".join(
        f"## {section}\n已压缩旧历史，保留恢复线索。"
        for section in SUMMARY_SECTIONS
    )
    return f"{DRAFT_OPEN}\n草稿。\n{DRAFT_CLOSE}\n{SUMMARY_OPEN}\n{body}\n{SUMMARY_CLOSE}"
