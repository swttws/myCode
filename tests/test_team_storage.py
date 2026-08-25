from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import pytest

from mycode.team import (
    ApprovalState,
    BatchRecord,
    BatchState,
    MemberBackend,
    MemberRecord,
    MemberState,
    ResolvedBackend,
    TaskKind,
    TeamError,
    TeamRecord,
    TeamSnapshot,
    TeamState,
    TeamTask,
    TeamTaskState,
    WakeEndpoint,
)
from mycode.team.infrastructure.storage import TeamStore


COMMIT = "0123456789abcdef0123456789abcdef01234567"


def utc_now() -> datetime:
    return datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)


def make_team(root: Path) -> TeamRecord:
    return TeamRecord(
        team_name="team-a",
        repository_root=root,
        repository_id="repo-123",
        target_branch="main",
        state=TeamState.ACTIVE,
        revision=0,
        max_members=16,
        max_active_members=4,
        created_at=utc_now(),
        updated_at=utc_now(),
    )


def make_member(store: TeamStore, root: Path) -> MemberRecord:
    wake = WakeEndpoint(
        member_name="dev",
        backend=ResolvedBackend.IN_PROCESS,
        endpoint="in-process:dev",
        revision=1,
    )
    return MemberRecord(
        member_name="dev",
        role_name="general",
        role_revision=1,
        requested_backend=MemberBackend.IN_PROCESS,
        resolved_backend=ResolvedBackend.IN_PROCESS,
        state=MemberState.RUNNING,
        approval_required=False,
        worktree_root=root / "worktrees" / "dev",
        branch_name="mycode/team-a/dev",
        context_path=store.context_path("team-a", "dev"),
        wake_endpoint=wake,
        task_id="task-1",
        batch_id="batch-1",
        revision=1,
        created_at=utc_now(),
        updated_at=utc_now(),
    )


def make_batch() -> BatchRecord:
    return BatchRecord(
        batch_id="batch-1",
        goal="ship it",
        baseline_commit=COMMIT,
        state=BatchState.ACTIVE,
        task_id="task-1",
        revision=1,
        created_at=utc_now(),
        updated_at=utc_now(),
    )


def make_task() -> TeamTask:
    return TeamTask(
        task_id="task-1",
        batch_id="batch-1",
        title="Implement storage",
        description="Persist team state",
        dependency_ids=(),
        kind=TaskKind.CODE,
        owner=None,
        state=TeamTaskState.PENDING,
        plan_revision=0,
        approval_state=ApprovalState.PENDING,
        revision=1,
        created_at=utc_now(),
        updated_at=utc_now(),
    )


def test_team_store_creates_expected_layout_and_round_trips_records(tmp_path: Path):
    home = tmp_path / "home"
    home.mkdir()
    store = TeamStore(home=home)
    team = make_team(tmp_path)

    snapshot = store.create(team)
    root = home / ".mycode" / "teams" / "team-a"

    assert snapshot.team == team
    assert (root / "team.json").exists()
    assert (root / "registry.json").exists()
    assert store.context_path("team-a", "dev") == root / "members" / "dev" / "context.json"
    assert store.lead_lock_path("team-a") == root / "lead.lock"
    assert store.event_log_path("team-a") == root / "events.jsonl"
    assert store.event_cursors_path("team-a") == root / "event-cursors.json"
    assert store.event_failures_path("team-a") == root / "event-failures.jsonl"

    member = make_member(store, tmp_path)
    batch = make_batch()
    task = make_task()
    store.write_member("team-a", member)
    store.write_batch("team-a", batch)
    store.write_task("team-a", "batch-1", task)
    store.write_registry("team-a", {"dev": member.wake_endpoint})

    loaded = store.load("team-a")

    assert loaded.team == team
    assert loaded.members == (member,)
    assert loaded.batches == (batch,)
    assert dict(loaded.registry) == {"dev": member.wake_endpoint}
    assert store.read_task("team-a", "batch-1", "task-1") == task
    assert not list(root.rglob("*.tmp*"))


def test_team_store_rejects_unsafe_names_and_boundary_escapes(tmp_path: Path):
    store = TeamStore(home=tmp_path)

    for name in ("", "../team", "team/name", "team\\name", "."):
        with pytest.raises(ValueError):
            store.team_root(name)


def test_team_store_atomic_save_replaces_snapshot_without_temp_leftovers(tmp_path: Path):
    home = tmp_path / "home"
    home.mkdir()
    store = TeamStore(home=home)
    team = make_team(tmp_path)
    store.create(team)
    updated = replace(team, revision=1, updated_at=datetime(2026, 1, 3, tzinfo=timezone.utc))

    store.save(TeamSnapshot(team=updated, members=(), batches=(), registry={}))

    assert store.load("team-a").team.revision == 1
    assert not list(store.team_root("team-a").rglob("*.tmp*"))


def test_team_store_reports_corrupt_json_without_destroying_files(tmp_path: Path):
    home = tmp_path / "home"
    home.mkdir()
    store = TeamStore(home=home)
    team = make_team(tmp_path)
    store.create(team)
    team_path = store.team_root("team-a") / "team.json"
    team_path.write_text("{not-json", encoding="utf-8")

    with pytest.raises(TeamError, match="JSON"):
        store.load("team-a")

    assert team_path.read_text(encoding="utf-8") == "{not-json"


def test_team_store_archive_makes_team_read_only_but_loadable(tmp_path: Path):
    home = tmp_path / "home"
    home.mkdir()
    store = TeamStore(home=home)
    team = make_team(tmp_path)
    store.create(team)
    member = make_member(store, tmp_path)

    archived = store.archive("team-a")

    assert archived.state is TeamState.ARCHIVED
    assert store.load("team-a").team.state is TeamState.ARCHIVED
    with pytest.raises(TeamError, match="archived"):
        store.write_member("team-a", member)
