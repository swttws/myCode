from datetime import datetime, timezone
from pathlib import Path
from threading import Thread

import pytest

from mycode.team import (
    ApprovalState,
    BatchRecord,
    BatchState,
    TaskKind,
    TaskPatch,
    TaskResult,
    TeamError,
    TeamRecord,
    TeamState,
    TeamTask,
    TeamTaskState,
)
from mycode.team.config import TeamConfig
from mycode.team.storage import TeamStore
from mycode.team.tasks import TaskBoard


COMMIT = "0123456789abcdef0123456789abcdef01234567"


def utc_now() -> datetime:
    return datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)


def fast_lock_config() -> TeamConfig:
    return TeamConfig(
        lock_retry_interval_seconds=0.01,
        lock_timeout_seconds=0.2,
        lock_stale_after_seconds=1.0,
    )


def make_team(root: Path) -> TeamRecord:
    return TeamRecord(
        team_name="team-a",
        repository_root=root,
        repository_id="repo-123",
        target_branch="main",
        state=TeamState.ACTIVE,
        created_at=utc_now(),
        updated_at=utc_now(),
    )


def make_batch(batch_id: str = "batch-1") -> BatchRecord:
    return BatchRecord(
        batch_id=batch_id,
        goal="ship it",
        baseline_commit=COMMIT,
        state=BatchState.ACTIVE,
        revision=1,
        created_at=utc_now(),
        updated_at=utc_now(),
    )


def make_task(
    task_id: str,
    *,
    batch_id: str = "batch-1",
    dependency_ids: tuple[str, ...] = (),
    kind: TaskKind = TaskKind.CODE,
    owner: str | None = None,
    state: TeamTaskState = TeamTaskState.PENDING,
    approval_state: ApprovalState = ApprovalState.PENDING,
    result: TaskResult | None = None,
    error: str | None = None,
    revision: int = 0,
) -> TeamTask:
    return TeamTask(
        task_id=task_id,
        batch_id=batch_id,
        title=f"Task {task_id}",
        description=f"Do {task_id}",
        dependency_ids=dependency_ids,
        kind=kind,
        owner=owner,
        state=state,
        plan_revision=0,
        approval_state=approval_state,
        result=result,
        error=error,
        revision=revision,
        created_at=utc_now(),
        updated_at=utc_now(),
    )


def make_board(tmp_path: Path) -> tuple[TaskBoard, TeamStore]:
    store = TeamStore(home=tmp_path / "home")
    store.create(make_team(tmp_path))
    store.write_batch("team-a", make_batch())
    return TaskBoard(store=store, team_name="team-a", config=fast_lock_config()), store


def create_tasks(board: TaskBoard, *tasks: TeamTask) -> tuple[TeamTask, ...]:
    return tuple(board.create(task) for task in tasks)


def test_task_board_rejects_missing_dependency_before_write(tmp_path: Path):
    board, store = make_board(tmp_path)

    with pytest.raises(TeamError, match="missing dependency"):
        board.create(make_task("task-b", dependency_ids=("task-a",)))

    assert store.list_tasks("team-a", "batch-1") == ()


def test_task_board_rejects_self_dependency_before_write(tmp_path: Path):
    board, store = make_board(tmp_path)

    with pytest.raises(TeamError, match="self"):
        board.create(make_task("task-a", dependency_ids=("task-a",)))

    assert store.list_tasks("team-a", "batch-1") == ()


def test_task_board_rejects_empty_dependency_ids_before_write(tmp_path: Path):
    board, store = make_board(tmp_path)
    raw = make_task("task-a")
    object.__setattr__(raw, "dependency_ids", [""])

    with pytest.raises(TeamError, match="empty"):
        board.create(raw)

    assert store.list_tasks("team-a", "batch-1") == ()


def test_task_board_rejects_cycle_dependency_before_write(tmp_path: Path):
    board, store = make_board(tmp_path)
    task_a = board.create(make_task("task-a"))
    task_b = board.create(make_task("task-b", dependency_ids=("task-a",)))

    with pytest.raises(TeamError, match="cycle"):
        board.update(task_a.task_id, task_a.revision, TaskPatch(dependency_ids=(task_b.task_id,)))

    assert board.get("task-a").dependency_ids == ()
    assert store.read_task("team-a", "batch-1", "task-a").revision == task_a.revision


def test_task_board_rejects_cross_batch_dependency_before_write(tmp_path: Path):
    board, store = make_board(tmp_path)
    store.write_batch("team-a", make_batch("batch-2"))
    board.create(make_task("task-a", batch_id="batch-2"))

    with pytest.raises(TeamError, match="cross-batch"):
        board.create(make_task("task-b", dependency_ids=("task-a",)))

    assert [task.task_id for task in board.list("batch-1")] == []


def test_task_board_lists_tasks_in_valid_topological_order(tmp_path: Path):
    board, _store = make_board(tmp_path)
    create_tasks(
        board,
        make_task("task-c"),
        make_task("task-a", dependency_ids=("task-c",)),
        make_task("task-b", dependency_ids=("task-a",)),
    )

    assert [task.task_id for task in board.list("batch-1")] == [
        "task-c",
        "task-a",
        "task-b",
    ]


def test_task_board_claim_requires_dependencies_to_be_completed(tmp_path: Path):
    board, _store = make_board(tmp_path)
    task_a, task_b = create_tasks(
        board,
        make_task("task-a"),
        make_task("task-b", dependency_ids=("task-a",)),
    )

    with pytest.raises(TeamError, match="not ready"):
        board.claim(task_b.task_id, "dev", task_b.revision)

    claimed_a = board.claim(task_a.task_id, "dev", task_a.revision)
    running_a = board.transition(claimed_a.task_id, claimed_a.revision, TeamTaskState.RUNNING)
    completed_a = board.transition(
        running_a.task_id,
        running_a.revision,
        TeamTaskState.COMPLETED,
        result=TaskResult(
            summary="done",
            commit_id=COMMIT,
            verification_summary="tests passed",
        ),
    )

    claimed_b = board.claim(task_b.task_id, "ops", task_b.revision)

    assert completed_a.state is TeamTaskState.COMPLETED
    assert claimed_b.owner == "ops"
    assert claimed_b.state is TeamTaskState.CLAIMED


def test_task_board_crud_enforces_revision_cas_and_delete_rules(tmp_path: Path):
    board, _store = make_board(tmp_path)
    task_a, task_b = create_tasks(
        board,
        make_task("task-a"),
        make_task("task-b", dependency_ids=("task-a",)),
    )

    with pytest.raises(TeamError, match="revision"):
        board.update(task_a.task_id, task_a.revision + 1, TaskPatch(title="new title"))

    updated = board.update(task_b.task_id, task_b.revision, TaskPatch(title="new title"))

    assert updated.revision == task_b.revision + 1
    assert updated.title == "new title"

    with pytest.raises(TeamError, match="successor"):
        board.delete(task_a.task_id, task_a.revision)

    board.delete(task_b.task_id, updated.revision)

    assert [task.task_id for task in board.list("batch-1")] == ["task-a"]


def test_task_board_concurrent_claims_leave_single_owner(tmp_path: Path):
    board, _store = make_board(tmp_path)
    task = board.create(make_task("task-a"))
    results: list[TeamTask | TeamError] = []

    def claim(member_name: str) -> None:
        try:
            results.append(board.claim(task.task_id, member_name, task.revision))
        except TeamError as exc:
            results.append(exc)

    threads = [Thread(target=claim, args=(member_name,)) for member_name in ("dev", "ops")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    successes = [result for result in results if isinstance(result, TeamTask)]
    failures = [result for result in results if isinstance(result, TeamError)]

    assert len(successes) == 1
    assert len(failures) == 1
    assert board.get(task.task_id).owner == successes[0].owner


def test_task_board_transition_requires_approval_and_rejects_terminal_repetition(tmp_path: Path):
    board, _store = make_board(tmp_path)
    task = board.create(make_task("task-a"))
    claimed = board.claim(task.task_id, "dev", task.revision)
    awaiting = board.transition(
        claimed.task_id,
        claimed.revision,
        TeamTaskState.AWAITING_APPROVAL,
    )

    with pytest.raises(TeamError, match="approval"):
        board.transition(awaiting.task_id, awaiting.revision, TeamTaskState.RUNNING)

    approved = board.update(
        awaiting.task_id,
        awaiting.revision,
        TaskPatch(approval_state=ApprovalState.APPROVED),
    )
    running = board.transition(approved.task_id, approved.revision, TeamTaskState.RUNNING)
    completed = board.transition(
        running.task_id,
        running.revision,
        TeamTaskState.COMPLETED,
        result=TaskResult(
            summary="done",
            commit_id=COMMIT,
            verification_summary="tests passed",
        ),
    )

    with pytest.raises(TeamError, match="terminal"):
        board.transition(completed.task_id, completed.revision, TeamTaskState.COMPLETED)

    with pytest.raises(TeamError, match="rollback"):
        board.transition(completed.task_id, completed.revision, TeamTaskState.RUNNING)


def test_task_board_requires_structured_result_for_read_only_completion(tmp_path: Path):
    board, _store = make_board(tmp_path)
    task = board.create(make_task("task-a", kind=TaskKind.READ_ONLY))
    claimed = board.claim(task.task_id, "dev", task.revision)
    running = board.transition(claimed.task_id, claimed.revision, TeamTaskState.RUNNING)

    with pytest.raises(TeamError, match="result"):
        board.transition(running.task_id, running.revision, TeamTaskState.COMPLETED)

    completed = board.transition(
        running.task_id,
        running.revision,
        TeamTaskState.COMPLETED,
        result=TaskResult(summary="reviewed", verification_summary="checked manually"),
    )

    assert completed.state is TeamTaskState.COMPLETED
    assert completed.result is not None
    assert completed.result.summary == "reviewed"


def test_task_board_claim_rejects_owned_or_non_pending_tasks(tmp_path: Path):
    board, _store = make_board(tmp_path)
    assigned = board.create(make_task("task-a", owner="lead"))
    pending = board.create(make_task("task-b"))
    claimed = board.claim(pending.task_id, "dev", pending.revision)

    with pytest.raises(TeamError, match="owner"):
        board.claim(assigned.task_id, "dev", assigned.revision)

    with pytest.raises(TeamError, match="pending"):
        board.claim(claimed.task_id, "ops", claimed.revision)


def test_task_board_create_normalizes_dependency_tuple(tmp_path: Path):
    board, _store = make_board(tmp_path)
    dependency = board.create(make_task("task-a"))
    raw = make_task("task-b")
    object.__setattr__(raw, "dependency_ids", [dependency.task_id])

    created = board.create(raw)

    assert created.dependency_ids == (dependency.task_id,)


def test_task_board_get_rejects_unknown_task_id(tmp_path: Path):
    board, _store = make_board(tmp_path)

    with pytest.raises(TeamError, match="task"):
        board.get("missing")


def test_task_board_rejects_duplicate_task_ids_across_batches(tmp_path: Path):
    board, store = make_board(tmp_path)
    store.write_batch("team-a", make_batch("batch-2"))
    board.create(make_task("task-a"))

    with pytest.raises(TeamError, match="duplicate"):
        board.create(make_task("task-a", batch_id="batch-2"))
