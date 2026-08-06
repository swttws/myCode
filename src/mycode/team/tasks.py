from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Callable
from dataclasses import replace
from datetime import datetime, timezone
from threading import Thread
from typing import TypeVar

from mycode.team.config import TeamConfig
from mycode.team.locking import FileLease
from mycode.team.models import (
    ApprovalState,
    TaskKind,
    TaskPatch,
    TaskResult,
    TeamError,
    TeamState,
    TeamTask,
    TeamTaskState,
)
from mycode.team.storage import TeamStore


_T = TypeVar("_T")

_TERMINAL_STATES = {
    TeamTaskState.COMPLETED,
    TeamTaskState.FAILED,
    TeamTaskState.CANCELLED,
}

_STATE_RANK = {
    TeamTaskState.PENDING: 0,
    TeamTaskState.CLAIMED: 1,
    TeamTaskState.AWAITING_APPROVAL: 2,
    TeamTaskState.RUNNING: 3,
    TeamTaskState.BLOCKED: 4,
    TeamTaskState.COMPLETED: 5,
    TeamTaskState.FAILED: 5,
    TeamTaskState.CANCELLED: 5,
}

_ALLOWED_TRANSITIONS = {
    TeamTaskState.PENDING: {TeamTaskState.CANCELLED},
    TeamTaskState.CLAIMED: {
        TeamTaskState.AWAITING_APPROVAL,
        TeamTaskState.RUNNING,
        TeamTaskState.BLOCKED,
        TeamTaskState.FAILED,
        TeamTaskState.CANCELLED,
    },
    TeamTaskState.AWAITING_APPROVAL: {
        TeamTaskState.RUNNING,
        TeamTaskState.FAILED,
        TeamTaskState.CANCELLED,
    },
    TeamTaskState.RUNNING: {
        TeamTaskState.BLOCKED,
        TeamTaskState.COMPLETED,
        TeamTaskState.FAILED,
        TeamTaskState.CANCELLED,
    },
    TeamTaskState.BLOCKED: {
        TeamTaskState.FAILED,
        TeamTaskState.CANCELLED,
    },
    TeamTaskState.COMPLETED: set(),
    TeamTaskState.FAILED: set(),
    TeamTaskState.CANCELLED: set(),
}


class TaskBoard:
    def __init__(
        self,
        store: TeamStore,
        team_name: str,
        *,
        config: TeamConfig | None = None,
        lock_owner: str | None = None,
    ) -> None:
        if not isinstance(store, TeamStore):
            raise ValueError("store must be a TeamStore")
        _require_non_empty_string("team_name", team_name)
        if config is None:
            config = TeamConfig()
        if not isinstance(config, TeamConfig):
            raise ValueError("config must be a TeamConfig")
        if lock_owner is not None:
            _require_non_empty_string("lock_owner", lock_owner)
        self._store = store
        self._team_name = team_name
        self._config = config
        self._lock_owner = lock_owner or f"{team_name}:task-board"

    def create(self, task: TeamTask) -> TeamTask:
        if not isinstance(task, TeamTask):
            raise ValueError("task must be a TeamTask")
        batch_id = task.batch_id

        def action() -> TeamTask:
            snapshot = self._store.load(self._team_name)
            self._ensure_writable(snapshot.team.state, snapshot.team.revision)
            batch_ids = self._batch_ids(snapshot)
            self._ensure_batch_exists(batch_id, batch_ids)
            by_batch = self._load_tasks_by_batch(batch_ids)
            all_tasks = self._index_tasks(by_batch)
            if task.task_id in all_tasks:
                raise self._error(
                    code="duplicate_task",
                    phase="create",
                    message=f"duplicate task id: {task.task_id}",
                    batch_id=batch_id,
                    task_id=task.task_id,
                )
            now = _now()
            dependency_ids = self._normalize_dependency_ids(
                task.dependency_ids,
                batch_id=batch_id,
                task_id=task.task_id,
            )
            created = replace(
                task,
                dependency_ids=dependency_ids,
                revision=1,
                created_at=task.created_at or now,
                updated_at=now,
            )
            by_batch[batch_id] = (*by_batch.get(batch_id, ()), created)
            self._topological_order(by_batch[batch_id], all_tasks=all_tasks)
            self._store.write_task(self._team_name, batch_id, created)
            return created

        return self._with_batch_lock(batch_id, action)

    def update(self, task_id: str, expected_revision: int, patch: TaskPatch) -> TeamTask:
        if not isinstance(patch, TaskPatch):
            raise ValueError("patch must be a TaskPatch")
        batch_id = self._locate_task(task_id).batch_id

        def action() -> TeamTask:
            current, by_batch, all_tasks = self._load_locked_task(task_id, batch_id, phase="update")
            self._check_revision(current, expected_revision, phase="update")
            updated = self._apply_patch(current, patch)
            by_batch[batch_id] = _replace_task(by_batch[batch_id], updated)
            candidate_tasks = {**all_tasks, task_id: updated}
            self._topological_order(by_batch[batch_id], all_tasks=candidate_tasks)
            self._store.write_task(self._team_name, batch_id, updated)
            return updated

        return self._with_batch_lock(batch_id, action)

    def delete(self, task_id: str, expected_revision: int) -> None:
        batch_id = self._locate_task(task_id).batch_id

        def action() -> None:
            current, by_batch, _all_tasks = self._load_locked_task(task_id, batch_id, phase="delete")
            self._check_revision(current, expected_revision, phase="delete")
            if current.state is not TeamTaskState.PENDING:
                raise self._error(
                    code="task_started",
                    phase="delete",
                    message="only not-started pending tasks can be deleted",
                    batch_id=batch_id,
                    task_id=task_id,
                    revision=current.revision,
                )
            successors = [
                task.task_id
                for task in by_batch[batch_id]
                if task.task_id != task_id and task_id in task.dependency_ids
            ]
            if successors:
                raise self._error(
                    code="task_has_successors",
                    phase="delete",
                    message=f"task has successor dependencies: {', '.join(sorted(successors))}",
                    batch_id=batch_id,
                    task_id=task_id,
                    revision=current.revision,
                )
            self._task_path(batch_id, task_id).unlink()

        self._with_batch_lock(batch_id, action)

    def claim(self, task_id: str, member_name: str, expected_revision: int) -> TeamTask:
        _require_non_empty_string("member_name", member_name)
        batch_id = self._locate_task(task_id).batch_id

        def action() -> TeamTask:
            current, by_batch, all_tasks = self._load_locked_task(task_id, batch_id, phase="claim")
            self._topological_order(by_batch[batch_id], all_tasks=all_tasks)
            self._check_revision(current, expected_revision, phase="claim")
            if current.state is not TeamTaskState.PENDING:
                raise self._error(
                    code="task_not_pending",
                    phase="claim",
                    message="only pending tasks can be claimed",
                    batch_id=batch_id,
                    task_id=task_id,
                    revision=current.revision,
                )
            if current.owner is not None:
                raise self._error(
                    code="task_already_owned",
                    phase="claim",
                    message="task already has an owner",
                    batch_id=batch_id,
                    task_id=task_id,
                    revision=current.revision,
                )
            if not self._is_ready(current, by_batch[batch_id]):
                raise self._error(
                    code="task_not_ready",
                    phase="claim",
                    message="task is not ready because dependencies are incomplete",
                    batch_id=batch_id,
                    task_id=task_id,
                    revision=current.revision,
                )
            claimed = replace(
                current,
                owner=member_name,
                state=TeamTaskState.CLAIMED,
                revision=current.revision + 1,
                updated_at=_now(),
            )
            self._store.write_task(self._team_name, batch_id, claimed)
            return claimed

        return self._with_batch_lock(batch_id, action)

    def transition(
        self,
        task_id: str,
        expected_revision: int,
        state: TeamTaskState,
        result: TaskResult | None = None,
        error: str | None = None,
    ) -> TeamTask:
        if not isinstance(state, TeamTaskState):
            raise ValueError("state must be a TeamTaskState")
        if result is not None and not isinstance(result, TaskResult):
            raise ValueError("result must be a TaskResult")
        if error is not None:
            _require_non_empty_string("error", error)
        batch_id = self._locate_task(task_id).batch_id

        def action() -> TeamTask:
            current, by_batch, all_tasks = self._load_locked_task(
                task_id,
                batch_id,
                phase="transition",
            )
            self._topological_order(by_batch[batch_id], all_tasks=all_tasks)
            self._check_revision(current, expected_revision, phase="transition")
            self._validate_transition(current, state, result=result, error=error)
            transitioned = self._transition_task(current, state, result=result, error=error)
            self._store.write_task(self._team_name, batch_id, transitioned)
            return transitioned

        return self._with_batch_lock(batch_id, action)

    def get(self, task_id: str) -> TeamTask:
        return self._locate_task(task_id)

    def list(self, batch_id: str | None = None) -> tuple[TeamTask, ...]:
        snapshot = self._store.load(self._team_name)
        batch_ids = self._batch_ids(snapshot)
        if batch_id is not None:
            _require_non_empty_string("batch_id", batch_id)
            self._ensure_batch_exists(batch_id, batch_ids)
            tasks = self._load_tasks_by_batch((batch_id,))[batch_id]
            return self._topological_order(tasks, all_tasks=self._index_tasks({batch_id: tasks}))

        by_batch = self._load_tasks_by_batch(batch_ids)
        self._index_tasks(by_batch)
        ordered: list[TeamTask] = []
        all_tasks = self._index_tasks(by_batch)
        for current_batch_id in sorted(by_batch):
            ordered.extend(self._topological_order(by_batch[current_batch_id], all_tasks=all_tasks))
        return tuple(ordered)

    def _with_batch_lock(self, batch_id: str, action: Callable[[], _T]) -> _T:
        _require_non_empty_string("batch_id", batch_id)
        lock_path = self._store.batch_root(self._team_name, batch_id) / "batch.lock"
        lease = _run_async(FileLease.acquire(lock_path, config=self._config, owner=self._lock_owner))
        try:
            return action()
        finally:
            _run_async(lease.release())

    def _load_locked_task(
        self,
        task_id: str,
        batch_id: str,
        *,
        phase: str,
    ) -> tuple[TeamTask, dict[str, tuple[TeamTask, ...]], dict[str, TeamTask]]:
        snapshot = self._store.load(self._team_name)
        self._ensure_writable(snapshot.team.state, snapshot.team.revision)
        batch_ids = self._batch_ids(snapshot)
        self._ensure_batch_exists(batch_id, batch_ids)
        by_batch = self._load_tasks_by_batch(batch_ids)
        all_tasks = self._index_tasks(by_batch)
        current = next((task for task in by_batch[batch_id] if task.task_id == task_id), None)
        if current is None:
            raise self._missing_task(task_id, phase=phase)
        return current, by_batch, all_tasks

    def _apply_patch(self, current: TeamTask, patch: TaskPatch) -> TeamTask:
        values: dict[str, object] = {
            "revision": current.revision + 1,
            "updated_at": _now(),
        }
        if patch.title is not None:
            values["title"] = patch.title
        if patch.description is not None:
            values["description"] = patch.description
        if patch.dependency_ids is not None:
            values["dependency_ids"] = self._normalize_dependency_ids(
                patch.dependency_ids,
                batch_id=current.batch_id,
                task_id=current.task_id,
            )
        if patch.kind is not None:
            values["kind"] = patch.kind
        if patch.owner is not None:
            values["owner"] = patch.owner
        if patch.plan_revision is not None:
            values["plan_revision"] = patch.plan_revision
        if patch.approval_state is not None:
            values["approval_state"] = patch.approval_state
        return replace(current, **values)

    def _validate_transition(
        self,
        current: TeamTask,
        state: TeamTaskState,
        *,
        result: TaskResult | None,
        error: str | None,
    ) -> None:
        if current.state in _TERMINAL_STATES:
            if state is current.state:
                raise self._error(
                    code="terminal_repetition",
                    phase="transition",
                    message="terminal task state cannot be repeated",
                    batch_id=current.batch_id,
                    task_id=current.task_id,
                    revision=current.revision,
                )
            raise self._error(
                code="state_rollback",
                phase="transition",
                message="task state rollback is not allowed",
                batch_id=current.batch_id,
                task_id=current.task_id,
                revision=current.revision,
            )
        if state is current.state:
            raise self._error(
                code="invalid_transition",
                phase="transition",
                message="task state transition must change state",
                batch_id=current.batch_id,
                task_id=current.task_id,
                revision=current.revision,
            )
        if _STATE_RANK[state] < _STATE_RANK[current.state]:
            raise self._error(
                code="state_rollback",
                phase="transition",
                message="task state rollback is not allowed",
                batch_id=current.batch_id,
                task_id=current.task_id,
                revision=current.revision,
            )
        if state not in _ALLOWED_TRANSITIONS[current.state]:
            raise self._error(
                code="invalid_transition",
                phase="transition",
                message=f"cannot transition task from {current.state.value} to {state.value}",
                batch_id=current.batch_id,
                task_id=current.task_id,
                revision=current.revision,
            )
        if state is TeamTaskState.RUNNING:
            approval_required = (
                current.state is TeamTaskState.AWAITING_APPROVAL
                or current.approval_state is ApprovalState.REJECTED
            )
            if approval_required and current.approval_state is not ApprovalState.APPROVED:
                raise self._error(
                    code="approval_required",
                    phase="transition",
                    message="approval is required before running this task",
                    batch_id=current.batch_id,
                    task_id=current.task_id,
                    revision=current.revision,
                )
        if state is TeamTaskState.COMPLETED:
            self._validate_completion(current, result=result, error=error)
            return
        if result is not None:
            raise self._error(
                code="invalid_result",
                phase="transition",
                message="result is only allowed when completing a task",
                batch_id=current.batch_id,
                task_id=current.task_id,
                revision=current.revision,
            )
        if state is TeamTaskState.FAILED and error is None:
            raise self._error(
                code="missing_error",
                phase="transition",
                message="failed tasks require an error",
                batch_id=current.batch_id,
                task_id=current.task_id,
                revision=current.revision,
            )
        if state is TeamTaskState.BLOCKED and error is None:
            raise self._error(
                code="missing_error",
                phase="transition",
                message="blocked tasks require an error",
                batch_id=current.batch_id,
                task_id=current.task_id,
                revision=current.revision,
            )

    def _validate_completion(
        self,
        current: TeamTask,
        *,
        result: TaskResult | None,
        error: str | None,
    ) -> None:
        if error is not None:
            raise self._error(
                code="invalid_error",
                phase="transition",
                message="completed tasks cannot include an error",
                batch_id=current.batch_id,
                task_id=current.task_id,
                revision=current.revision,
            )
        if result is None:
            raise self._error(
                code="missing_result",
                phase="transition",
                message="completed tasks require a structured result",
                batch_id=current.batch_id,
                task_id=current.task_id,
                revision=current.revision,
            )
        if current.kind is TaskKind.CODE:
            if result.commit_id is None:
                raise self._error(
                    code="missing_commit",
                    phase="transition",
                    message="completed code tasks require a commit id",
                    batch_id=current.batch_id,
                    task_id=current.task_id,
                    revision=current.revision,
                )
            if result.verification_summary is None:
                raise self._error(
                    code="missing_verification",
                    phase="transition",
                    message="completed code tasks require a verification summary",
                    batch_id=current.batch_id,
                    task_id=current.task_id,
                    revision=current.revision,
                )

    def _transition_task(
        self,
        current: TeamTask,
        state: TeamTaskState,
        *,
        result: TaskResult | None,
        error: str | None,
    ) -> TeamTask:
        values: dict[str, object] = {
            "state": state,
            "result": None,
            "error": error,
            "revision": current.revision + 1,
            "updated_at": _now(),
        }
        if state is TeamTaskState.COMPLETED:
            values["result"] = result
            values["error"] = None
        elif state is TeamTaskState.AWAITING_APPROVAL:
            values["approval_state"] = ApprovalState.PENDING
            values["error"] = None
        elif state is TeamTaskState.RUNNING:
            values["error"] = None
        return replace(current, **values)

    def _is_ready(self, task: TeamTask, batch_tasks: tuple[TeamTask, ...]) -> bool:
        tasks_by_id = {current.task_id: current for current in batch_tasks}
        return all(
            tasks_by_id[dependency_id].state is TeamTaskState.COMPLETED
            for dependency_id in task.dependency_ids
        )

    def _topological_order(
        self,
        tasks: tuple[TeamTask, ...],
        *,
        all_tasks: dict[str, TeamTask],
    ) -> tuple[TeamTask, ...]:
        tasks_by_id = {task.task_id: task for task in tasks}
        ordered_ids = sorted(tasks_by_id)
        successors: dict[str, list[str]] = {task_id: [] for task_id in ordered_ids}
        indegrees = {task_id: 0 for task_id in ordered_ids}
        for task in tasks:
            dependencies = self._normalize_dependency_ids(
                task.dependency_ids,
                batch_id=task.batch_id,
                task_id=task.task_id,
            )
            if dependencies != task.dependency_ids:
                task = replace(task, dependency_ids=dependencies)
                tasks_by_id[task.task_id] = task
            for dependency_id in dependencies:
                if dependency_id == task.task_id:
                    raise self._error(
                        code="self_dependency",
                        phase="validate",
                        message="task cannot depend on itself",
                        batch_id=task.batch_id,
                        task_id=task.task_id,
                    )
                dependency = all_tasks.get(dependency_id)
                if dependency is None:
                    raise self._error(
                        code="missing_dependency",
                        phase="validate",
                        message=f"missing dependency: {dependency_id}",
                        batch_id=task.batch_id,
                        task_id=task.task_id,
                    )
                if dependency.batch_id != task.batch_id:
                    raise self._error(
                        code="cross_batch_dependency",
                        phase="validate",
                        message=f"cross-batch dependency is not allowed: {dependency_id}",
                        batch_id=task.batch_id,
                        task_id=task.task_id,
                    )
                indegrees[task.task_id] += 1
                successors.setdefault(dependency_id, []).append(task.task_id)

        ready = deque(task_id for task_id in ordered_ids if indegrees[task_id] == 0)
        ordered: list[TeamTask] = []
        while ready:
            task_id = ready.popleft()
            ordered.append(tasks_by_id[task_id])
            for successor_id in sorted(successors[task_id]):
                indegrees[successor_id] -= 1
                if indegrees[successor_id] == 0:
                    ready.append(successor_id)

        if len(ordered) != len(tasks_by_id):
            raise self._error(
                code="dependency_cycle",
                phase="validate",
                message="task dependency cycle detected",
                batch_id=tasks[0].batch_id if tasks else None,
            )
        return tuple(ordered)

    def _locate_task(self, task_id: str) -> TeamTask:
        _require_non_empty_string("task_id", task_id)
        snapshot = self._store.load(self._team_name)
        by_batch = self._load_tasks_by_batch(self._batch_ids(snapshot))
        matches = [task for tasks in by_batch.values() for task in tasks if task.task_id == task_id]
        if not matches:
            raise self._missing_task(task_id, phase="load")
        if len(matches) > 1:
            raise self._error(
                code="duplicate_task",
                phase="load",
                message=f"duplicate task id: {task_id}",
                task_id=task_id,
            )
        return matches[0]

    def _load_tasks_by_batch(self, batch_ids: tuple[str, ...]) -> dict[str, tuple[TeamTask, ...]]:
        return {
            batch_id: self._store.list_tasks(self._team_name, batch_id)
            for batch_id in sorted(batch_ids)
        }

    def _index_tasks(self, by_batch: dict[str, tuple[TeamTask, ...]]) -> dict[str, TeamTask]:
        indexed: dict[str, TeamTask] = {}
        for tasks in by_batch.values():
            for task in tasks:
                existing = indexed.get(task.task_id)
                if existing is not None:
                    raise self._error(
                        code="duplicate_task",
                        phase="load",
                        message=f"duplicate task id: {task.task_id}",
                        task_id=task.task_id,
                    )
                indexed[task.task_id] = task
        return indexed

    def _normalize_dependency_ids(
        self,
        value: object,
        *,
        batch_id: str,
        task_id: str,
    ) -> tuple[str, ...]:
        try:
            return _normalize_dependency_ids(value)
        except ValueError as exc:
            raise self._error(
                code="invalid_dependency",
                phase="validate",
                message=str(exc),
                batch_id=batch_id,
                task_id=task_id,
            ) from exc

    def _batch_ids(self, snapshot: object) -> tuple[str, ...]:
        return tuple(batch.batch_id for batch in snapshot.batches)

    def _ensure_batch_exists(self, batch_id: str, batch_ids: tuple[str, ...]) -> None:
        if batch_id not in batch_ids:
            raise self._error(
                code="missing_batch",
                phase="load",
                message=f"missing batch: {batch_id}",
                batch_id=batch_id,
            )

    def _ensure_writable(self, team_state: TeamState, revision: int) -> None:
        if team_state is TeamState.ARCHIVED:
            raise self._error(
                code="team_archived",
                phase="write",
                message="team is archived and read-only",
                revision=revision,
            )

    def _check_revision(self, task: TeamTask, expected_revision: int, *, phase: str) -> None:
        if type(expected_revision) is not int or expected_revision < 0:
            raise ValueError("expected_revision must be a non-negative int")
        if task.revision != expected_revision:
            raise self._error(
                code="revision_conflict",
                phase=phase,
                message=(
                    f"task revision conflict: expected {expected_revision}, "
                    f"found {task.revision}"
                ),
                batch_id=task.batch_id,
                task_id=task.task_id,
                revision=task.revision,
            )

    def _task_path(self, batch_id: str, task_id: str):
        return self._store.batch_root(self._team_name, batch_id) / "tasks" / f"{_safe_segment(task_id)}.json"

    def _missing_task(self, task_id: str, *, phase: str) -> TeamError:
        return self._error(
            code="missing_task",
            phase=phase,
            message=f"missing task: {task_id}",
            task_id=task_id,
        )

    def _error(
        self,
        *,
        code: str,
        phase: str,
        message: str,
        batch_id: str | None = None,
        task_id: str | None = None,
        revision: int = 0,
    ) -> TeamError:
        return TeamError(
            code=code,
            phase=phase,
            message=message,
            team_name=self._team_name,
            batch_id=batch_id,
            task_id=task_id,
            revision=revision,
        )


def _normalize_dependency_ids(value: object) -> tuple[str, ...]:
    if not isinstance(value, tuple):
        value = tuple(value)
    normalized: list[str] = []
    seen: set[str] = set()
    for dependency_id in value:
        if type(dependency_id) is not str or not dependency_id:
            raise ValueError("dependency_ids must contain non-empty strings")
        if dependency_id in seen:
            continue
        seen.add(dependency_id)
        normalized.append(dependency_id)
    return tuple(normalized)


def _replace_task(tasks: tuple[TeamTask, ...], replacement: TeamTask) -> tuple[TeamTask, ...]:
    return tuple(replacement if task.task_id == replacement.task_id else task for task in tasks)


def _run_async(coro):
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)

    result: dict[str, object] = {}

    def run_in_thread() -> None:
        try:
            result["value"] = asyncio.run(coro)
        except BaseException as exc:  # pragma: no cover - defensive event-loop bridge.
            result["error"] = exc

    thread = Thread(target=run_in_thread)
    thread.start()
    thread.join()
    if "error" in result:
        raise result["error"]
    return result.get("value")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _require_non_empty_string(field_name: str, value: object) -> None:
    if type(value) is not str or not value:
        raise ValueError(f"{field_name} must be a non-empty string")


def _safe_segment(value: str) -> str:
    if type(value) is not str or value in {"", ".", ".."}:
        raise ValueError("task path segment must be a safe non-empty name")
    if "/" in value or "\\" in value:
        raise ValueError("task path segment must not contain path separators")
    return value
