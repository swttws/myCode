from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from mycode.team.domain.models import (
    BatchRecord,
    BatchState,
    IntegrationReport,
    TaskKind,
    TeamError,
    TeamTask,
    TeamTaskState,
)
from mycode.team.infrastructure.storage import TeamStore
from mycode.team.application.tasks import TaskBoard


class IntegrationService:
    def __init__(self, *, store: TeamStore, team_name: str, task_board: TaskBoard, git, clock=None) -> None:
        self._store = store
        self._team_name = team_name
        self._task_board = task_board
        self._git = git
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    async def integrate(self, batch_id: str, *, lead_workspace_root: Path) -> IntegrationReport:
        snapshot = self._store.load(self._team_name)
        batch = _find_batch(snapshot.batches, batch_id)
        status = self._git.status(lead_workspace_root)
        if status.has_staged_changes or status.has_unstaged_changes or status.untracked_paths:
            raise TeamError(
                code="dirty_target",
                phase="integrate",
                message="target workspace is dirty",
                team_name=self._team_name,
                batch_id=batch_id,
                path=lead_workspace_root,
            )

        started_at = self._clock()
        target_ref_before = self._git.capture_head(lead_workspace_root)
        integration_root, integration_branch = self._git.create_integration_branch(
            snapshot.team.repository_root,
            batch_id,
            batch.baseline_commit,
        )
        self._save_batch(
            replace(
                batch,
                state=BatchState.INTEGRATING,
                revision=batch.revision + 1,
                updated_at=started_at,
            )
        )

        tasks = tuple(
            task
            for task in self._task_board.list(batch_id)
            if task.kind is TaskKind.CODE
            and task.state is TeamTaskState.COMPLETED
            and task.result is not None
            and task.result.commit_id is not None
        )
        integrated_member_names: list[str] = []
        try:
            for task in tasks:
                assert task.result is not None
                assert task.result.commit_id is not None
                self._git.merge_commit(integration_root, task.result.commit_id)
                if task.owner is not None and task.owner not in integrated_member_names:
                    integrated_member_names.append(task.owner)
            merged_commit = self._git.capture_head(integration_root) if tasks else target_ref_before
        except Exception as exc:
            self._git.abort_merge(integration_root)
            conflict_task = self._create_conflict_task(batch_id, exc)
            blocked = replace(
                batch,
                state=BatchState.BLOCKED,
                revision=batch.revision + 1,
                updated_at=self._clock(),
                integration_diagnostics=(str(exc) or exc.__class__.__name__,),
            )
            self._save_batch(blocked)
            self._remove_integration_worktree(snapshot.team.repository_root, integration_root, integration_branch)
            return IntegrationReport(
                batch_id=batch_id,
                state=BatchState.BLOCKED,
                target_ref_before=target_ref_before,
                target_ref_after=target_ref_before,
                conflict_task_id=conflict_task.task_id,
                diagnostics=blocked.integration_diagnostics,
                started_at=started_at,
                completed_at=self._clock(),
            )

        self._git.update_local_ref(
            snapshot.team.repository_root,
            snapshot.team.target_branch,
            merged_commit,
            expected_old=target_ref_before,
        )
        completed_at = self._clock()
        completed = replace(
            batch,
            state=BatchState.COMPLETED,
            revision=batch.revision + 1,
            updated_at=completed_at,
            completed_at=completed_at,
            result_commit_id=merged_commit,
        )
        self._save_batch(completed)
        self._remove_integration_worktree(snapshot.team.repository_root, integration_root, integration_branch)
        return IntegrationReport(
            batch_id=batch_id,
            state=BatchState.COMPLETED,
            target_ref_before=target_ref_before,
            target_ref_after=merged_commit,
            result_commit_id=merged_commit,
            integrated_member_names=tuple(integrated_member_names),
            started_at=started_at,
            completed_at=completed_at,
        )

    def _create_conflict_task(self, batch_id: str, exc: Exception) -> TeamTask:
        existing_ids = {task.task_id for task in self._task_board.list(batch_id)}
        index = 1
        while True:
            task_id = f"{batch_id}-conflict-{index}"
            if task_id not in existing_ids:
                break
            index += 1
        return self._task_board.create(
            TeamTask(
                task_id=task_id,
                batch_id=batch_id,
                title="Resolve integration conflict",
                description=str(exc) or exc.__class__.__name__,
                dependency_ids=(),
                kind=TaskKind.CODE,
                state=TeamTaskState.PENDING,
            )
        )

    def _save_batch(self, replacement: BatchRecord) -> None:
        snapshot = self._store.load(self._team_name)
        batches = tuple(replacement if batch.batch_id == replacement.batch_id else batch for batch in snapshot.batches)
        self._store.save(replace(snapshot, batches=batches))

    def _remove_integration_worktree(self, repository_root: Path, integration_root: Path, branch: str) -> None:
        try:
            self._git.remove_integration_worktree(repository_root, integration_root, branch)
        except Exception:
            return


def _find_batch(batches: tuple[BatchRecord, ...], batch_id: str) -> BatchRecord:
    for batch in batches:
        if batch.batch_id == batch_id:
            return batch
    raise TeamError(code="missing_batch", phase="integrate", message=f"missing batch: {batch_id}", batch_id=batch_id)


__all__ = ["IntegrationService"]
