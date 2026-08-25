from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from enum import Enum
from typing import AsyncIterator

from mycode.agent import AgentEvent, AgentEventType, AgentMode
from mycode.team.domain.models import BatchState, TeamError, TeamTaskState
from mycode.team.execution.consumer import RoleEventConsumer
from mycode.team.infrastructure.requests import TeamRequestState
from mycode.team.domain.state import SupervisorState
from mycode.log_context import use_log_identity


logger = logging.getLogger("mycode.team.supervisor")


class TeamEventKind(str, Enum):
    """Supervisor 消费的输入事件类别。"""

    USER_GOAL = "user_goal"  # 用户提交的目标文本
    MEMBER_MESSAGE = "member_message"  # member 发来的消息事件
    USER_DECISION = "user_decision"  # 用户对审批请求的决策
    STOP = "stop"  # 停止 supervisor 信号


@dataclass(frozen=True)
class TeamEvent:
    """传给 Lead 的安全事件摘要，不携带完整 prompt 或敏感凭据。"""

    event_id: str
    event_kind: TeamEventKind
    message_id: str | None = None
    request_id: str | None = None
    team_name: str | None = None
    batch_id: str | None = None
    task_id: str | None = None
    member_name: str | None = None
    summary: str = ""
    body: str = ""


class LeadSupervisor:
    """Run one Lead AgentLoop continuously while a team batch is unfinished."""

    def __init__(
        self,
        service,
        lead_agent,
    ) -> None:
        self._service = service
        self._lead_agent = lead_agent
        self._queue: asyncio.Queue[tuple[TeamEventKind, object]] = asyncio.Queue()
        self._event_queue: asyncio.Queue[AgentEvent | None] = asyncio.Queue()
        self._lead_lock = asyncio.Lock()
        self._stop_event = asyncio.Event()
        self._worker_task: asyncio.Task | None = None
        self._watcher_task: asyncio.Task | None = None
        self._event_consumer: RoleEventConsumer | None = None
        self._state = SupervisorState.IDLE

    @property
    def state(self) -> SupervisorState:
        return self._state

    async def start(self) -> None:
        if self._worker_task is not None and not self._worker_task.done():
            return
        self._stop_event.clear()
        self._state = SupervisorState.IDLE
        self._worker_task = asyncio.create_task(self._run_queue(), name="team-lead-supervisor")
        try:
            event_store = self._service.event_store
        except TeamError:
            return
        notifier = self._service.event_notifier
        self._event_consumer = RoleEventConsumer(
            "lead",
            events=event_store,
            notifier=notifier,
            handler=self._handle_persistent_event,
        )
        self._watcher_task = asyncio.create_task(self._event_consumer.run(), name="team-lead-event-consumer")

    async def submit_user_goal(self, text: str) -> None:
        if type(text) is not str or not text.strip():
            raise ValueError("text must be a non-empty string")
        await self._queue.put((TeamEventKind.USER_GOAL, text))

    async def resolve_user_request(self, request_id: str, resolution: str) -> None:
        if type(request_id) is not str or not request_id:
            raise ValueError("request_id must be a non-empty string")
        if type(resolution) is not str or not resolution:
            raise ValueError("resolution must be a non-empty string")
        await self._service.resolve_request(
            request_id,
            resolution=resolution,
            resolved_by="user",
            state=TeamRequestState.RESOLVED,
        )
        await self._queue.put(
            (
                TeamEventKind.USER_DECISION,
                TeamEvent(
                    event_id=f"user-decision-{request_id}",
                    event_kind=TeamEventKind.USER_DECISION,
                    request_id=request_id,
                    summary=resolution,
                ),
            )
        )

    async def events(self) -> AsyncIterator[AgentEvent]:
        while True:
            event = await self._event_queue.get()
            if event is None:
                return
            yield event

    async def stop(self) -> None:
        self._state = SupervisorState.STOPPING
        self._stop_event.set()
        await self._queue.put((TeamEventKind.STOP, None))
        if self._event_consumer is not None:
            await self._event_consumer.stop()
        for task in (self._watcher_task, self._worker_task):
            if task is not None:
                await task
        self._watcher_task = None
        self._worker_task = None
        self._event_consumer = None
        await self._event_queue.put(None)

    async def _run_queue(self) -> None:
        while True:
            kind, payload = await self._queue.get()
            if kind is TeamEventKind.STOP:
                self._state = SupervisorState.STOPPING
                return
            try:
                if kind is TeamEventKind.USER_GOAL:
                    await self._run_lead(str(payload), event_id=None)
                elif kind is TeamEventKind.USER_DECISION:
                    event = payload
                    await self._run_lead(
                        f"用户已解决请求 {event.request_id}：{event.summary}",
                        event_id=event.event_id,
                        task_id=event.task_id,
                        batch_id=event.batch_id,
                    )
            except Exception:
                logger.exception("team.supervisor.event.failed")
                self._state = SupervisorState.FAILED

    async def _handle_persistent_event(self, event) -> None:
        message = event.message
        await self._run_lead(
            f"收到成员 {message.sender} 的团队事件：{message.summary}\n{message.body}",
            event_id=event.event_id,
            task_id=message.task_id,
            batch_id=message.batch_id,
        )

    async def _run_lead(
        self,
        prompt: str,
        *,
        event_id: str | None,
        task_id: str | None = None,
        batch_id: str | None = None,
    ) -> None:
        async with self._lead_lock:
            self._state = SupervisorState.RUNNING_LEAD
            started = asyncio.get_running_loop().time()
            context = _lead_log_context(
                self._service,
                event_id=event_id,
                task_id=task_id,
                batch_id=batch_id,
            )
            logger.info(
                "team.lead.started",
                extra=context,
            )
            result_summary = ""
            try:
                with use_log_identity(
                    agent_role="lead",
                    team_name=context.get("team_name"),
                    task_id=task_id,
                    batch_id=batch_id,
                    event_id=event_id,
                ):
                    async for event in self._lead_agent.run(prompt, mode=AgentMode()):
                        await self._event_queue.put(event)
                        if event.type is AgentEventType.FINAL_RESPONSE:
                            result_summary = _log_summary(event.content)
                        if event.type is AgentEventType.ERROR:
                            raise RuntimeError(event.content or "lead agent failed")
            except Exception:
                self._state = SupervisorState.FAILED
                logger.exception(
                    "team.lead.failed",
                    extra={**context, "result_summary": "执行失败", "duration_ms": _elapsed_ms(started)},
                )
                raise
            logger.info(
                "team.lead.result",
                extra={**context, "result_summary": result_summary or "未返回文本结果", "duration_ms": _elapsed_ms(started)},
            )
            await self._update_wait_state()

    async def _update_wait_state(self) -> None:
        try:
            pending = self._service.list_requests(state=TeamRequestState.PENDING)
            if pending:
                self._state = SupervisorState.WAITING_USER
                return
            snapshot = await self._service.status()
            tasks = self._service.list_tasks()
            if _snapshot_complete(snapshot, tasks):
                self._state = SupervisorState.COMPLETED
            else:
                self._state = SupervisorState.WAITING_MEMBER
        except Exception:
            self._state = SupervisorState.WAITING_MEMBER


def _snapshot_complete(snapshot, tasks=()) -> bool:
    batches = snapshot.batches
    for batch in batches:
        if batch.state in {BatchState.ACTIVE, BatchState.BLOCKED, BatchState.INTEGRATING}:
            return False
    return all(task.state in {TeamTaskState.COMPLETED, TeamTaskState.CANCELLED} for task in tasks) if tasks else False


def _lead_log_context(service, *, event_id: str | None, task_id: str | None, batch_id: str | None) -> dict[str, object]:
    team_name = service.team_name
    return {
        key: value
        for key, value in {
            "agent_role": "lead",
            "team_name": team_name,
            "event_id": event_id,
            "task_id": task_id,
            "batch_id": batch_id,
        }.items()
        if value is not None and value != ""
    }


def _log_summary(value: object, *, limit: int = 240) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return f"{text[:limit - 3]}..."


def _elapsed_ms(started: float) -> int:
    return int((asyncio.get_running_loop().time() - started) * 1000)


__all__ = ["LeadSupervisor", "SupervisorState", "TeamEvent", "TeamEventKind"]
