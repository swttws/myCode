from __future__ import annotations

import asyncio
from pathlib import Path

from mycode.team import MemberBackend, MessageProtocol, TaskKind, TeamMessage, TeamTask
from mycode.team.backends import BackendSelector
from mycode.team.config import TeamConfig
from mycode.team.service import TeamService
from mycode.team.storage import TeamStore
from tests.test_team_service import FakeBackend, FakeWorktreeService


def test_stage_14_success_path_uses_persistent_team_state_without_push(tmp_path: Path):
    async def scenario():
        repository_root = tmp_path / "repo"
        repository_root.mkdir()
        store = TeamStore(home=tmp_path / "home")
        service = TeamService(
            store=store,
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
        )
        await service.create_or_attach("team-a")
        batch = await service.start_batch("ship")
        first = service.task_board.create(
            TeamTask(
                task_id="task-a",
                batch_id=batch.batch_id,
                title="A",
                description="A",
                dependency_ids=(),
                kind=TaskKind.CODE,
            )
        )
        second = service.task_board.create(
            TeamTask(
                task_id="task-b",
                batch_id=batch.batch_id,
                title="B",
                description="B",
                dependency_ids=("task-a",),
                kind=TaskKind.CODE,
            )
        )
        await service.spawn_member(
            member_name="alpha",
            role_name="general",
            role_revision=1,
            requested_backend=MemberBackend.IN_PROCESS,
            task_id=first.task_id,
            batch_id=batch.batch_id,
            goal="ship",
            read_only=False,
            approval_required=False,
        )
        await service.spawn_member(
            member_name="beta",
            role_name="general",
            role_revision=1,
            requested_backend=MemberBackend.IN_PROCESS,
            task_id=second.task_id,
            batch_id=batch.batch_id,
            goal="ship",
            read_only=False,
            approval_required=False,
        )
        receipt = await service.send_message(
            TeamMessage(
                message_id="msg-1",
                protocol=MessageProtocol.MESSAGE,
                sender="lead",
                target_name="alpha",
                broadcast=False,
                body="resume",
                summary="resume",
                timestamp=first.created_at,
            )
        )

        assert receipt.recipient_names == ("alpha",)
        assert store.load("team-a").members[0].member_name == "alpha"

    asyncio.run(scenario())


def test_team_worker_main_runs_named_member_runtime_factory(tmp_path: Path):
    from mycode.team.worker import main

    calls = []

    class Runtime:
        async def resume_from_checkpoint(self):
            calls.append("resume")

        async def run_until_idle(self):
            calls.append("run")

    def runtime_factory(request):
        calls.append((request.team_name, request.member_name, request.home))
        return Runtime()

    exit_code = main(
        ["team-a/dev", "--home", str(tmp_path / "home")],
        runtime_factory=runtime_factory,
    )

    assert exit_code == 0
    assert calls == [("team-a", "dev", tmp_path / "home"), "resume", "run"]
