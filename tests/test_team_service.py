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
    ResolvedBackend,
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
        self.prepared_leases = []
        self.released = []

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
        lease = type(
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
        self.prepared_leases.append(lease)
        return lease

    async def release(self, lease):
        self.released.append(lease)
        return None


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


def make_service_with_backend(
    tmp_path: Path,
    *,
    backend_selector,
    backend,
    config: TeamConfig | None = None,
) -> TeamService:
    repository_root = tmp_path / "repo"
    repository_root.mkdir(exist_ok=True)
    worktree_service = FakeWorktreeService(repository_root)
    return TeamService(
        store=TeamStore(home=tmp_path / "home"),
        repository_root=repository_root,
        repository_id="repo-123",
        target_branch="main",
        lead_owner="lead-1",
        config=config
        or TeamConfig(
            lock_retry_interval_seconds=0.01,
            lock_timeout_seconds=0.1,
            lock_stale_after_seconds=0.2,
        ),
        worktree_service=worktree_service,
        backend_selector=backend_selector,
        backend=backend,
        clock=lambda: NOW,
    )


async def spawn_service_member(service: TeamService, *, member_name: str = "dev"):
    await service.create_or_attach("team-a")
    batch = await service.start_batch("ship feature")
    task = service.task_board.create(
        TeamTask(
            task_id=f"task-{member_name}",
            batch_id=batch.batch_id,
            title="Build piece",
            description="Implement the first piece",
            dependency_ids=(),
            kind=TaskKind.CODE,
            state=TeamTaskState.PENDING,
        )
    )
    return await service.spawn_member(
        member_name=member_name,
        role_name="general",
        role_revision=7,
        requested_backend=MemberBackend.IN_PROCESS,
        task_id=task.task_id,
        batch_id=batch.batch_id,
        goal="ship feature",
        read_only=False,
        approval_required=False,
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


def test_team_service_clear_session_gracefully_stops_only_in_process_members(tmp_path: Path):
    async def scenario():
        service = make_service(tmp_path)
        backend = service._backend
        await service.create_or_attach("team-a")
        batch = await service.start_batch("ship feature")
        task = service.task_board.create(
            TeamTask(
                task_id="task-1",
                batch_id=batch.batch_id,
                title="Build piece",
                description="Implement the first piece",
                dependency_ids=(),
                kind=TaskKind.CODE,
                state=TeamTaskState.PENDING,
            )
        )
        await service.spawn_member(
            member_name="dev",
            role_name="general",
            role_revision=7,
            requested_backend=MemberBackend.IN_PROCESS,
            task_id=task.task_id,
            batch_id=batch.batch_id,
            goal="ship feature",
            read_only=False,
            approval_required=False,
        )

        await service.clear_session()

        assert backend.stopped[0][1] is False

        service = make_service(tmp_path)
        backend = service._backend
        await service.create_or_attach("team-a")
        batch = await service.start_batch("external feature")
        task = service.task_board.create(
            TeamTask(
                task_id="task-2",
                batch_id=batch.batch_id,
                title="Build other piece",
                description="Implement the second piece",
                dependency_ids=(),
                kind=TaskKind.CODE,
                state=TeamTaskState.PENDING,
            )
        )
        await service.spawn_member(
            member_name="ops",
            role_name="general",
            role_revision=7,
            requested_backend=MemberBackend.TMUX,
            task_id=task.task_id,
            batch_id=batch.batch_id,
            goal="external feature",
            read_only=False,
            approval_required=False,
        )

        await service.clear_session()

        assert backend.stopped == []

    asyncio.run(scenario())


def test_team_service_spawn_member_releases_worktree_when_backend_is_unavailable(tmp_path: Path):
    async def scenario():
        service = make_service_with_backend(
            tmp_path,
            backend_selector=BackendSelector(capability_probe=lambda backend, env: False),
            backend=FakeBackend(),
        )
        await service.create_or_attach("team-a")
        batch = await service.start_batch("ship feature")
        task = service.task_board.create(
            TeamTask(
                task_id="task-1",
                batch_id=batch.batch_id,
                title="Build piece",
                description="Implement the first piece",
                dependency_ids=(),
                kind=TaskKind.CODE,
                state=TeamTaskState.PENDING,
            )
        )

        with pytest.raises(TeamError, match="backend"):
            await service.spawn_member(
                member_name="dev",
                role_name="general",
                role_revision=7,
                requested_backend=MemberBackend.IN_PROCESS,
                task_id=task.task_id,
                batch_id=batch.batch_id,
                goal="ship feature",
                read_only=False,
                approval_required=False,
            )

        assert service._worktree_service.released == service._worktree_service.prepared_leases

    asyncio.run(scenario())


def test_team_service_spawn_member_releases_worktree_when_backend_start_fails(tmp_path: Path):
    class FailingBackend(FakeBackend):
        async def start(self, spec):
            raise RuntimeError("backend start failed")

    async def scenario():
        service = make_service_with_backend(
            tmp_path,
            backend_selector=BackendSelector(capability_probe=lambda backend, env: True),
            backend=FailingBackend(),
        )
        await service.create_or_attach("team-a")
        batch = await service.start_batch("ship feature")
        task = service.task_board.create(
            TeamTask(
                task_id="task-1",
                batch_id=batch.batch_id,
                title="Build piece",
                description="Implement the first piece",
                dependency_ids=(),
                kind=TaskKind.CODE,
                state=TeamTaskState.PENDING,
            )
        )

        with pytest.raises(RuntimeError, match="backend start failed"):
            await service.spawn_member(
                member_name="dev",
                role_name="general",
                role_revision=7,
                requested_backend=MemberBackend.IN_PROCESS,
                task_id=task.task_id,
                batch_id=batch.batch_id,
                goal="ship feature",
                read_only=False,
                approval_required=False,
            )

        assert service._worktree_service.released == service._worktree_service.prepared_leases

    asyncio.run(scenario())


def test_team_service_persists_and_registers_member_before_backend_start(tmp_path: Path):
    class ObservingBackend(FakeBackend):
        async def start(self, spec):
            stored = service.store.load(spec.team_name)
            member = next(item for item in stored.members if item.member_name == spec.member_name)
            assert member.state is MemberState.PROVISIONING
            service._mailbox_or_error().receive(spec.member_name)
            return await super().start(spec)

    async def scenario():
        nonlocal service
        service = make_service_with_backend(
            tmp_path,
            backend_selector=BackendSelector(capability_probe=lambda backend, env: True),
            backend=ObservingBackend(),
        )
        await service.create_or_attach("team-a")
        batch = await service.start_batch("ship feature")
        task = service.task_board.create(
            TeamTask(
                task_id="task-1",
                batch_id=batch.batch_id,
                title="Build piece",
                description="Implement the first piece",
                dependency_ids=(),
                kind=TaskKind.CODE,
                state=TeamTaskState.PENDING,
            )
        )

        member = await service.spawn_member(
            member_name="dev",
            role_name="general",
            role_revision=7,
            requested_backend=MemberBackend.IN_PROCESS,
            task_id=task.task_id,
            batch_id=batch.batch_id,
            goal="ship feature",
            read_only=False,
            approval_required=False,
        )

        assert member.state is MemberState.RUNNING

    service = None
    asyncio.run(scenario())


def test_team_service_passes_configured_backend_priority_to_selector(tmp_path: Path):
    class RecordingSelector:
        def __init__(self) -> None:
            self.priority = None

        def select(self, requested_backend, environment, *, priority):
            self.priority = priority
            return type(
                "Selection",
                (),
                {
                    "available": True,
                    "resolved_backend": ResolvedBackend.IN_PROCESS,
                    "reason_code": "backend_available",
                    "reason": "selected in_process",
                },
            )()

    async def scenario():
        selector = RecordingSelector()
        service = make_service_with_backend(
            tmp_path,
            backend_selector=selector,
            backend=FakeBackend(),
            config=TeamConfig(
                backend_priority=(MemberBackend.IN_PROCESS, MemberBackend.TMUX),
                lock_retry_interval_seconds=0.01,
                lock_timeout_seconds=0.1,
                lock_stale_after_seconds=0.2,
            ),
        )

        await spawn_service_member(service)

        assert selector.priority == (MemberBackend.IN_PROCESS, MemberBackend.TMUX)

    asyncio.run(scenario())


def test_team_service_terminate_waits_for_shutdown_response_before_stopping(tmp_path: Path):
    class ObservingBackend(FakeBackend):
        def __init__(self, observed):
            super().__init__()
            self.observed = observed

        async def stop(self, handle, *, force: bool):
            self.observed.append(
                any(
                    message.protocol is MessageProtocol.SHUTDOWN_RESPONSE
                    and message.sender == "dev"
                    for message in service._mailbox_or_error().receive("lead")
                )
            )
            await super().stop(handle, force=force)

    async def scenario():
        nonlocal service
        observed = []
        backend = ObservingBackend(observed)
        service = make_service_with_backend(
            tmp_path,
            backend_selector=BackendSelector(capability_probe=lambda backend, env: True),
            backend=backend,
            config=TeamConfig(
                lock_retry_interval_seconds=0.005,
                lock_timeout_seconds=0.05,
                lock_stale_after_seconds=0.1,
                graceful_shutdown_timeout_seconds=0.2,
            ),
        )
        await spawn_service_member(service)

        async def respond():
            await asyncio.sleep(0.03)
            await service.send_message(
                TeamMessage(
                    message_id="shutdown-response-dev",
                    protocol=MessageProtocol.SHUTDOWN_RESPONSE,
                    sender="dev",
                    target_name="lead",
                    broadcast=False,
                    body="checkpoint saved",
                    summary="checkpoint saved",
                    timestamp=NOW,
                )
            )

        response_task = asyncio.create_task(respond())
        member = await service.terminate_member("dev", force=False)
        await response_task

        assert member.state is MemberState.STOPPED
        assert backend.stopped[0][1] is False
        assert observed == [True]

    service = None
    asyncio.run(scenario())


def test_team_service_terminate_forces_backend_after_shutdown_timeout(tmp_path: Path):
    async def scenario():
        backend = FakeBackend()
        service = make_service_with_backend(
            tmp_path,
            backend_selector=BackendSelector(capability_probe=lambda backend, env: True),
            backend=backend,
            config=TeamConfig(
                lock_retry_interval_seconds=0.005,
                lock_timeout_seconds=0.05,
                lock_stale_after_seconds=0.1,
                graceful_shutdown_timeout_seconds=0.01,
            ),
        )
        await spawn_service_member(service)

        member = await service.terminate_member("dev", force=False)

        assert member.state is MemberState.STOPPED
        assert backend.stopped[0][1] is True

    asyncio.run(scenario())


def test_team_service_terminate_ignores_stale_shutdown_response(tmp_path: Path):
    async def scenario():
        backend = FakeBackend()
        service = make_service_with_backend(
            tmp_path,
            backend_selector=BackendSelector(capability_probe=lambda backend, env: True),
            backend=backend,
            config=TeamConfig(
                lock_retry_interval_seconds=0.005,
                lock_timeout_seconds=0.05,
                lock_stale_after_seconds=0.1,
                graceful_shutdown_timeout_seconds=0.01,
            ),
        )
        await spawn_service_member(service)
        await service.send_message(
            TeamMessage(
                message_id="old-shutdown-response-dev",
                protocol=MessageProtocol.SHUTDOWN_RESPONSE,
                sender="dev",
                target_name="lead",
                broadcast=False,
                body="old checkpoint",
                summary="old checkpoint",
                timestamp=NOW,
            )
        )

        await service.terminate_member("dev", force=False)

        assert backend.stopped[0][1] is True

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


def test_team_service_reattach_wakes_persisted_member_without_prior_handle(tmp_path: Path):
    async def scenario():
        backend = FakeBackend()
        first = make_service_with_backend(
            tmp_path,
            backend_selector=BackendSelector(capability_probe=lambda backend, env: True),
            backend=backend,
        )
        await spawn_service_member(first)

        second = make_service_with_backend(
            tmp_path,
            backend_selector=BackendSelector(capability_probe=lambda backend, env: True),
            backend=backend,
        )
        await first.clear_session()
        await second.create_or_attach("team-a")
        assert second._backend_handles == {}

        await second.send_message(
            TeamMessage(
                message_id="resume-dev",
                protocol=MessageProtocol.MESSAGE,
                sender="lead",
                target_name="dev",
                broadcast=False,
                body="resume work",
                summary="resume",
                timestamp=NOW,
            )
        )

        assert len(backend.started) == 2
        assert "dev" in second._backend_handles

    asyncio.run(scenario())


def test_team_service_marks_member_blocked_when_persisted_wake_fails(tmp_path: Path):
    class FailingWakeBackend(FakeBackend):
        async def start(self, spec):
            if self.started:
                raise RuntimeError("worker launch failed")
            return await super().start(spec)

    async def scenario():
        backend = FailingWakeBackend()
        first = make_service_with_backend(
            tmp_path,
            backend_selector=BackendSelector(capability_probe=lambda backend, env: True),
            backend=backend,
        )
        await spawn_service_member(first)
        await first.clear_session()

        second = make_service_with_backend(
            tmp_path,
            backend_selector=BackendSelector(capability_probe=lambda backend, env: True),
            backend=backend,
        )
        await second.create_or_attach("team-a")
        with pytest.raises(RuntimeError, match="launch failed"):
            await second.send_message(
                TeamMessage(
                    message_id="wake-fails",
                    protocol=MessageProtocol.MESSAGE,
                    sender="lead",
                    target_name="dev",
                    broadcast=False,
                    body="resume work",
                    summary="resume",
                    timestamp=NOW,
                )
            )

        assert second.store.load("team-a").members[0].state is MemberState.BLOCKED
        assert second._backend_handles == {}

    asyncio.run(scenario())
