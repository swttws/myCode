from __future__ import annotations

import asyncio
from pathlib import Path

from mycode.team import (
    MemberBackend,
    MemberRecord,
    MemberState,
    MessageProtocol,
    ResolvedBackend,
    TaskKind,
    TeamMessage,
    TeamRecord,
    TeamState,
    TeamTask,
    WakeEndpoint,
)
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


def test_team_worker_default_runtime_builds_real_agent_loop(tmp_path: Path, monkeypatch):
    from types import SimpleNamespace

    from mycode.team import worker
    from mycode.team.worker import TeamWorkerRequest

    home = tmp_path / "home"
    repository_root = tmp_path / "repo"
    worktree_root = repository_root / ".worktrees" / "team-a" / "dev"
    repository_root.mkdir()
    worktree_root.mkdir(parents=True)
    store = TeamStore(home=home)
    store.create(
        TeamRecord(
            team_name="team-a",
            repository_root=repository_root,
            repository_id="repo-123",
            target_branch="main",
            state=TeamState.ACTIVE,
        )
    )
    store.write_member(
        "team-a",
        MemberRecord(
            member_name="dev",
            role_name="general",
            role_revision=1,
            requested_backend=MemberBackend.IN_PROCESS,
            resolved_backend=ResolvedBackend.IN_PROCESS,
            state=MemberState.RUNNING,
            worktree_root=worktree_root,
            branch_name="mycode/team/team-a/dev",
            mailbox_path=store.mailbox_path("team-a", "dev"),
            context_path=store.context_path("team-a", "dev"),
            wake_endpoint=WakeEndpoint(
                member_name="dev",
                backend=ResolvedBackend.IN_PROCESS,
                endpoint="in-process:dev",
                revision=1,
            ),
        )
    )
    captured = {}

    class FakeAgentLoop:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        async def run(self, user_text, *, mode, approval_provider=None):
            if False:
                yield None

    monkeypatch.setattr(worker, "load_config", lambda *args, **kwargs: SimpleNamespace(model="test-model"), raising=False)
    monkeypatch.setattr(worker, "create_llm", lambda config: object(), raising=False)
    monkeypatch.setattr(worker, "AgentLoop", FakeAgentLoop, raising=False)

    runtime = worker.create_worker_runtime(
        TeamWorkerRequest(team_name="team-a", member_name="dev", home=home)
    )

    assert isinstance(runtime._agent, FakeAgentLoop)
    assert captured["workspace"].root == worktree_root
    assert "team_member" in {
        definition.name
        for definition in captured["tool_registry"].definitions()
    }
