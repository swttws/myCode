from __future__ import annotations

import asyncio
import logging
from collections import deque
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import replace
from typing import AsyncIterator

from mycode.agent.events import AgentEvent, AgentEventType


logger = logging.getLogger(__name__)

_current_render_bus: ContextVar["RenderEventBus | None"] = ContextVar(
    "subagent_render_bus",
    default=None,
)


class RenderEventBus:
    def __init__(self, *, max_pending: int = 128) -> None:
        if type(max_pending) is not int or max_pending <= 0:
            raise ValueError("max_pending must be a positive integer.")
        self._max_pending = max_pending
        self._pending: deque[AgentEvent] = deque()
        self._condition = asyncio.Condition()
        self._producer_done = False
        self._next_sequence = 1
        self._dropped_mergeable = 0

    @property
    def pending_count(self) -> int:
        return len(self._pending)

    async def publish(self, event: AgentEvent) -> int:
        async with self._condition:
            normalized = replace(event, sequence=self._next_sequence)
            self._next_sequence += 1
            if len(self._pending) >= self._max_pending:
                if self._drop_oldest_mergeable():
                    pass
                elif _is_mergeable(normalized):
                    self._dropped_mergeable += 1
                    logger.debug(
                        "subagent render event dropped",
                        extra=_event_log_extra(normalized, dropped=True),
                    )
                    return normalized.sequence
                else:
                    while len(self._pending) >= self._max_pending and not self._drop_oldest_mergeable():
                        await self._condition.wait()

            self._pending.append(normalized)
            self._condition.notify_all()

        logger.debug(
            "subagent render event",
            extra=_event_log_extra(normalized, dropped=False),
        )
        return normalized.sequence

    async def mark_producer_done(self) -> None:
        self._producer_done = True
        async with self._condition:
            self._condition.notify_all()

    async def __anext__(self) -> AgentEvent:
        async with self._condition:
            while not self._pending:
                if self._producer_done:
                    raise StopAsyncIteration
                await self._condition.wait()
            event = self._pending.popleft()
            self._condition.notify_all()
            return event

    def __aiter__(self) -> AsyncIterator[AgentEvent]:
        return self

    def _drop_oldest_mergeable(self) -> bool:
        for index, event in enumerate(self._pending):
            if _is_mergeable(event):
                del self._pending[index]
                self._dropped_mergeable += 1
                return True
        return False


@contextmanager
def use_render_bus(bus: RenderEventBus):
    token = _current_render_bus.set(bus)
    try:
        yield bus
    finally:
        _current_render_bus.reset(token)


def current_render_bus() -> RenderEventBus | None:
    return _current_render_bus.get()


async def publish_current_render_event(event: AgentEvent) -> bool:
    bus = current_render_bus()
    if bus is None:
        return False
    await bus.publish(event)
    return True


def render_prefix(event: AgentEvent) -> str:
    if event.agent_type == "subagent":
        role = event.role_name or "fork"
        suffix = _task_suffix(event.task_id)
        if suffix is not None:
            return f"[子Agent:{role}#{suffix}]"
        return f"[子Agent:{role}]"
    return "[父Agent]"


def render_event_message(event: AgentEvent) -> str | None:
    if event.type is AgentEventType.TOOL_CALL_STARTED and event.tool_call is not None:
        return f"工具请求：{event.tool_call.name}"
    if event.type is AgentEventType.TOOL_RESULT and event.tool_result is not None:
        if event.tool_result.ok:
            return f"工具完成：{event.tool_result.tool_name}"
        error = event.tool_result.error or "unknown error"
        return f"工具失败：{event.tool_result.tool_name} - {error}"
    if event.type is AgentEventType.SUBAGENT_TASK_QUEUED:
        return "任务排队"
    if event.type is AgentEventType.SUBAGENT_TASK_STARTED:
        return "任务开始"
    if event.type is AgentEventType.SUBAGENT_TASK_DETACHED:
        return "转后台"
    if event.type is AgentEventType.SUBAGENT_TASK_COMPLETED:
        detail = f"，{event.content}" if event.content else ""
        return f"任务完成{detail}"
    if event.type is AgentEventType.SUBAGENT_TASK_FAILED:
        return event.content or "任务失败"
    if event.type is AgentEventType.SUBAGENT_TASK_CANCELLED:
        return event.content or "任务已取消"
    if event.type is AgentEventType.ERROR:
        return f"错误：{event.content}"
    if event.type is AgentEventType.CANCELLED:
        return f"已取消：{event.content}"
    return None


def render_multiline(prefix: str, message: str) -> tuple[str, ...]:
    lines = message.splitlines() or [message]
    return tuple(f"{prefix} {line}" if line else prefix for line in lines)


def _task_suffix(task_id: str | None) -> str | None:
    if not task_id:
        return None
    if "-" not in task_id:
        return task_id
    return task_id.rsplit("-", 1)[-1]


def _is_mergeable(event: AgentEvent) -> bool:
    if event.type is AgentEventType.TOOL_RESULT and event.tool_result is not None:
        return event.tool_result.ok
    return event.type is AgentEventType.SUBAGENT_TASK_COMPLETED


def _event_log_extra(event: AgentEvent, *, dropped: bool) -> dict[str, object]:
    return {
        "agent_type": event.agent_type,
        "role_name": event.role_name or "",
        "task_id": event.task_id or "",
        "sequence": event.sequence,
        "event_type": event.type.value,
        "dropped": dropped,
    }
