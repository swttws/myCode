from __future__ import annotations

import asyncio
from dataclasses import dataclass

from mycode.llm import BaseLLM, ChatMessage, StreamEvent, StreamEventType
from mycode.memory import InMemoryConversationMemory, MemoryPaths, ProjectMemoryManager, create_project_memory_manager
from mycode.memory.manager import ProjectMemoryManager
from mycode.memory.models import (
    FrameworkContext,
    FrameworkContextBlock,
    FrameworkContextKind,
    InstructionLoadResult,
    MemoryDiagnostic,
    MemoryIndexBundle,
    MemoryScope,
    NoteUpdateDecision,
    NoteUpdateResult,
    SessionRestoreResult,
    SessionSummary,
)


@dataclass
class FakeInstructions:
    result: InstructionLoadResult
    load_calls: int = 0

    def load(self):
        self.load_calls += 1
        return self.result


class FakeSessions:
    def __init__(self, restore_result: SessionRestoreResult) -> None:
        self.restore_result = restore_result
        self.cleanup_calls = 0
        self.restore_calls = 0
        self.appended: list[ChatMessage] = []
        self.start_new_session_calls = 0
        self.close_calls = 0
        self.current_session_id = "session-current"

    def cleanup_expired(self):
        self.cleanup_calls += 1
        return ()

    def restore_latest(self):
        self.restore_calls += 1
        return self.restore_result

    def append_message(self, message):
        self.appended.append(message)

    def start_new_session(self):
        self.start_new_session_calls += 1
        self.current_session_id = f"session-new-{self.start_new_session_calls}"

    def close(self):
        self.close_calls += 1


class FakeNotes:
    def __init__(self) -> None:
        self.load_index_calls: list[MemoryScope] = []
        self.apply_calls = []

    def load_index_bundle(self, scope):
        self.load_index_calls.append(scope)
        return MemoryIndexBundle(
            scope=scope,
            entries=(f"- {scope.value} note",),
            rendered_text=f"- {scope.value} note",
            line_count=1,
            byte_count=len(scope.value) + 7,
            truncated=False,
        )

    def apply_decisions(self, decisions, *, source_session_id):
        self.apply_calls.append((tuple(decisions), source_session_id))
        return NoteUpdateResult(created=0, merged=0, updated=0, ignored=len(decisions))


class FakeNotePrompt:
    def __init__(self) -> None:
        self.build_calls = []
        self.parse_calls = []

    def build(self, *, user_message, assistant_message, user_index, project_index):
        self.build_calls.append((user_message, assistant_message, user_index, project_index))
        return ChatMessage(role="user", content="note update prompt")

    def parse(self, text):
        self.parse_calls.append(text)
        return (NoteUpdateDecision(action="ignore", reason="test"),)


class FakeLLM(BaseLLM):
    def __init__(self, chunks: tuple[StreamEvent, ...]) -> None:
        self.chunks = chunks
        self.requests = []
        self.tools = []

    async def stream_chat(self, messages, tools=None):
        self.requests.append(messages)
        self.tools.append(tools)
        for chunk in self.chunks:
            yield chunk


class HangingLLM(BaseLLM):
    def __init__(self) -> None:
        self.started = asyncio.Event()

    async def stream_chat(self, messages, tools=None):
        self.started.set()
        await asyncio.Future()
        yield StreamEvent(StreamEventType.DONE)


class RaisingLLM(BaseLLM):
    async def stream_chat(self, messages, tools=None):
        raise RuntimeError("Invalid JSON in SSE data.")
        yield StreamEvent(StreamEventType.DONE)


def _summary() -> SessionSummary:
    return SessionSummary(
        session_id="session-1",
        path="session-1.jsonl",
        title="Old work",
        message_count=1,
        updated_at="2026-07-22T10:00:00+00:00",
        recoverable=True,
    )


def _restore_result(*, history: tuple[ChatMessage, ...] = ()) -> SessionRestoreResult:
    return SessionRestoreResult(
        summary=_summary() if history else None,
        history=history,
        skipped_lines=0,
        truncated_at_boundary=False,
    )


def _manager(*, restore_result: SessionRestoreResult):
    instructions = FakeInstructions(
        InstructionLoadResult(blocks=(), rendered_text="project instructions", diagnostics=())
    )
    sessions = FakeSessions(restore_result)
    notes = FakeNotes()
    note_prompt = FakeNotePrompt()
    llm = FakeLLM(
        (
            StreamEvent(StreamEventType.TEXT_DELTA, '{"decisions": [{"action": "ignore", "reason": "test"}]}'),
            StreamEvent(StreamEventType.DONE),
        )
    )
    memory = InMemoryConversationMemory()
    manager = ProjectMemoryManager(
        paths=None,
        instructions=instructions,
        sessions=sessions,
        notes=notes,
        note_prompt=note_prompt,
        llm=llm,
        memory=memory,
    )
    return manager, instructions, sessions, notes, note_prompt, llm, memory


def test_project_memory_manager_refreshes_context_and_restores_only_once():
    old_history = (ChatMessage(role="user", content="old request"),)
    manager, instructions, sessions, notes, _note_prompt, _llm, memory = _manager(
        restore_result=_restore_result(history=old_history)
    )

    async def compact_prepare(messages):
        return tuple(messages) + (ChatMessage(role="assistant", content="compact summary"),)

    first = asyncio.run(manager.before_user_request(compact_prepare=compact_prepare))
    second = asyncio.run(manager.before_user_request(compact_prepare=compact_prepare))

    assert sessions.cleanup_calls == 2
    assert sessions.restore_calls == 1
    assert instructions.load_calls == 2
    assert notes.load_index_calls == [
        MemoryScope.USER,
        MemoryScope.PROJECT,
        MemoryScope.USER,
        MemoryScope.PROJECT,
    ]
    assert memory.messages() == [
        ChatMessage(role="user", content="old request"),
        ChatMessage(role="assistant", content="compact summary"),
    ]
    assert first.session_summary == _summary()
    assert {block.kind for block in first.blocks} == {
        FrameworkContextKind.INSTRUCTIONS,
        FrameworkContextKind.MEMORY_INDEX,
    }
    assert "project instructions" in first.blocks[0].content
    assert "## 用户记忆" in first.blocks[1].content
    assert "## 项目记忆" in first.blocks[1].content
    assert "user note" in first.blocks[1].content
    assert "project note" in first.blocks[1].content
    assert second.restored_history == ()


def test_project_memory_manager_reports_compaction_unavailable_and_failed_without_sensitive_history():
    old_history = (ChatMessage(role="user", content="SECRET RESTORE BODY"),)
    manager, _instructions, _sessions, _notes, _note_prompt, _llm, memory = _manager(
        restore_result=_restore_result(history=old_history)
    )

    no_callback = asyncio.run(manager.before_user_request(compact_prepare=None))

    assert memory.messages() == list(old_history)
    assert any(diagnostic.code == "restore_compaction_unavailable" for diagnostic in no_callback.diagnostics)
    assert "SECRET RESTORE BODY" not in "\n".join(diagnostic.message for diagnostic in no_callback.diagnostics)

    manager, _instructions, _sessions, _notes, _note_prompt, _llm, memory = _manager(
        restore_result=_restore_result(history=old_history)
    )

    async def failing_compact(messages):
        raise RuntimeError("too many tokens")

    failed = asyncio.run(manager.before_user_request(compact_prepare=failing_compact))

    assert memory.messages() == []
    assert any(diagnostic.code == "restore_compaction_failed" for diagnostic in failed.diagnostics)
    assert "SECRET RESTORE BODY" not in "\n".join(diagnostic.message for diagnostic in failed.diagnostics)


def test_project_memory_manager_includes_restore_notice_when_gap_exceeds_threshold():
    restore_notice = FrameworkContextBlock(
        id="restore-notice",
        kind=FrameworkContextKind.RESTORE_NOTICE,
        priority=150,
        content="Restored after a long gap.",
    )
    restore_result = SessionRestoreResult(
        summary=_summary(),
        history=(ChatMessage(role="user", content="old request"),),
        skipped_lines=0,
        truncated_at_boundary=False,
        time_gap_seconds=90_000,
        time_gap_block=restore_notice,
    )
    manager, _instructions, _sessions, _notes, _note_prompt, _llm, _memory = _manager(
        restore_result=restore_result
    )

    context = asyncio.run(manager.before_user_request(compact_prepare=None))

    assert restore_notice in context.blocks


def test_project_memory_manager_records_messages_once_and_updates_notes_in_background():
    manager, _instructions, sessions, notes, note_prompt, llm, _memory = _manager(
        restore_result=_restore_result()
    )
    user_message = ChatMessage(role="user", content="remember pytest")
    assistant_message = ChatMessage(role="assistant", content="noted")
    framework_context = FrameworkContext(blocks=(), restored_history=())

    async def scenario():
        manager.record_user_message(user_message)
        manager.record_assistant_message(assistant_message)
        manager.after_final_response(
            user_message=user_message,
            assistant_message=assistant_message,
            framework_context=framework_context,
        )
        await asyncio.sleep(0)
        await manager.close()

    asyncio.run(scenario())

    assert sessions.appended == [user_message, assistant_message]
    assert len(note_prompt.build_calls) == 1
    assert llm.tools == [[]]
    assert note_prompt.parse_calls == ['{"decisions": [{"action": "ignore", "reason": "test"}]}']
    assert notes.apply_calls[0][1] == "session-current"


def test_project_memory_manager_clear_and_close_lifecycle():
    manager, _instructions, sessions, _notes, _note_prompt, _llm, _memory = _manager(
        restore_result=_restore_result(history=(ChatMessage(role="user", content="old"),))
    )

    async def scenario():
        await manager.before_user_request(compact_prepare=None)
        manager.clear_session_state()
        await manager.before_user_request(compact_prepare=None)
        await manager.close()
        await manager.close()

    asyncio.run(scenario())

    assert sessions.start_new_session_calls == 1
    assert sessions.restore_calls == 1
    assert sessions.close_calls == 1


def test_project_memory_manager_close_cancels_background_note_tasks():
    instructions = FakeInstructions(
        InstructionLoadResult(blocks=(), rendered_text="project instructions", diagnostics=())
    )
    sessions = FakeSessions(_restore_result())
    notes = FakeNotes()
    note_prompt = FakeNotePrompt()
    llm = HangingLLM()
    memory = InMemoryConversationMemory()
    manager = ProjectMemoryManager(
        paths=None,
        instructions=instructions,
        sessions=sessions,
        notes=notes,
        note_prompt=note_prompt,
        llm=llm,
        memory=memory,
    )

    async def scenario():
        manager.after_final_response(
            user_message=ChatMessage(role="user", content="remember"),
            assistant_message=ChatMessage(role="assistant", content="ok"),
            framework_context=FrameworkContext(blocks=(), restored_history=()),
        )
        await llm.started.wait()
        await manager.close()
        await manager.close()

    asyncio.run(scenario())

    assert sessions.close_calls == 1


def test_project_memory_manager_prints_background_llm_error(capsys):
    manager, _instructions, _sessions, _notes, _note_prompt, _llm, _memory = _manager(
        restore_result=_restore_result()
    )
    manager._llm = FakeLLM((StreamEvent(StreamEventType.ERROR, "模型限流"),))

    async def scenario():
        manager.after_final_response(
            user_message=ChatMessage(role="user", content="remember"),
            assistant_message=ChatMessage(role="assistant", content="ok"),
            framework_context=FrameworkContext(blocks=(), restored_history=()),
        )
        await asyncio.sleep(0)
        await manager.close()

    asyncio.run(scenario())

    captured = capsys.readouterr()
    assert "myCode 记忆更新错误：memory_note_llm_error: 模型限流" in captured.err


def test_project_memory_manager_prints_background_llm_exception(capsys):
    manager, _instructions, _sessions, _notes, _note_prompt, _llm, _memory = _manager(
        restore_result=_restore_result()
    )
    manager._llm = RaisingLLM()

    async def scenario():
        manager.after_final_response(
            user_message=ChatMessage(role="user", content="remember"),
            assistant_message=ChatMessage(role="assistant", content="ok"),
            framework_context=FrameworkContext(blocks=(), restored_history=()),
        )
        await asyncio.sleep(0)
        await manager.close()

    asyncio.run(scenario())

    captured = capsys.readouterr()
    assert "myCode 记忆更新错误：memory_note_update_failed: Invalid JSON in SSE data." in captured.err


def test_memory_package_exports_factory_and_paths():
    assert MemoryPaths is not None
    assert ProjectMemoryManager is not None
    assert callable(create_project_memory_manager)


def test_create_project_memory_manager_wires_default_components(tmp_path, monkeypatch):
    import mycode.memory.manager as manager_module

    calls: dict[str, object] = {}

    class FakeMemoryPaths:
        def __init__(self, *, workspace_root, home):
            calls["paths"] = (workspace_root, home)
            self.workspace_root = workspace_root
            self.home = home
            self.ensure_calls = 0

        def ensure_directories(self):
            self.ensure_calls += 1
            calls["ensure_directories"] = self.ensure_calls

    class FakeInstructionLoader:
        def __init__(self, *, paths):
            calls["instructions"] = paths

    class FakeSessionArchiveStore:
        def __init__(self, *, paths, now):
            calls["sessions"] = (paths, now)

    class FakeMemoryNoteStore:
        def __init__(self, *, paths, now):
            calls["notes"] = (paths, now)

    class FakeNoteUpdatePrompt:
        def __init__(self):
            calls["prompt"] = True

    class FakeProjectMemoryManager:
        def __init__(self, **kwargs):
            calls["manager_kwargs"] = kwargs

    monkeypatch.setattr(manager_module, "MemoryPaths", FakeMemoryPaths)
    monkeypatch.setattr(manager_module, "InstructionLoader", FakeInstructionLoader)
    monkeypatch.setattr(manager_module, "SessionArchiveStore", FakeSessionArchiveStore)
    monkeypatch.setattr(manager_module, "MemoryNoteStore", FakeMemoryNoteStore)
    monkeypatch.setattr(manager_module, "NoteUpdatePrompt", FakeNoteUpdatePrompt)
    monkeypatch.setattr(manager_module, "ProjectMemoryManager", FakeProjectMemoryManager)

    workspace = tmp_path / "workspace"
    home = tmp_path / "home"
    result = manager_module.create_project_memory_manager(
        workspace_root=workspace,
        home=home,
        llm=object(),
        memory=object(),
        now=lambda: _dt(2026, 7, 23, 10, 0, 0),
    )

    assert isinstance(result, FakeProjectMemoryManager)
    assert calls["paths"] == (workspace, home)
    assert calls["ensure_directories"] == 1
    assert calls["instructions"].workspace_root == workspace
    assert calls["sessions"][0].workspace_root == workspace
    assert calls["notes"][0].workspace_root == workspace
    assert calls["prompt"] is True
    assert calls["manager_kwargs"]["llm"].__class__ is object
    assert calls["manager_kwargs"]["memory"].__class__ is object
