from __future__ import annotations

import asyncio
from dataclasses import dataclass
from enum import Enum

from mycode.subagent.models import (
    AgentIsolationMode,
    AgentRoleDefinition,
    SubAgentConfig,
    SubAgentKind,
    SubAgentLaunchRequest,
    SubAgentTaskSnapshot,
    SubAgentTaskState,
    SubAgentTaskSummary,
)
from mycode.subagent.tasks import SubAgentTaskManager
from mycode.worktree.service import WorktreeService


class ForegroundWaitOutcome(str, Enum):
    TERMINAL = "terminal"
    TIMEOUT = "timeout"
    DETACHED = "detached"


@dataclass(frozen=True)
class ForegroundWaitResult:
    outcome: ForegroundWaitOutcome
    snapshot: SubAgentTaskSnapshot


@dataclass(frozen=True)
class SubAgentRunResponse:
    inline: bool
    task: SubAgentTaskSnapshot


class SubAgentService:
    def __init__(
        self,
        *,
        config: SubAgentConfig,
        runtime_factory,
        task_manager: SubAgentTaskManager,
        foreground_waiter=None,
        worktree_service: WorktreeService | None = None,
    ) -> None:
        self._config = config
        self._runtime_factory = runtime_factory
        self._task_manager = task_manager
        self._foreground_waiter = foreground_waiter or _PollingForegroundWaiter()
        self._worktree_service = worktree_service
        self._lock = asyncio.Lock()
        self._active_task_id: str | None = None
        self._active_detach_event: asyncio.Event | None = None
        self._closed = False

    async def run(self, request: SubAgentLaunchRequest) -> SubAgentRunResponse:
        detached = _starts_detached(request)
        async with self._lock:
            if self._closed:
                raise RuntimeError("subagent_service_closed")
            if not detached and self._active_task_id is not None:
                raise RuntimeError("foreground_task_already_active")
            snapshot = await self._task_manager.reserve(request)
            workspace_lease = None
            if self._worktree_service is not None:
                try:
                    role = self._runtime_factory.role_for(request)
                    workspace_lease = await self._prepare_workspace(
                        request=request,
                        role=role,
                        task_id=snapshot.id,
                        task_token=snapshot.task_token or snapshot.id,
                    )
                    snapshot = await self._task_manager.bind_workspace(snapshot.id, workspace_lease)
                except Exception as exc:
                    await self._task_manager.fail_reserved(
                        snapshot.id,
                        "workspace_prepare_error",
                        str(exc) or exc.__class__.__name__,
                    )
                    raise
            try:
                runtime = self._create_runtime(
                    request,
                    detached=detached,
                    task_id=snapshot.id,
                    workspace_lease=workspace_lease,
                )
            except Exception as exc:
                await self._task_manager.fail_reserved(
                    snapshot.id,
                    "runtime_factory_error",
                    str(exc) or exc.__class__.__name__,
                )
                raise
            snapshot = await self._task_manager.start_reserved(snapshot.id, runtime.run)
            if detached:
                if not snapshot.detached:
                    snapshot = await self._task_manager.detach(snapshot.id)
                return SubAgentRunResponse(inline=False, task=snapshot)
            detach_event = asyncio.Event()
            self._active_task_id = snapshot.id
            self._active_detach_event = detach_event

        try:
            wait_result = await self._foreground_waiter.wait(
                manager=self._task_manager,
                task_id=snapshot.id,
                timeout_seconds=self._config.foreground_timeout_seconds,
                detach_event=detach_event,
            )
            if wait_result.outcome is ForegroundWaitOutcome.TERMINAL:
                return SubAgentRunResponse(inline=True, task=wait_result.snapshot)
            detached_snapshot = await self._task_manager.detach(snapshot.id)
            return SubAgentRunResponse(inline=False, task=detached_snapshot)
        finally:
            async with self._lock:
                if self._active_task_id == snapshot.id:
                    self._active_task_id = None
                    self._active_detach_event = None

    def _create_runtime(
        self,
        request: SubAgentLaunchRequest,
        *,
        detached: bool,
        task_id: str,
        workspace_lease=None,
    ):
        return self._runtime_factory.create(
            request,
            detached=detached,
            task_id=task_id,
            workspace_lease=workspace_lease,
        )

    async def _prepare_workspace(
        self,
        *,
        request: SubAgentLaunchRequest,
        role: AgentRoleDefinition | None,
        task_id: str,
        task_token: str,
    ):
        assert self._worktree_service is not None
        if _uses_shared_workspace(request, role):
            return self._worktree_service.shared_lease()
        if role is None:
            raise RuntimeError("worktree_role_required")
        return await self._worktree_service.prepare(
            role_name=role.metadata.name,
            task_id=task_id,
            task_token=task_token,
        )

    async def detach_active(self) -> SubAgentTaskSnapshot | None:
        async with self._lock:
            if self._active_task_id is None:
                return None
            task_id = self._active_task_id
            detach_event = self._active_detach_event
            snapshot = await self._task_manager.detach(task_id)
            self._active_task_id = None
            self._active_detach_event = None
            if detach_event is not None:
                detach_event.set()
            return snapshot

    def list_tasks(self) -> tuple[SubAgentTaskSummary, ...]:
        return self._task_manager.list()

    def get_task(self, task_id: str) -> SubAgentTaskSnapshot:
        return self._task_manager.get(task_id)

    async def clear(self) -> None:
        async with self._lock:
            active_event = self._active_detach_event
            self._active_task_id = None
            self._active_detach_event = None
            if active_event is not None:
                active_event.set()
        await self._task_manager.cancel_all_and_clear()

    async def close(self) -> None:
        async with self._lock:
            if self._closed:
                return
            self._closed = True
            active_event = self._active_detach_event
            self._active_task_id = None
            self._active_detach_event = None
            if active_event is not None:
                active_event.set()
        await self._task_manager.cancel_all_and_clear()


class _PollingForegroundWaiter:
    async def wait(
        self,
        *,
        manager: SubAgentTaskManager,
        task_id: str,
        timeout_seconds: float,
        detach_event: asyncio.Event,
    ) -> ForegroundWaitResult:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout_seconds
        while True:
            snapshot = manager.get(task_id)
            if _is_terminal(snapshot.state):
                return ForegroundWaitResult(ForegroundWaitOutcome.TERMINAL, snapshot)
            if detach_event.is_set():
                return ForegroundWaitResult(ForegroundWaitOutcome.DETACHED, snapshot)

            remaining = deadline - loop.time()
            if remaining <= 0:
                return ForegroundWaitResult(ForegroundWaitOutcome.TIMEOUT, snapshot)
            try:
                await asyncio.wait_for(detach_event.wait(), timeout=min(0.05, remaining))
            except asyncio.TimeoutError:
                continue


def _starts_detached(request: SubAgentLaunchRequest) -> bool:
    return request.requested_background or request.kind is SubAgentKind.FORK


def _uses_shared_workspace(
    request: SubAgentLaunchRequest,
    role: AgentRoleDefinition | None,
) -> bool:
    if request.kind is SubAgentKind.FORK:
        return True
    if role is None:
        return True
    return role.metadata.isolation is AgentIsolationMode.SHARED


def _is_terminal(state: SubAgentTaskState) -> bool:
    return state in {
        SubAgentTaskState.COMPLETED,
        SubAgentTaskState.FAILED,
        SubAgentTaskState.CANCELLED,
    }
