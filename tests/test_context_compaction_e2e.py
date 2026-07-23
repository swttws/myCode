import asyncio
import json

from mycode.agent import AgentConfig, AgentEventType, AgentLoop
from mycode.compact.archive import ArchiveSession
from mycode.compact.estimator import TokenEstimator
from mycode.compact.manager import ContextManager
from mycode.compact.models import CompactAction, CompactConfig, CompactPolicy
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


def _mode():
    from mycode.agent import AgentMode

    return AgentMode()


def _summary_output() -> str:
    body = "\n\n".join(
        f"## {section}\n已压缩旧历史，保留恢复线索。"
        for section in SUMMARY_SECTIONS
    )
    return f"{DRAFT_OPEN}\n草稿。\n{DRAFT_CLOSE}\n{SUMMARY_OPEN}\n{body}\n{SUMMARY_CLOSE}"
