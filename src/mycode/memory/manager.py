from __future__ import annotations

import asyncio
import sys
from collections.abc import Awaitable, Callable, Sequence
from typing import Any

from mycode.llm import BaseLLM, ChatMessage, MessageOrigin, StreamEventType
from mycode.memory.base import ConversationMemory
from mycode.memory.instructions import InstructionLoader
from mycode.memory.models import (
    FrameworkContext,
    FrameworkContextBlock,
    FrameworkContextKind,
    MemoryDiagnostic,
    MemoryKind,
    MemoryNote,
    MemoryScope,
)
from mycode.memory.paths import MemoryPaths
from mycode.memory.note_prompt import NoteUpdatePrompt
from mycode.memory.notes import MemoryNoteStore
from mycode.memory.sessions import SessionArchiveStore
from mycode.memory.tools import ReadMemoryNoteTool


_USER_DIRECT_MEMORY_KINDS = frozenset({MemoryKind.USER_PREFERENCE, MemoryKind.CORRECTION})


class ProjectMemoryManager:
    def __init__(
        self,
        *,
        paths: MemoryPaths | None,
        instructions: Any,
        sessions: Any,
        notes: Any,
        note_prompt: Any,
        llm: BaseLLM,
        memory: ConversationMemory,
        time_gap_notice_seconds: int = 86_400,
    ) -> None:
        self._paths = paths
        self._instructions = instructions
        self._sessions = sessions
        self._notes = notes
        self._note_prompt = note_prompt
        self._llm = llm
        self._memory = memory
        self._memory_note_tool = ReadMemoryNoteTool(notes)
        self._time_gap_notice_seconds = time_gap_notice_seconds
        self._restored = False
        self._closed = False
        self._recorded_message_ids: set[int] = set()
        self._background_tasks: set[asyncio.Task[None]] = set()
        self._background_diagnostics: list[MemoryDiagnostic] = []

    @property
    def memory_note_tool(self) -> ReadMemoryNoteTool:
        return self._memory_note_tool

    async def before_user_request(
        self,
        *,
        compact_prepare: Callable[[Sequence[ChatMessage]], Awaitable[tuple[ChatMessage, ...]]] | None,
    ) -> FrameworkContext:
        diagnostics: list[MemoryDiagnostic] = []
        blocks: list[FrameworkContextBlock] = []
        restored_history: tuple[ChatMessage, ...] = ()
        session_summary = None

        diagnostics.extend(_call_diagnostics(self._sessions.cleanup_expired, "session_cleanup_failed"))

        if not self._restored:
            restore_result = self._sessions.restore_latest()
            self._restored = True
            session_summary = restore_result.summary
            diagnostics.extend(restore_result.diagnostics)
            restored_history = tuple(restore_result.history)
            if restored_history:
                compacted = await self._prepare_restored_history(
                    restored_history,
                    compact_prepare=compact_prepare,
                    diagnostics=diagnostics,
                    session_path=session_summary.path if session_summary is not None else None,
                )
                if compacted is not None:
                    self._memory.replace(compacted)
                    restored_history = tuple(compacted)
                else:
                    restored_history = ()
            if (
                restore_result.time_gap_block is not None
                and restore_result.time_gap_seconds is not None
                and restore_result.time_gap_seconds > self._time_gap_notice_seconds
            ):
                blocks.append(restore_result.time_gap_block)

        instruction_result = self._instructions.load()
        diagnostics.extend(instruction_result.diagnostics)
        if instruction_result.rendered_text:
            blocks.append(
                FrameworkContextBlock(
                    id="instructions",
                    kind=FrameworkContextKind.INSTRUCTIONS,
                    priority=100,
                    content=instruction_result.rendered_text,
                )
            )

        user_index = self._notes.load_index_bundle(MemoryScope.USER)
        project_index = self._notes.load_index_bundle(MemoryScope.PROJECT)
        user_notes = self._notes.load_notes(MemoryScope.USER)
        diagnostics.extend(user_index.diagnostics)
        diagnostics.extend(project_index.diagnostics)
        blocks.append(
            FrameworkContextBlock(
                id="memory-index",
                kind=FrameworkContextKind.MEMORY_INDEX,
                priority=200,
                content=_render_memory_index(
                    user_index.rendered_text,
                    project_index.rendered_text,
                    _direct_user_notes(user_notes),
                ),
            )
        )

        return FrameworkContext(
            blocks=tuple(sorted(blocks, key=lambda block: (block.priority, block.id))),
            restored_history=restored_history,
            session_summary=session_summary,
            diagnostics=tuple(diagnostics),
        )

    def record_user_message(self, message: ChatMessage) -> None:
        self._record_message_once(message)

    def record_assistant_message(self, message: ChatMessage) -> None:
        self._record_message_once(message)

    def record_tool_history(
        self,
        *,
        assistant_tool_call: ChatMessage | None = None,
        tool_result: ChatMessage | None = None,
    ) -> None:
        if assistant_tool_call is not None:
            self._record_message_once(assistant_tool_call)
        if tool_result is not None:
            self._record_message_once(tool_result)

    def after_final_response(
        self,
        *,
        user_message: ChatMessage,
        assistant_message: ChatMessage,
        framework_context: FrameworkContext,
    ) -> None:
        self._record_message_once(user_message)
        self._record_message_once(assistant_message)
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        task = loop.create_task(
            self._update_notes_async(
                user_message=user_message,
                assistant_message=assistant_message,
                framework_context=framework_context,
            )
        )
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

    def clear_session_state(self) -> None:
        self._sessions.start_new_session()
        self._recorded_message_ids.clear()
        # /clear 后短期上下文已经被用户显式清空，本进程内不再把旧 JSONL 自动覆写回来。
        self._restored = True

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        pending = [task for task in self._background_tasks if not task.done()]
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        self._background_tasks.clear()
        self._sessions.close()

    async def _prepare_restored_history(
        self,
        restored_history: tuple[ChatMessage, ...],
        *,
        compact_prepare: Callable[[Sequence[ChatMessage]], Awaitable[tuple[ChatMessage, ...]]] | None,
        diagnostics: list[MemoryDiagnostic],
        session_path: str | None,
    ) -> tuple[ChatMessage, ...] | None:
        if compact_prepare is None:
            diagnostics.append(
                MemoryDiagnostic(
                    code="restore_compaction_unavailable",
                    message="restored history was loaded without a compaction callback",
                    path=session_path,
                )
            )
            return restored_history
        try:
            return tuple(await compact_prepare(restored_history))
        except Exception as exc:
            diagnostics.append(
                MemoryDiagnostic(
                    code="restore_compaction_failed",
                    message=str(exc),
                    path=session_path,
                )
            )
            return None

    def _record_message_once(self, message: ChatMessage) -> None:
        if message.origin is not MessageOrigin.CONVERSATION:
            return
        message_id = id(message)
        if message_id in self._recorded_message_ids:
            return
        self._sessions.append_message(message)
        self._recorded_message_ids.add(message_id)

    async def _update_notes_async(
        self,
        *,
        user_message: ChatMessage,
        assistant_message: ChatMessage,
        framework_context: FrameworkContext,
    ) -> None:
        del framework_context
        try:
            user_index = self._notes.load_index_bundle(MemoryScope.USER)
            project_index = self._notes.load_index_bundle(MemoryScope.PROJECT)
            prompt = self._note_prompt.build(
                user_message=user_message,
                assistant_message=assistant_message,
                user_index=user_index,
                project_index=project_index,
            )
            text_parts: list[str] = []
            async for event in self._llm.stream_chat([prompt], tools=[]):
                if event.type is StreamEventType.TEXT_DELTA:
                    text_parts.append(event.content)
                elif event.type is StreamEventType.TOOL_CALL:
                    self._background_diagnostics.append(
                        MemoryDiagnostic(
                            code="memory_note_tool_call_returned",
                            message="note update model returned a tool call",
                        )
                    )
                    return
                elif event.type is StreamEventType.ERROR:
                    diagnostic = MemoryDiagnostic(
                        code="memory_note_llm_error",
                        message=event.content or "note update model returned an error",
                    )
                    self._background_diagnostics.append(diagnostic)
                    _print_background_error(diagnostic)
                    return
            decisions = self._note_prompt.parse("".join(text_parts))
            result = self._notes.apply_decisions(
                decisions,
                source_session_id=getattr(self._sessions, "current_session_id", None),
            )
            self._background_diagnostics.extend(result.diagnostics)
        except asyncio.CancelledError:
            # 关闭时取消后台笔记，避免退出流程被网络或模型调用拖住。
            raise
        except Exception as exc:
            diagnostic = MemoryDiagnostic(
                code="memory_note_update_failed",
                message=str(exc),
            )
            self._background_diagnostics.append(diagnostic)
            _print_background_error(diagnostic)


def _print_background_error(diagnostic: MemoryDiagnostic) -> None:
    print(f"myCode 记忆更新错误：{diagnostic.code}: {diagnostic.message}", file=sys.stderr)


def _render_memory_index(
    user_text: str,
    project_text: str,
    direct_user_notes: Sequence[MemoryNote],
) -> str:
    return "\n".join(
        [
            "## 长期记忆使用规则",
            "- 用户记忆中的 user_preference/correction 是当前请求的行为约束；当相关时必须遵循。",
            "- 最新用户明确要求优先于长期记忆中的旧偏好。",
            "- 项目记忆索引只提供摘要；需要完整正文时调用 read_memory_note(scope, note_id)。",
            "",
            "## 用户记忆",
            "### 已加载偏好与纠正正文",
            _render_direct_user_notes(direct_user_notes),
            "",
            "### 用户记忆索引",
            user_text or "（空）",
            "",
            "## 项目记忆索引",
            project_text or "（空）",
        ]
    )


def _direct_user_notes(notes: Sequence[MemoryNote]) -> tuple[MemoryNote, ...]:
    return tuple(note for note in notes if note.kind in _USER_DIRECT_MEMORY_KINDS)


def _render_direct_user_notes(notes: Sequence[MemoryNote]) -> str:
    if not notes:
        return "（空）"
    lines: list[str] = []
    for note in notes:
        title = note.frontmatter.get("title", note.note_id)
        lines.append(f"- {title} ({note.kind.value}, id: {note.note_id}, updated: {note.updated_at})")
        body = note.body.strip()
        if body:
            lines.extend(f"  {line}" for line in body.splitlines())
    return "\n".join(lines)


def _call_diagnostics(func: Callable[[], Sequence[MemoryDiagnostic]], code: str) -> tuple[MemoryDiagnostic, ...]:
    try:
        return tuple(func())
    except Exception as exc:
        return (MemoryDiagnostic(code=code, message=str(exc)),)


def create_project_memory_manager(
    *,
    workspace_root,
    home,
    llm: BaseLLM,
    memory: ConversationMemory,
    now: Callable[[], object],
    time_gap_notice_seconds: int = 86_400,
) -> ProjectMemoryManager:
    paths = MemoryPaths(workspace_root=workspace_root, home=home)
    paths.ensure_directories()
    return ProjectMemoryManager(
        paths=paths,
        instructions=InstructionLoader(paths=paths),
        sessions=SessionArchiveStore(paths=paths, now=now),
        notes=MemoryNoteStore(paths=paths, now=now),
        note_prompt=NoteUpdatePrompt(),
        llm=llm,
        memory=memory,
        time_gap_notice_seconds=time_gap_notice_seconds,
    )
