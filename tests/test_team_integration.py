from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path

import pytest

from mycode.team import (
    ApprovalState,
    BatchRecord,
    BatchState,
    TaskKind,
    TaskResult,
    TeamError,
    TeamRecord,
    TeamState,
    TeamTask,
    TeamTaskState,
)
from mycode.team.integration import IntegrationService
from mycode.team.storage import TeamStore
from mycode.team.tasks import TaskBoard
from mycode.worktree.models import GitStatus


BASE = "0123456789abcdef0123456789abcdef01234567"
FIRST = "1111111111111111111111111111111111111111"
SECOND = "2222222222222222222222222222222222222222"
MERGED_FIRST = "3333333333333333333333333333333333333333"
MERGED_SECOND = "4444444444444444444444444444444444444444"
NOW = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)


class FakeGitGateway:
    def __init__(self, *, dirty: bool = False, conflict_on: str | None = None) -> None:
        self.dirty = dirty
        self.conflict_on = conflict_on
        self.calls = []
        self.target_ref = BASE
        self.integration_root = None
        self.integration_ref = BASE

    def status(self, target: Path):
        self.calls.append(("status", target))
        return GitStatus(
            has_staged_changes=self.dirty,
            has_unstaged_changes=False,
            untracked_paths=(),
        )

    def capture_head(self, target: Path) -> str:
        self.calls.append(("capture_head", target))
        if self.integration_root is not None and target == self.integration_root:
            return self.integration_ref
        return self.target_ref

    def create_integration_branch(self, repository_root: Path, batch_id: str, base_commit: str):
        self.calls.append(("create_integration_branch", batch_id, base_commit))
        self.integration_root = repository_root / ".worktrees" / "integration" / batch_id
        self.integration_ref = base_commit
        return self.integration_root, f"mycode/team/integration/{batch_id}"

    def merge_commit(self, integration_root: Path, commit_id: str) -> None:
        self.calls.append(("merge_commit", commit_id))
        if commit_id == self.conflict_on:
            raise TeamError(code="merge_conflict", phase="git", message="conflict")
        self.integration_ref = {
            FIRST: MERGED_FIRST,
            SECOND: MERGED_SECOND,
        }[commit_id]

    def update_local_ref(self, repository_root: Path, branch: str, commit_id: str, *, expected_old=None) -> None:
        self.calls.append(("update_local_ref", branch, commit_id, expected_old))
        self.target_ref = commit_id

    def abort_merge(self, integration_root: Path) -> None:
        self.calls.append(("abort_merge", integration_root))

    def remove_integration_worktree(self, repository_root: Path, integration_root: Path, branch: str) -> None:
        self.calls.append(("remove_integration_worktree", branch))


def make_store(tmp_path: Path) -> tuple[TeamStore, TaskBoard, BatchRecord]:
    store = TeamStore(home=tmp_path / "home")
    store.create(
        TeamRecord(
            team_name="team-a",
            repository_root=tmp_path / "repo",
            repository_id="repo-123",
            target_branch="main",
            state=TeamState.ACTIVE,
            created_at=NOW,
            updated_at=NOW,
        )
    )
    batch = BatchRecord(
        batch_id="batch-1",
        goal="ship",
        baseline_commit=BASE,
        state=BatchState.ACTIVE,
        created_at=NOW,
        updated_at=NOW,
    )
    store.write_batch("team-a", batch)
    board = TaskBoard(store, "team-a")
    return store, board, batch


def completed_task(task_id: str, *, owner: str, commit_id: str, dependencies=()) -> TeamTask:
    return TeamTask(
        task_id=task_id,
        batch_id="batch-1",
        title=task_id,
        description=task_id,
        dependency_ids=tuple(dependencies),
        kind=TaskKind.CODE,
        owner=owner,
        state=TeamTaskState.COMPLETED,
        plan_revision=1,
        approval_state=ApprovalState.APPROVED,
        result=TaskResult(
            summary="done",
            commit_id=commit_id,
            verification_summary="pytest",
        ),
        revision=1,
        created_at=NOW,
        updated_at=NOW,
    )


def test_integration_service_rejects_dirty_target_without_mutating_refs(tmp_path: Path):
    store, board, batch = make_store(tmp_path)
    git = FakeGitGateway(dirty=True)
    service = IntegrationService(store=store, team_name="team-a", task_board=board, git=git)

    with pytest.raises(TeamError, match="dirty"):
        asyncio.run(service.integrate(batch.batch_id, lead_workspace_root=tmp_path / "repo"))

    assert not any(call[0] == "update_local_ref" for call in git.calls)


def test_integration_service_merges_completed_code_tasks_in_dependency_order(tmp_path: Path):
    store, board, batch = make_store(tmp_path)
    board.create(completed_task("task-a", owner="alpha", commit_id=FIRST))
    board.create(completed_task("task-b", owner="beta", commit_id=SECOND, dependencies=("task-a",)))
    git = FakeGitGateway()
    service = IntegrationService(store=store, team_name="team-a", task_board=board, git=git)

    report = asyncio.run(service.integrate(batch.batch_id, lead_workspace_root=tmp_path / "repo"))

    assert [call for call in git.calls if call[0] == "merge_commit"] == [
        ("merge_commit", FIRST),
        ("merge_commit", SECOND),
    ]
    assert git.calls[-2] == ("update_local_ref", "main", MERGED_SECOND, BASE)
    assert report.state is BatchState.COMPLETED
    assert report.result_commit_id == MERGED_SECOND
    assert report.target_ref_after == MERGED_SECOND
    assert report.integrated_member_names == ("alpha", "beta")
    assert store.load("team-a").batches[0].state is BatchState.COMPLETED


def test_integration_service_turns_conflict_into_task_and_preserves_target(tmp_path: Path):
    store, board, batch = make_store(tmp_path)
    board.create(completed_task("task-a", owner="alpha", commit_id=FIRST))
    board.create(completed_task("task-b", owner="beta", commit_id=SECOND, dependencies=("task-a",)))
    git = FakeGitGateway(conflict_on=SECOND)
    service = IntegrationService(store=store, team_name="team-a", task_board=board, git=git)

    report = asyncio.run(service.integrate(batch.batch_id, lead_workspace_root=tmp_path / "repo"))

    assert report.state is BatchState.BLOCKED
    assert report.target_ref_before == BASE
    assert report.target_ref_after == BASE
    assert report.conflict_task_id is not None
    assert any(call[0] == "abort_merge" for call in git.calls)
    assert not any(call[0] == "update_local_ref" for call in git.calls)
    conflict_task = board.get(report.conflict_task_id)
    assert conflict_task.kind is TaskKind.CODE
    assert conflict_task.state is TeamTaskState.PENDING
    assert "conflict" in conflict_task.title.lower()
