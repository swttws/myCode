from __future__ import annotations

import json
import asyncio
from datetime import datetime, timezone

from mycode.agent import AgentLoop, AgentMode
from mycode.compact.models import CompactAction, CompactReport, CompactStatus, PreparedContext, RequestSnapshot, TokenEstimate
from mycode.llm import BaseLLM, ChatMessage, MessageOrigin, StreamEvent, StreamEventType
from mycode.memory import InMemoryConversationMemory, MemoryPaths, ProjectMemoryManager
from mycode.memory.instructions import InstructionLoader
from mycode.memory.note_prompt import NoteUpdatePrompt
from mycode.memory.notes import MemoryNoteStore
from mycode.memory.sessions import SessionArchiveStore
from mycode.permission.models import PermissionEffect, PermissionMode, PermissionDecision
from mycode.prompt import PromptBuilder
from mycode.prompt.models import EnvironmentSnapshot, PromptConfig, PromptContextBlock, PromptModuleDefinition
from mycode.prompt.registry import PromptRegistry
from mycode.prompt.reminder import ReminderPolicy
from mycode.tool import ToolExecutor, ToolRegistry, ToolDefinition, ToolKind


class FixedEnvironmentCollector:
    def collect(self) -> EnvironmentSnapshot:
        return EnvironmentSnapshot(
            workspace="workspace",
            operating_system="TestOS",
            current_time="2026-07-23T10:00:00+00:00",
            timezone="UTC",
            git_branch="main",
            git_status="clean",
            diagnostics=(),
        )


class StableModule:
    def __init__(self, text: str) -> None:
        self._definition = PromptModuleDefinition("stable", 100)
        self._text = text

    @property
    def definition(self) -> PromptModuleDefinition:
        return self._definition

    def render(self, context) -> str:
        return self._text


class SequencedLLM(BaseLLM):
    def __init__(self, responses: list[list[StreamEvent]]) -> None:
        self._responses = list(responses)
        self.requests: list[list[ChatMessage]] = []
        self.tools: list[list[ToolDefinition]] = []

    async def stream_chat(self, messages, tools=None):
        self.requests.append(list(messages))
        self.tools.append(list(tools or []))
        if not self._responses:
            raise AssertionError("unexpected LLM call")
        for event in self._responses.pop(0):
            yield event


class PassthroughContextManager:
    def __init__(self, memory: InMemoryConversationMemory) -> None:
        self._memory = memory
        self.artifact_tool = _ArtifactTool()

    async def prepare_auto(self, *, build_request, run_deadline):
        del run_deadline
        request = build_request(tuple(self._memory.messages()))
        snapshot = RequestSnapshot(ascii_chars=1, non_ascii_chars=0, fingerprint="project-memory-e2e")
        estimate = TokenEstimate(tokens=1, source="full_chars", delta_tokens=0)
        report = CompactReport(
            status=CompactStatus.SAFE,
            actions=(CompactAction.NONE,),
            before_tokens=1,
            after_tokens=1,
            archived_count=0,
            attempts=0,
            circuit_open=False,
        )
        return PreparedContext(request=request, snapshot=snapshot, estimate=estimate, report=report)

    def record_usage(self, snapshot, usage) -> None:
        del snapshot, usage

    def clear(self) -> None:
        self._memory.clear()

    def close(self) -> None:
        return None


class _ArtifactTool:
    @property
    def definition(self):
        return ToolDefinition(
            name="read_compact_artifact",
            description="Read compact artifact.",
            parameters={"type": "object", "properties": {}, "required": []},
            kind=ToolKind.READ,
        )


class NoopPermission:
    async def before_tool(self, call, definition, *, plan_only, round_index):
        del call, definition, plan_only, round_index
        return PermissionDecision(
            effect=PermissionEffect.DENY,
            reason_code="noop",
            message_zh="noop",
            mode=PermissionMode.DEFAULT,
            display_arguments={},
        )

    def create_approval_request(self, *args, **kwargs):
        raise AssertionError("not used")

    def denied_result(self, call, decision):
        del call, decision
        raise AssertionError("not used")

    async def resolve_approval(self, request, decision):
        del request, decision
        raise AssertionError("not used")

    async def after_tool(self, call, result):
        del call, result
        raise AssertionError("not used")


def _builder() -> PromptBuilder:
    return PromptBuilder(
        registry=PromptRegistry([StableModule("stable instruction")]),
        environment_collector=FixedEnvironmentCollector(),
        reminder_policy=ReminderPolicy(4),
        config=PromptConfig(),
    )


def _project_memory(paths: MemoryPaths, llm: BaseLLM, memory: InMemoryConversationMemory) -> ProjectMemoryManager:
    fixed_now = lambda: datetime(2026, 7, 23, 10, 0, 0, tzinfo=timezone.utc)
    return ProjectMemoryManager(
        paths=paths,
        instructions=InstructionLoader(paths=paths),
        sessions=SessionArchiveStore(paths=paths, now=fixed_now),
        notes=MemoryNoteStore(paths=paths, now=fixed_now),
        note_prompt=NoteUpdatePrompt(),
        llm=llm,
        memory=memory,
    )


async def _collect(async_iterable):
    return [event async for event in async_iterable]


def test_project_memory_keeps_framework_context_when_project_index_is_corrupted(tmp_path):
    workspace = tmp_path / "workspace"
    home = tmp_path / "home"
    workspace.mkdir()
    home.mkdir()
    (workspace / "mycode.md").write_text("root instructions", encoding="utf-8")
    (workspace / ".mycode").mkdir()
    (workspace / ".mycode" / "instructions.md").write_text("project instructions", encoding="utf-8")
    (home / ".mycode").mkdir()
    (home / ".mycode" / "instructions.md").write_text("user instructions", encoding="utf-8")

    paths = MemoryPaths(workspace_root=workspace, home=home)
    paths.ensure_directories()
    (paths.project_memory_dir / "index.md").write_bytes(b"\xff\xfe\x00")

    llm = SequencedLLM(
        [
            [StreamEvent(StreamEventType.TEXT_DELTA, "first answer"), StreamEvent(StreamEventType.DONE)],
            [StreamEvent(StreamEventType.TEXT_DELTA, '{"decisions":[{"action":"ignore","reason":"done"}]}'), StreamEvent(StreamEventType.DONE)],
        ]
    )
    memory = InMemoryConversationMemory()
    project_memory = _project_memory(paths, llm, memory)
    agent = AgentLoop(
        llm=llm,
        memory=memory,
        tool_executor=ToolExecutor(ToolRegistry([])),
        tool_registry=ToolRegistry([]),
        permission=NoopPermission(),
        context_manager=PassthroughContextManager(memory),
        project_memory=project_memory,
        prompt_builder=_builder(),
    )

    async def scenario():
        events = await _collect(agent.run("hello", mode=AgentMode()))
        await asyncio.sleep(0)
        await project_memory.close()
        return events

    events = asyncio.run(scenario())

    assert events[-1].type.name == "FINAL_RESPONSE"
    framework_messages = [message for message in llm.requests[0] if message.origin is MessageOrigin.FRAMEWORK_CONTEXT]
    assert framework_messages
    framework_content = framework_messages[0].content
    assert "root instructions" in framework_content
    assert "project instructions" in framework_content
    assert "user instructions" in framework_content


def test_project_memory_restores_recent_session_injects_memory_and_creates_notes(tmp_path):
    workspace = tmp_path / "workspace"
    home = tmp_path / "home"
    workspace.mkdir()
    home.mkdir()
    (workspace / "mycode.md").write_text("root instructions", encoding="utf-8")
    (workspace / ".mycode").mkdir()
    (workspace / ".mycode" / "instructions.md").write_text("project instructions", encoding="utf-8")
    (home / ".mycode").mkdir()
    (home / ".mycode" / "instructions.md").write_text("user instructions", encoding="utf-8")

    paths = MemoryPaths(workspace_root=workspace, home=home)
    paths.ensure_directories()

    llm = SequencedLLM(
        [
            [StreamEvent(StreamEventType.TEXT_DELTA, "first answer"), StreamEvent(StreamEventType.DONE)],
            [
                StreamEvent(
                    StreamEventType.TEXT_DELTA,
                    '{"decisions":[{"action":"create","scope":"project","kind":"project_knowledge","title":"Project memory note","body":"Remembered project fact.","reason":"capture"}]}',
                ),
                StreamEvent(StreamEventType.DONE),
            ],
            [StreamEvent(StreamEventType.TEXT_DELTA, "second answer"), StreamEvent(StreamEventType.DONE)],
            [
                StreamEvent(
                    StreamEventType.TEXT_DELTA,
                    '{"decisions":[{"action":"ignore","reason":"already captured"}]}',
                ),
                StreamEvent(StreamEventType.DONE),
            ],
        ]
    )

    async def run_round(agent, project_memory, user_text):
        events = await _collect(agent.run(user_text, mode=AgentMode()))
        await asyncio.sleep(0)
        await project_memory.close()
        return events

    memory1 = InMemoryConversationMemory()
    project_memory1 = _project_memory(paths, llm, memory1)
    agent1 = AgentLoop(
        llm=llm,
        memory=memory1,
        tool_executor=ToolExecutor(ToolRegistry([])),
        tool_registry=ToolRegistry([]),
        permission=NoopPermission(),
        context_manager=PassthroughContextManager(memory1),
        project_memory=project_memory1,
        prompt_builder=_builder(),
    )
    first_events = asyncio.run(run_round(agent1, project_memory1, "remember this project fact"))

    note_index_path = paths.project_memory_dir / "index.md"
    assert first_events[-1].type.name == "FINAL_RESPONSE"
    assert note_index_path.exists()
    assert "Project memory note" in note_index_path.read_text(encoding="utf-8")

    memory2 = InMemoryConversationMemory()
    project_memory2 = _project_memory(paths, llm, memory2)
    agent2 = AgentLoop(
        llm=llm,
        memory=memory2,
        tool_executor=ToolExecutor(ToolRegistry([])),
        tool_registry=ToolRegistry([]),
        permission=NoopPermission(),
        context_manager=PassthroughContextManager(memory2),
        project_memory=project_memory2,
        prompt_builder=_builder(),
    )
    second_events = asyncio.run(run_round(agent2, project_memory2, "what about now?"))

    assert second_events[-1].type.name == "FINAL_RESPONSE"
    restored_request = llm.requests[2]
    framework_messages = [message for message in restored_request if message.origin is MessageOrigin.FRAMEWORK_CONTEXT]
    assert framework_messages
    framework_content = framework_messages[0].content
    assert "root instructions" in framework_content
    assert "project instructions" in framework_content
    assert "user instructions" in framework_content
    assert "Project memory note" in framework_content
    assert any(message.content == "remember this project fact" for message in restored_request)
    assert any(message.content == "first answer" for message in restored_request)


def test_project_memory_faults_are_recovered_or_blocked_safely(tmp_path):
    workspace = tmp_path / "workspace"
    home = tmp_path / "home"
    workspace.mkdir()
    home.mkdir()
    (workspace / "mycode.md").write_text("root instructions", encoding="utf-8")
    (workspace / ".mycode").mkdir()
    (workspace / ".mycode" / "instructions.md").write_text("project instructions", encoding="utf-8")
    (home / ".mycode").mkdir()
    (home / ".mycode" / "instructions.md").write_text("user instructions", encoding="utf-8")

    paths = MemoryPaths(workspace_root=workspace, home=home)
    paths.ensure_directories()
    (paths.project_memory_dir / "index.md").write_bytes(b"\xff\xfe\x00")

    fixed_now = lambda: datetime(2026, 7, 23, 10, 0, 0, tzinfo=timezone.utc)
    def seed_session(manager: ProjectMemoryManager) -> None:
        session_record_path = paths.sessions_dir / f"{manager._sessions.current_session_id}.jsonl"
        records = [
            {
                "type": "message",
                "timestamp": "2026-07-23T09:00:00+00:00",
                "role": "user",
                "content": "restore me",
                "origin": "conversation",
            },
            "not-json",
            {
                "type": "message",
                "timestamp": "2026-07-23T09:01:00+00:00",
                "role": "assistant",
                "content": "tool call",
                "origin": "conversation",
                "tool_call_id": "tool-1",
                "tool_name": "noop",
                "tool_arguments": "{}",
            },
            {
                "type": "message",
                "timestamp": "2026-07-23T09:02:00+00:00",
                "role": "user",
                "content": "after boundary",
                "origin": "conversation",
            },
        ]
        lines = [json.dumps(record, ensure_ascii=False) if isinstance(record, dict) else record for record in records]
        session_record_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    async def compact_success(restored_history):
        return tuple(restored_history) + (ChatMessage(role="assistant", content="safe compacted history"),)

    async def compact_failure(restored_history):
        del restored_history
        raise RuntimeError("compaction exploded")

    async def scenario():
        manager1 = ProjectMemoryManager(
            paths=paths,
            instructions=InstructionLoader(paths=paths),
            sessions=SessionArchiveStore(paths=paths, now=fixed_now),
            notes=MemoryNoteStore(paths=paths, now=fixed_now),
            note_prompt=NoteUpdatePrompt(),
            llm=SequencedLLM([]),
            memory=InMemoryConversationMemory(),
        )
        seed_session(manager1)
        success_context = await manager1.before_user_request(compact_prepare=compact_success)
        assert manager1._memory.messages() == [
            ChatMessage(role="user", content="restore me"),
            ChatMessage(role="assistant", content="safe compacted history"),
        ]
        await manager1.close()

        manager2 = ProjectMemoryManager(
            paths=paths,
            instructions=InstructionLoader(paths=paths),
            sessions=SessionArchiveStore(paths=paths, now=fixed_now),
            notes=MemoryNoteStore(paths=paths, now=fixed_now),
            note_prompt=NoteUpdatePrompt(),
            llm=SequencedLLM([]),
            memory=InMemoryConversationMemory(),
        )
        failure_context = await manager2.before_user_request(compact_prepare=compact_failure)
        await manager2.close()
        return success_context, failure_context, manager2

    success_context, failure_context, _manager = asyncio.run(scenario())

    success_codes = {diagnostic.code for diagnostic in success_context.diagnostics}
    assert {"session_json_invalid", "session_tool_boundary_truncated", "memory_index_unreadable"} <= success_codes
    assert any(block.kind.value == "instructions" for block in success_context.blocks)
    assert any(block.kind.value == "memory_index" for block in success_context.blocks)
    assert success_context.restored_history == (
        ChatMessage(role="user", content="restore me"),
        ChatMessage(role="assistant", content="safe compacted history"),
    )

    failure_codes = {diagnostic.code for diagnostic in failure_context.diagnostics}
    assert "restore_compaction_failed" in failure_codes
    assert "memory_index_unreadable" in failure_codes
    assert failure_context.restored_history == ()
