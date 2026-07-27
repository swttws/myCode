from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from mycode.compact.models import (
    CompactAction,
    CompactReport,
    CompactStatus,
    ContextTokenStatus,
    PreparedContext,
    RequestSnapshot,
    TokenEstimate,
)
from mycode.llm import ChatMessage, MessageOrigin


class SkillContextTooLarge(RuntimeError):
    pass


def select_completed_turns(
    history: Sequence[ChatMessage],
    count: int,
) -> tuple[ChatMessage, ...]:
    if count <= 0:
        return ()
    turns: list[tuple[ChatMessage, ...]] = []
    current: list[ChatMessage] = []
    for message in history:
        if _is_conversation_user(message):
            if _is_completed_turn(current):
                turns.append(tuple(current))
            current = [message]
            continue
        if current:
            current.append(message)
    if _is_completed_turn(current):
        turns.append(tuple(current))
    return tuple(message for turn in turns[-count:] for message in turn)


def build_summary_prompt(history: Sequence[ChatMessage]) -> str:
    lines = [
        "请将以下主对话概括为独立任务所需的背景。",
        "保留用户目标、已确认约束、关键技术事实、已完成工作和未解决问题。",
        "不要添加新结论，不要复述工具原始输出，不要给出执行建议。",
        "",
        "主对话：",
    ]
    for message in history:
        lines.append(f"{message.role}: {message.content}")
    return "\n".join(lines)


class EphemeralContextManager:
    def __init__(self, memory, *, max_chars: int = 120_000) -> None:
        self._memory = memory
        self._max_chars = max_chars
        self.record_usage_calls = []
        self.clear_calls = 0
        self.close_calls = 0

    async def prepare_auto(self, *, build_request, run_deadline):
        return self.prepare_now(build_request=build_request)

    def prepare_now(self, *, build_request):
        history = self._history()
        char_count = sum(len(message.content or "") for message in history)
        if char_count > self._max_chars:
            raise SkillContextTooLarge("independent context too large")
        request = build_request(history)
        return PreparedContext(
            request=request,
            snapshot=RequestSnapshot(ascii_chars=char_count, non_ascii_chars=0, fingerprint="ephemeral"),
            estimate=TokenEstimate(tokens=char_count // 4, source="full_chars", delta_tokens=0),
            report=CompactReport(
                status=CompactStatus.SAFE,
                actions=(CompactAction.NONE,),
                before_tokens=char_count // 4,
                after_tokens=char_count // 4,
                archived_count=0,
                attempts=0,
                circuit_open=False,
            ),
        )

    def record_usage(self, snapshot, usage) -> None:
        self.record_usage_calls.append((snapshot, usage))

    def estimate_current(self, *, build_request) -> ContextTokenStatus:
        history = self._history()
        char_count = sum(len(message.content or "") for message in history)
        return ContextTokenStatus(
            estimated_tokens=char_count // 4,
            context_window_tokens=max(self._max_chars // 4, 1),
            usage_ratio=0.0 if self._max_chars == 0 else min(char_count / self._max_chars, 1.0),
            source="ephemeral",
        )

    def clear(self) -> None:
        self.clear_calls += 1
        if hasattr(self._memory, "clear"):
            self._memory.clear()

    def close(self) -> None:
        self.close_calls += 1

    def _history(self) -> tuple[ChatMessage, ...]:
        messages = self._memory.messages if hasattr(self._memory, "messages") else None
        if callable(messages):
            return tuple(messages())
        return tuple(self._memory)


def _is_conversation_user(message: ChatMessage) -> bool:
    return message.role == "user" and message.origin is MessageOrigin.CONVERSATION


def _is_completed_turn(messages: list[ChatMessage]) -> bool:
    if not messages or not _is_conversation_user(messages[0]):
        return False
    return any(message.role == "assistant" and bool(message.content) for message in messages[1:])
