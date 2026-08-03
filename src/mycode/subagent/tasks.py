from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass
from typing import Awaitable, Callable

from mycode.agent.events import AgentEvent, AgentEventType
from mycode.subagent.models import (
    SubAgentConfig,
    SubAgentExecutionReport,
    SubAgentLaunchRequest,
    SubAgentNotification,
    SubAgentResult,
    SubAgentTaskSnapshot,
    SubAgentTaskState,
    SubAgentTaskSummary,
    SubAgentUsage,
)
from mycode.subagent.notifications import SubAgentNotificationInbox
from mycode.subagent.rendering import publish_current_render_event


SubAgentRunner = Callable[[asyncio.Event], Awaitable[SubAgentExecutionReport]]


@dataclass
class _TaskRecord:
    id: str
    sequence: int
    request: SubAgentLaunchRequest
    runner: SubAgentRunner | None
    state: SubAgentTaskState
    detached: bool
    rounds: int = 0
    result: SubAgentResult | None = None
    error_code: str | None = None
    error_message: str | None = None
    usage: SubAgentUsage = SubAgentUsage()
    cancel_event: asyncio.Event | None = None
    task: asyncio.Task | None = None


class SubAgentTaskManager:
    def __init__(
        self,
        *,
        config: SubAgentConfig,
        notification_inbox: SubAgentNotificationInbox,
    ) -> None:
        self._config = config
        self._notification_inbox = notification_inbox
        self._lock = asyncio.Lock()
        self._records: dict[str, _TaskRecord] = {}
        self._queue: deque[str] = deque()
        self._next_sequence = 1
        self._closed = False

    async def submit(
        self,
        request: SubAgentLaunchRequest,
        runner: SubAgentRunner,
    ) -> SubAgentTaskSnapshot:
        snapshot = await self.reserve(request)
        return await self.start_reserved(snapshot.id, runner)

    async def reserve(self, request: SubAgentLaunchRequest) -> SubAgentTaskSnapshot:
        async with self._lock:
            if self._closed:
                raise RuntimeError("subagent_task_manager_closed")
            if (
                self._running_count() >= self._config.max_concurrency
                and len(self._queue) >= self._config.max_queued_tasks
            ):
                raise RuntimeError("task_queue_full")

            sequence = self._next_sequence
            self._next_sequence += 1
            task_id = f"task-{sequence:06d}"
            record = _TaskRecord(
                id=task_id,
                sequence=sequence,
                request=request,
                runner=None,
                state=SubAgentTaskState.QUEUED,
                detached=request.requested_background,
            )
            self._records[task_id] = record
            if self._running_count() >= self._config.max_concurrency or self._queue:
                self._queue.append(task_id)
            snapshot = self._snapshot(record)

        await _publish_task_event(snapshot, AgentEventType.SUBAGENT_TASK_QUEUED)
        return snapshot

    async def start_reserved(self, task_id: str, runner: SubAgentRunner) -> SubAgentTaskSnapshot:
        async with self._lock:
            record = self._records.get(task_id)
            if record is None:
                raise KeyError(f"task_not_found: {task_id}")
            record.runner = runner
            in_queue = record.id in self._queue
            can_start_now = self._running_count() < self._config.max_concurrency
            if in_queue and self._queue and self._queue[0] == record.id and can_start_now:
                self._queue.popleft()
                self._start_record(record)
            elif not in_queue and self._queue:
                self._queue.append(task_id)
            elif not in_queue and can_start_now:
                self._start_record(record)
            elif not in_queue:
                self._queue.append(task_id)
            return self._snapshot(record)

    async def fail_reserved(self, task_id: str, error_code: str, error_message: str) -> SubAgentTaskSnapshot:
        async with self._lock:
            record = self._records.get(task_id)
            if record is None:
                raise KeyError(f"task_not_found: {task_id}")
            if record.id in self._queue:
                self._queue.remove(record.id)
            record.state = SubAgentTaskState.FAILED
            record.error_code = error_code
            record.error_message = error_message
            record.rounds = 0
            record.result = None
            if record.detached:
                self._notification_inbox.enqueue(
                    sequence=record.sequence,
                    notification=SubAgentNotification(
                        task_id=record.id,
                        state=record.state,
                        summary=error_message or error_code,
                        summary_truncated=False,
                        usage=record.usage,
                        role_name=record.request.role_name or record.request.kind.value,
                    ),
                )
            self._start_next_queued()
            self._enforce_retention()
            snapshot = self._snapshot(record)

        await _publish_task_event(
            snapshot,
            AgentEventType.SUBAGENT_TASK_FAILED,
            content=error_message or error_code,
        )
        return snapshot

    async def detach(self, task_id: str) -> SubAgentTaskSnapshot:
        async with self._lock:
            record = self._records.get(task_id)
            if record is None:
                raise KeyError(f"task_not_found: {task_id}")
            was_detached = record.detached
            record.detached = True
            snapshot = self._snapshot(record)

        if not was_detached:
            await _publish_task_event(snapshot, AgentEventType.SUBAGENT_TASK_DETACHED)
        return snapshot

    def list(self) -> tuple[SubAgentTaskSummary, ...]:
        return tuple(
            self._summary(record)
            for record in sorted(self._records.values(), key=lambda item: item.sequence)
        )

    def get(self, task_id: str) -> SubAgentTaskSnapshot:
        record = self._records.get(task_id)
        if record is None:
            raise KeyError(f"task_not_found: {task_id}")
        return self._snapshot(record)

    async def cancel_all_and_clear(self) -> None:
        cancelled_snapshots: list[SubAgentTaskSnapshot] = []
        async with self._lock:
            running = [
                record
                for record in self._records.values()
                if record.state is SubAgentTaskState.RUNNING
            ]
            for record in running:
                if record.cancel_event is not None:
                    record.cancel_event.set()
            for task_id in list(self._queue):
                record = self._records.get(task_id)
                if record is not None and record.state is SubAgentTaskState.QUEUED:
                    record.state = SubAgentTaskState.CANCELLED
                    record.error_code = "cancelled"
                    record.error_message = "任务已取消。"
                    cancelled_snapshots.append(self._snapshot(record))
            self._queue.clear()

        tasks = [record.task for record in running if record.task is not None]
        if tasks:
            await asyncio.wait(tasks, timeout=15)
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

        async with self._lock:
            self._records.clear()
            self._queue.clear()
            self._next_sequence = 1
            self._closed = False
            self._notification_inbox.clear()

        for snapshot in cancelled_snapshots:
            await _publish_task_event(
                snapshot,
                AgentEventType.SUBAGENT_TASK_CANCELLED,
                content="任务已取消。",
            )

    def _start_record(self, record: _TaskRecord) -> None:
        record.state = SubAgentTaskState.RUNNING
        record.cancel_event = asyncio.Event()
        record.task = asyncio.create_task(self._run_record(record))

    async def _run_record(self, record: _TaskRecord) -> None:
        try:
            report = await record.runner(record.cancel_event or asyncio.Event())
        except asyncio.CancelledError:
            report = SubAgentExecutionReport(
                state=SubAgentTaskState.CANCELLED,
                rounds=record.rounds,
                result=None,
                error_code="cancelled",
                error_message="任务已取消。",
                usage=record.usage,
            )
        except Exception as exc:
            report = SubAgentExecutionReport(
                state=SubAgentTaskState.FAILED,
                rounds=record.rounds,
                result=None,
                error_code="runner_error",
                error_message=str(exc),
                usage=record.usage,
            )
        async with self._lock:
            self._finalize(record, report)
            self._start_next_queued()

    def _finalize(self, record: _TaskRecord, report: SubAgentExecutionReport) -> None:
        if record.state in (
            SubAgentTaskState.COMPLETED,
            SubAgentTaskState.FAILED,
            SubAgentTaskState.CANCELLED,
        ):
            return
        record.state = report.state
        record.rounds = report.rounds
        record.result = report.result
        record.error_code = report.error_code
        record.error_message = report.error_message
        record.usage = report.usage
        if record.detached:
            self._notification_inbox.enqueue(
                sequence=record.sequence,
                notification=SubAgentNotification(
                    task_id=record.id,
                    state=record.state,
                    summary=_notification_summary(record),
                    summary_truncated=False,
                    usage=record.usage,
                ),
            )
        self._enforce_retention()

    def _start_next_queued(self) -> None:
        while self._queue and self._running_count() < self._config.max_concurrency:
            task_id = self._queue.popleft()
            record = self._records.get(task_id)
            if record is not None and record.state is SubAgentTaskState.QUEUED and record.runner is not None:
                self._start_record(record)
                break
            if record is not None and record.state is SubAgentTaskState.QUEUED:
                self._queue.appendleft(task_id)
                break

    def _enforce_retention(self) -> None:
        terminals = [
            record
            for record in self._records.values()
            if record.state
            in (
                SubAgentTaskState.COMPLETED,
                SubAgentTaskState.FAILED,
                SubAgentTaskState.CANCELLED,
            )
        ]
        overflow = len(terminals) - self._config.max_retained_tasks
        if overflow <= 0:
            return
        for record in sorted(terminals, key=lambda item: item.sequence)[:overflow]:
            self._records.pop(record.id, None)

    def _running_count(self) -> int:
        return sum(1 for record in self._records.values() if record.state is SubAgentTaskState.RUNNING)

    def _summary(self, record: _TaskRecord) -> SubAgentTaskSummary:
        return SubAgentTaskSummary(
            id=record.id,
            sequence=record.sequence,
            kind=record.request.kind,
            role_name=record.request.role_name,
            state=record.state,
            detached=record.detached,
            rounds=record.rounds,
            error_code=record.error_code,
            usage=record.usage,
        )

    def _snapshot(self, record: _TaskRecord) -> SubAgentTaskSnapshot:
        return SubAgentTaskSnapshot(
            id=record.id,
            sequence=record.sequence,
            kind=record.request.kind,
            role_name=record.request.role_name,
            state=record.state,
            detached=record.detached,
            rounds=record.rounds,
            result=record.result,
            error_code=record.error_code,
            error_message=record.error_message,
            usage=record.usage,
        )


def _notification_summary(record: _TaskRecord) -> str:
    if record.result is not None:
        return record.result.summary
    if record.error_message:
        return record.error_message
    return record.error_code or record.state.value


async def _publish_task_event(
    snapshot: SubAgentTaskSnapshot,
    event_type: AgentEventType,
    *,
    content: str = "",
) -> None:
    await publish_current_render_event(
        AgentEvent(
            event_type,
            content=content,
            agent_type="subagent",
            role_name=snapshot.role_name or snapshot.kind.value,
            task_id=snapshot.id,
            sequence=snapshot.sequence,
        )
    )
