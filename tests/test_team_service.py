from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import pytest

from mycode.team import (
    BatchState,
    MemberBackend,
    MemberState,
    MessageProtocol,
    TaskKind,
    TeamError,
    TeamMessage,
    TeamState,
    TeamTask,
    TeamTaskState,
)
from mycode.team.backends import BackendSelector
from mycode.team.config import TeamConfig
from mycode.team.service import TeamService
from mycode.team.storage import TeamStore


COMMIT = "0123456789abcdef0123456789abcdef01234567"
NOW = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)


class FakeGit:
    def __init__(self) -> None:
        self.head = COMMIT

    def identify_repository(self, repository_root: Path):
        return type(
            "Identity",
            (),
            {
                "root": repository_root,
                "common_dir": repository_root / ".git",
                "repository_id": "repo-123",
            },
        )()

    def capture_head(self, repository_root: Path) -> str:
        return self.head


class FakeWorktreeService:
    def __init__(self, repository_root: Path) -> None:
        self.shared_workspace = type(
            "Workspace",
            (),
            {
                "root": repository_root,
                "repository_root": repository_root,
                "repository_id": "repo-123",
            },
        )()
        self.repository_identity = type(
            "Identity",
            (),
            {
                "root": repository_root,
                "common_dir": repository_root / ".git",
                "repository_id": "repo-123",
            },
        )()
        self.git = FakeGit()
        self.prepared = []

    async def prepare_member(
        self,
        *,
        team_name: str,
        member_name: str,
        role_name: str,
        base_commit: str,
    ):
        root = self.shared_workspace.repository_root / ".worktrees" / team_name / member_name
        root.mkdir(parents=True, exist_ok=True)
        branch_name = f"mycode/team/{team_name}/{member_name}"
        self.prepared.append((team_name, member_name, role_name, base_commit))
        return type(
            "Lease",
            (),
            {
                "context": type(
                    "Context",
                    (),
                    {
                        "root": root,
                        "branch_name": branch_name,
                        "repository_root": self.shared_workspace.repository_root,
                        "repository_id": "repo-123",
                    },
                )()
            },
        )()


class FakeBackend:
    def __init__(self) -> None:
        self.started = []
        self.stopped = []
        self.woken = []

    async def start(self, spec):
        self.started.append(spec)
        return type(
            "Handle",
            (),
            {
                "wake_endpoint": spec.wake_endpoint,
                "process_id": 123,
                "token": "backend-token",
            },
        )()

    async def wake(self, handle):
        self.woken.append(handle)

    async def stop(self, handle, *, force: bool):
        self.stopped.append((handle, force))


def make_service(tmp_path: Path) -> TeamService:
    repository_root = tmp_path / "repo"
    repository_root.mkdir(exist_ok=True)
    return TeamService(
        store=TeamStore(home=tmp_path / "home"),
        repository_root=repository_root,
        repository_id="repo-123",
        target_branch="main",
        lead_owner="lead-1",
        config=TeamConfig(
            lock_retry_interval_seconds=0.01,
            lock_timeout_seconds=0.1,
            lock_stale_after_seconds=0.2,
        ),
        worktree_service=FakeWorktreeService(repository_root),
        backend_selector=BackendSelector(capability_probe=lambda backend, env: True),
        backend=FakeBackend(),
        clock=lambda: NOW,
    )


def test_team_service_visible_tools_keep_normal_cli_before_team_activation(tmp_path: Path):
    service = make_service(tmp_path)
    candidates = frozenset({"team", "team_lead", "team_member", "read_file", "edit_file", "run_command"})

    assert service.visible_team_tools(candidates) == frozenset({"team", "read_file", "edit_file", "run_command"})


def test_team_service_create_attach_and_release_lead_lease(tmp_path: Path):
    async def scenario():
        service = make_service(tmp_path)

        snapshot = await service.create_or_attach("team-a", goal="initial goal")

        assert snapshot.team.team_name == "team-a"
        assert snapshot.team.repository_id == "repo-123"
        assert snapshot.team.target_branch == "main"
        assert snapshot.team.state is TeamState.ACTIVE
        assert snapshot.lead_lease is not None
        assert snapshot.lead_lease.owner == "lead-1"

        competing = make_service(tmp_path)
        with pytest.raises(TeamError, match="lock"):
            await competing.create_or_attach("team-a")

        await service.clear_session()
        reattached = await competing.create_or_attach("team-a")

        assert reattached.team.team_name == "team-a"
        assert reattached.lead_lease is not None

    asyncio.run(scenario())


def test_team_service_start_batch_and_spawn_member_persist_registration(tmp_path: Path):
    async def scenario():
        service = make_service(tmp_path)
        await service.create_or_attach("team-a")
        batch = await service.start_batch("ship feature")

        task = TeamTask(
            task_id="task-1",
            batch_id=batch.batch_id,
            title="Build piece",
            description="Implement the first piece",
            dependency_ids=(),
            kind=TaskKind.CODE,
            state=TeamTaskState.PENDING,
        )
        created = service.task_board.create(task)
        member = await service.spawn_member(
            member_name="dev",
            role_name="general",
            role_revision=7,
            requested_backend=MemberBackend.AUTO,
            task_id=created.task_id,
            batch_id=batch.batch_id,
            goal="ship feature",
            read_only=False,
            approval_required=True,
        )

        assert batch.state is BatchState.ACTIVE
        assert member.member_name == "dev"
        assert member.role_revision == 7
        assert member.state is MemberState.RUNNING
        assert member.mailbox_path is not None
        assert member.context_path is not None
        assert member.wake_endpoint is not None
        assert service.store.load("team-a").members == (member,)

        await service.clear_session()
        service_after_restart = make_service(tmp_path)
        await service_after_restart.create_or_attach("team-a")
        receipt = await service_after_restart.send_message(
            TeamMessage(
                message_id="msg-1",
                protocol=MessageProtocol.MESSAGE,
                sender="lead",
                target_name="dev",
                broadcast=False,
                body="continue",
                summary="continue",
                timestamp=NOW,
            )
        )

        assert receipt.recipient_names == ("dev",)

    asyncio.run(scenario())


def test_team_service_archive_rejects_running_work_then_marks_read_only(tmp_path: Path):
    async def scenario():
        service = make_service(tmp_path)
        await service.create_or_attach("team-a")
        await service.start_batch("ship feature")

        with pytest.raises(TeamError, match="running"):
            await service.archive()

        stored = service.store.load("team-a")
        quiet_batch = replace(
            stored.batches[0],
            state=BatchState.COMPLETED,
            completed_at=NOW,
            result_commit_id=COMMIT,
        )
        service.store.save(replace(stored, batches=(quiet_batch,)))

        archived = await service.archive()

        assert archived.state is TeamState.ARCHIVED
        with pytest.raises(TeamError, match="archived"):
            await service.start_batch("new work")

    asyncio.run(scenario())
