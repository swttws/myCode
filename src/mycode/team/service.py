from __future__ import annotations

import asyncio
import os
import platform
import shutil
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

from mycode.team.backends import BackendSelector
from mycode.team.config import TeamConfig, coordinator_enabled_from_env
from mycode.team.context import JsonConversationMemory
from mycode.team.integration import IntegrationService
from mycode.team.locking import FileLease
from mycode.team.mailbox import MailboxStore
from mycode.team.models import (
    BackendEnvironment,
    BatchRecord,
    BatchState,
    LeadLease,
    MemberBackend,
    MemberLaunchSpec,
    MemberRecord,
    MemberState,
    MessageProtocol,
    ResolvedBackend,
    TeamError,
    TeamMessage,
    TeamRecord,
    TeamSnapshot,
    TeamState,
    WakeEndpoint,
)
from mycode.team.policy import TeamRuntimeRole, TeamToolPolicy
from mycode.team.tool_names import LEAD_TEAM_TOOL_NAMES, PARENT_TEAM_TOOL_NAMES
from mycode.team.storage import TeamStore
from mycode.team.tasks import TaskBoard


class TeamService:
    def __init__(
        self,
        *,
        store: TeamStore,
        repository_root: Path,
        repository_id: str,
        target_branch: str,
        lead_owner: str,
        config: TeamConfig | None = None,
        worktree_service=None,
        backend_selector: BackendSelector | None = None,
        backend=None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._store = store
        self._repository_root = Path(repository_root).resolve(strict=False)
        self._repository_id = repository_id
        self._target_branch = target_branch
        self._lead_owner = lead_owner
        self._config = config or TeamConfig()
        self._worktree_service = worktree_service
        self._backend_selector = backend_selector or BackendSelector()
        self._backend = backend
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._team_name: str | None = None
        self._lead_file_lease: FileLease | None = None
        self._lead_lease: LeadLease | None = None
        self._mailbox: MailboxStore | None = None
        self._task_board: TaskBoard | None = None
        self._backend_handles: dict[str, object] = {}

    @property
    def store(self) -> TeamStore:
        return self._store

    @property
    def task_board(self) -> TaskBoard:
        if self._task_board is None:
            raise TeamError(code="team_inactive", phase="task_board", message="team is not active")
        return self._task_board

    async def create_team(self, team_name: str, *, goal: str | None = None) -> TeamSnapshot:
        if (self._store.team_root(team_name) / "team.json").exists():
            raise TeamError(code="team_exists", phase="create", message="团队已存在", team_name=team_name)
        return await self._activate_team(team_name, goal=goal, create=True)

    async def attach_team(self, team_name: str) -> TeamSnapshot:
        if not (self._store.team_root(team_name) / "team.json").exists():
            raise TeamError(code="team_not_found", phase="attach", message="团队不存在", team_name=team_name)
        return await self._activate_team(team_name, goal=None, create=False)

    async def create_or_attach(self, team_name: str, *, goal: str | None = None) -> TeamSnapshot:
        """Compatibility entry retained for Stage 14 callers; new tools use explicit entrances."""
        return await self._activate_team(team_name, goal=goal, create=not (self._store.team_root(team_name) / "team.json").exists())

    async def _activate_team(self, team_name: str, *, goal: str | None, create: bool) -> TeamSnapshot:
        if self._team_name == team_name and self._lead_lease is not None:
            return self._with_lease(self._load_and_register(team_name))
        file_lease = await FileLease.acquire(
            self._store.lead_lock_path(team_name),
            config=self._config,
            owner=self._lead_owner,
        )
        self._lead_file_lease = file_lease
        self._lead_lease = LeadLease(
            team_name=team_name,
            owner=file_lease.owner,
            lock_path=file_lease.path,
            token=file_lease.token,
            acquired_at=file_lease.acquired_at,
            process_id=file_lease.process_id,
            revision=1,
            expires_at=file_lease.acquired_at + timedelta(seconds=self._config.lock_stale_after_seconds),
        )
        self._team_name = team_name
        if create:
            now = self._clock()
            snapshot = self._store.create(
                TeamRecord(
                    team_name=team_name,
                    repository_root=self._repository_root,
                    repository_id=self._repository_id,
                    target_branch=self._target_branch,
                    state=TeamState.ACTIVE,
                    lead_owner=self._lead_owner,
                    max_members=self._config.max_members,
                    max_active_members=self._config.max_active_members,
                    created_at=now,
                    updated_at=now,
                )
            )
        else:
            snapshot = self._store.load(team_name)
            self._validate_reattach(snapshot)
        self._mailbox = MailboxStore(team_name, store=self._store, config=self._config)
        self._task_board = TaskBoard(self._store, team_name, config=self._config, lock_owner=self._lead_owner)
        snapshot = self._load_and_register(team_name)
        return self._with_lease(snapshot)

    async def status(self) -> TeamSnapshot:
        return self._with_lease(self._active_snapshot())

    async def start_batch(self, goal: str) -> BatchRecord:
        snapshot = self._active_snapshot()
        self._ensure_writable(snapshot)
        now = self._clock()
        batch_id = self._next_batch_id(snapshot)
        batch = BatchRecord(
            batch_id=batch_id,
            goal=goal,
            baseline_commit=self._capture_head(),
            state=BatchState.ACTIVE,
            revision=1,
            created_at=now,
            updated_at=now,
        )
        self._store.save(replace(snapshot, batches=(*snapshot.batches, batch)))
        return batch

    async def spawn_member(
        self,
        *,
        member_name: str,
        role_name: str,
        role_revision: int,
        requested_backend: MemberBackend,
        task_id: str,
        batch_id: str,
        goal: str,
        read_only: bool,
        approval_required: bool,
    ) -> MemberRecord:
        snapshot = self._active_snapshot()
        self._ensure_writable(snapshot)
        if any(member.member_name == member_name for member in snapshot.members):
            raise TeamError(
                code="duplicate_member",
                phase="spawn",
                message=f"duplicate member: {member_name}",
                team_name=snapshot.team.team_name,
                member_name=member_name,
            )
        if len(snapshot.members) >= snapshot.team.max_members:
            raise TeamError(code="member_limit", phase="spawn", message="team member limit reached")
        running = sum(1 for member in snapshot.members if member.state is MemberState.RUNNING)
        if running >= snapshot.team.max_active_members:
            raise TeamError(code="active_member_limit", phase="spawn", message="active member limit reached")

        lease = await self._prepare_member_worktree(
            team_name=snapshot.team.team_name,
            member_name=member_name,
            role_name=role_name,
            base_commit=self._capture_head(),
        )
        try:
            workspace = lease.context
            environment = self._backend_environment(
                requested_backend=requested_backend,
                workspace_root=workspace.root,
                member_name=member_name,
            )
            selection = self._backend_selector.select(
                requested_backend,
                environment,
                priority=self._config.backend_priority,
            )
            if not selection.available or selection.resolved_backend is None:
                raise TeamError(
                    code=selection.reason_code or "backend_unavailable",
                    phase="spawn",
                    message=selection.reason or "backend unavailable",
                    team_name=snapshot.team.team_name,
                    member_name=member_name,
                )
            wake_endpoint = WakeEndpoint(
                member_name=member_name,
                backend=selection.resolved_backend,
                endpoint=f"{selection.resolved_backend.value}:{member_name}",
                revision=1,
            )
            spec = MemberLaunchSpec(
                team_name=snapshot.team.team_name,
                member_name=member_name,
                role_name=role_name,
                role_revision=role_revision,
                requested_backend=requested_backend,
                resolved_backend=selection.resolved_backend,
                argv=("mycode", "--team-worker", f"{snapshot.team.team_name}/{member_name}"),
                environment={
                    "MYCODE_TEAM": snapshot.team.team_name,
                    "MYCODE_TEAM_MEMBER": member_name,
                    "MYCODE_TEAM_ROLE": role_name,
                    "MYCODE_HOME": str(self._store.home),
                },
                workspace_root=workspace.root,
                repository_root=workspace.repository_root,
                repository_id=workspace.repository_id,
                branch_name=workspace.branch_name or f"mycode/team/{snapshot.team.team_name}/{member_name}",
                mailbox_path=self._store.mailbox_path(snapshot.team.team_name, member_name),
                context_path=self._store.context_path(snapshot.team.team_name, member_name),
                wake_endpoint=wake_endpoint,
                task_id=task_id,
                batch_id=batch_id,
                goal=goal,
                approval_required=approval_required,
                read_only=read_only,
                revision=1,
            )
            now = self._clock()
            member = MemberRecord(
                member_name=member_name,
                role_name=role_name,
                role_revision=role_revision,
                requested_backend=requested_backend,
                resolved_backend=selection.resolved_backend,
                state=MemberState.PROVISIONING,
                approval_required=approval_required,
                worktree_root=workspace.root,
                branch_name=spec.branch_name,
                mailbox_path=spec.mailbox_path,
                context_path=spec.context_path,
                wake_endpoint=wake_endpoint,
                task_id=task_id,
                batch_id=batch_id,
                revision=1,
                created_at=now,
                updated_at=now,
                last_seen_at=now,
            )
            latest = self._store.load(snapshot.team.team_name)
            registry = {**dict(latest.registry), member_name: wake_endpoint}
            self._store.save(replace(latest, members=(*latest.members, member), registry=registry))
            self._mailbox_or_error().register(member)
            handle = await self._start_backend(spec)
        except Exception:
            if "member" in locals():
                self._mark_member_failed(snapshot.team.team_name, member)
            await self._release_member_worktree(lease)
            raise
        endpoint = getattr(handle, "wake_endpoint", wake_endpoint)
        member = replace(
            member,
            state=MemberState.RUNNING,
            wake_endpoint=endpoint,
            revision=member.revision + 1,
            updated_at=self._clock(),
            last_seen_at=now,
        )
        latest = self._store.load(snapshot.team.team_name)
        registry = {**dict(latest.registry), member_name: endpoint}
        members = tuple(
            member if current.member_name == member.member_name else current
            for current in latest.members
        )
        self._store.save(replace(latest, members=members, registry=registry))
        self._backend_handles[member_name] = handle
        return member

    async def terminate_member(self, member_name: str, *, force: bool = False) -> MemberRecord:
        snapshot = self._active_snapshot()
        member = _find_member(snapshot.members, member_name)
        handle = self._backend_handles.get(member_name)
        shutdown_message_id = f"shutdown-{member_name}-{int(self._clock().timestamp() * 1000000)}"
        graceful = force
        if not force:
            seen_response_ids = self._shutdown_response_ids(member_name)
            await self.send_message(
                TeamMessage(
                    message_id=shutdown_message_id,
                    protocol=MessageProtocol.SHUTDOWN_REQUEST,
                    sender="lead",
                    target_name=member_name,
                    broadcast=False,
                    body="shutdown requested",
                    summary="shutdown requested",
                    timestamp=self._clock(),
                )
            )
            if handle is not None and self._backend is not None:
                wake = getattr(self._backend, "wake", None)
                if callable(wake):
                    result = wake(handle)
                    if asyncio.iscoroutine(result):
                        await result
            graceful = await self._wait_for_shutdown_ack(
                member_name,
                shutdown_message_id,
                seen_response_ids=seen_response_ids,
            )
        if handle is not None and self._backend is not None:
            stop = getattr(self._backend, "stop", None)
            if callable(stop):
                await stop(handle, force=force or not graceful)
        updated = replace(
            member,
            state=MemberState.STOPPED,
            revision=member.revision + 1,
            updated_at=self._clock(),
        )
        self._replace_member(snapshot, updated)
        return updated

    async def send_message(self, message):
        receipt = self._mailbox_or_error().send(message)
        for recipient in receipt.recipient_names:
            await self._wake_member(recipient)
        return receipt

    def create_task(self, task):
        return self.task_board.create(task)

    def list_tasks(self, batch_id: str | None = None):
        return self.task_board.list(batch_id)

    def get_task(self, task_id: str):
        return self.task_board.get(task_id)

    def update_task(self, task_id: str, expected_revision: int, patch):
        return self.task_board.update(task_id, expected_revision, patch)

    def delete_task(self, task_id: str, expected_revision: int) -> None:
        self.task_board.delete(task_id, expected_revision)

    def claim_task(self, task_id: str, member_name: str, expected_revision: int):
        return self.task_board.claim(task_id, member_name, expected_revision)

    def transition_task(self, task_id: str, expected_revision: int, state, result=None, error=None):
        return self.task_board.transition(task_id, expected_revision, state, result, error)

    def member_requires_approval(self, member_name: str, task_id: str) -> bool:
        snapshot = self._active_snapshot()
        member = _find_member(snapshot.members, member_name)
        return member.task_id == task_id and member.approval_required

    async def integrate_batch(self, batch_id: str, *, lead_workspace_root: Path | None = None):
        snapshot = self._active_snapshot()
        git = getattr(self._worktree_service, "git", None)
        if git is None:
            raise TeamError(code="git_unavailable", phase="integrate", message="git gateway unavailable")
        service = IntegrationService(
            store=self._store,
            team_name=snapshot.team.team_name,
            task_board=self.task_board,
            git=git,
            clock=self._clock,
        )
        return await service.integrate(batch_id, lead_workspace_root=lead_workspace_root or snapshot.team.repository_root)

    async def archive(self):
        snapshot = self._active_snapshot()
        active_batches = [
            batch
            for batch in snapshot.batches
            if batch.state
            in {
                BatchState.PENDING,
                BatchState.ACTIVE,
                BatchState.BLOCKED,
                BatchState.INTEGRATING,
            }
        ]
        active_members = [
            member
            for member in snapshot.members
            if member.state
            in {
                MemberState.PROVISIONING,
                MemberState.RUNNING,
                MemberState.AWAITING_APPROVAL,
                MemberState.BLOCKED,
                MemberState.STOPPING,
            }
        ]
        if active_batches or active_members:
            raise TeamError(
                code="team_running",
                phase="archive",
                message="team has running batches or members",
                team_name=snapshot.team.team_name,
            )
        archived = self._store.archive(snapshot.team.team_name)
        return archived

    async def clear_session(self) -> None:
        await self._stop_in_process_members()
        await self._release_lead_lease()
        self._team_name = None
        self._lead_lease = None
        self._mailbox = None
        self._task_board = None
        self._backend_handles.clear()

    async def close(self) -> None:
        await self.clear_session()

    def current_policy(self) -> TeamToolPolicy | None:
        if self._team_name is None:
            return None
        role = TeamRuntimeRole.LEAD
        return TeamToolPolicy(
            role=role,
            coordinator_enabled=coordinator_enabled_from_env(self._config),
        )

    def visible_team_tools(self, candidates: frozenset[str] | None = None) -> frozenset[str]:
        if candidates is None:
            candidates = LEAD_TEAM_TOOL_NAMES | PARENT_TEAM_TOOL_NAMES | frozenset({"read_file", "write_file", "edit_file", "run_command", "Agent"})
        policy = self.current_policy()
        if policy is None:
            return frozenset(name for name in candidates if name in PARENT_TEAM_TOOL_NAMES or name in {"read_file", "write_file", "edit_file", "run_command", "Agent"})
        return policy.visible_names(candidates)

    def _load_and_register(self, team_name: str) -> TeamSnapshot:
        snapshot = self._store.load(team_name)
        if self._mailbox is None:
            self._mailbox = MailboxStore(team_name, store=self._store, config=self._config)
        self._mailbox.register_lead()
        for member in snapshot.members:
            self._mailbox.register(member)
        return snapshot

    def _with_lease(self, snapshot: TeamSnapshot) -> TeamSnapshot:
        return replace(snapshot, lead_lease=self._lead_lease)

    def _active_snapshot(self) -> TeamSnapshot:
        if self._team_name is None:
            raise TeamError(code="team_inactive", phase="service", message="team is not active")
        return self._load_and_register(self._team_name)

    def _ensure_writable(self, snapshot: TeamSnapshot) -> None:
        if snapshot.team.state is TeamState.ARCHIVED:
            raise TeamError(
                code="team_archived",
                phase="write",
                message="team is archived and read-only",
                team_name=snapshot.team.team_name,
                revision=snapshot.team.revision,
            )

    def _validate_reattach(self, snapshot: TeamSnapshot) -> None:
        if snapshot.team.repository_id != self._repository_id:
            raise TeamError(code="repository_mismatch", phase="attach", message="repository identity mismatch")
        if snapshot.team.target_branch != self._target_branch:
            raise TeamError(code="target_branch_mismatch", phase="attach", message="target branch mismatch")
        if snapshot.team.state is TeamState.ARCHIVED:
            raise TeamError(code="team_archived", phase="attach", message="team is archived")

    def _next_batch_id(self, snapshot: TeamSnapshot) -> str:
        used = {batch.batch_id for batch in snapshot.batches}
        index = len(used) + 1
        while True:
            batch_id = f"batch-{index}"
            if batch_id not in used:
                return batch_id
            index += 1

    def _capture_head(self) -> str:
        git = getattr(self._worktree_service, "git", None)
        if git is not None:
            return git.capture_head(self._repository_root)
        return "0" * 40

    async def _prepare_member_worktree(
        self,
        *,
        team_name: str,
        member_name: str,
        role_name: str,
        base_commit: str,
    ):
        prepare_member = getattr(self._worktree_service, "prepare_member", None)
        if callable(prepare_member):
            return await prepare_member(
                team_name=team_name,
                member_name=member_name,
                role_name=role_name,
                base_commit=base_commit,
            )
        if self._worktree_service is not None:
            prepare = getattr(self._worktree_service, "prepare", None)
            if callable(prepare):
                return await prepare(role_name=role_name, task_id=member_name, task_token=member_name)
        shared_lease = getattr(self._worktree_service, "shared_lease", None)
        if callable(shared_lease):
            return shared_lease()
        raise TeamError(code="worktree_unavailable", phase="spawn", message="worktree service unavailable")

    async def _release_member_worktree(self, lease) -> None:
        release = getattr(self._worktree_service, "release", None)
        if not callable(release):
            return
        result = release(lease)
        if asyncio.iscoroutine(result):
            await result

    async def _start_backend(self, spec: MemberLaunchSpec):
        if self._backend is None:
            raise TeamError(
                code="backend_unavailable",
                phase="spawn",
                message="team backend is not configured",
                team_name=spec.team_name,
                member_name=spec.member_name,
            )
        start = getattr(self._backend, "start", None)
        if not callable(start):
            raise TeamError(code="backend_invalid", phase="spawn", message="backend start unavailable")
        result = start(spec)
        if asyncio.iscoroutine(result):
            return await result
        return result

    async def _wake_member(self, member_name: str) -> None:
        if self._backend is None:
            return
        handle = self._backend_handles.get(member_name)
        if handle is not None:
            wake = getattr(self._backend, "wake", None)
            if callable(wake):
                try:
                    result = wake(handle)
                    if asyncio.iscoroutine(result):
                        await result
                except Exception:
                    snapshot = self._active_snapshot()
                    member = next((item for item in snapshot.members if item.member_name == member_name), None)
                    if member is not None:
                        self._mark_member_blocked(snapshot.team.team_name, member)
                    self._backend_handles.pop(member_name, None)
                    raise
            return

        snapshot = self._active_snapshot()
        member = next((item for item in snapshot.members if item.member_name == member_name), None)
        if member is None or member.state in {MemberState.STOPPED, MemberState.FAILED}:
            return
        spec = self._launch_spec_from_member(snapshot, member)
        try:
            self._backend_handles[member_name] = await self._start_backend(spec)
        except Exception:
            self._mark_member_blocked(snapshot.team.team_name, member)
            raise

    def _launch_spec_from_member(self, snapshot: TeamSnapshot, member: MemberRecord) -> MemberLaunchSpec:
        if (
            member.resolved_backend is None
            or member.worktree_root is None
            or member.branch_name is None
            or member.mailbox_path is None
            or member.context_path is None
            or member.wake_endpoint is None
            or member.task_id is None
            or member.batch_id is None
        ):
            raise TeamError(
                code="member_launch_incomplete",
                phase="wake",
                message="member record lacks launch metadata",
                team_name=snapshot.team.team_name,
                member_name=member.member_name,
            )
        batch = next((item for item in snapshot.batches if item.batch_id == member.batch_id), None)
        if batch is None:
            raise TeamError(
                code="missing_batch",
                phase="wake",
                message="member batch is missing",
                team_name=snapshot.team.team_name,
                member_name=member.member_name,
                batch_id=member.batch_id,
            )
        task = self.task_board.get(member.task_id)
        return MemberLaunchSpec(
            team_name=snapshot.team.team_name,
            member_name=member.member_name,
            role_name=member.role_name,
            role_revision=member.role_revision,
            requested_backend=member.requested_backend,
            resolved_backend=member.resolved_backend,
            argv=("mycode", "--team-worker", f"{snapshot.team.team_name}/{member.member_name}"),
            environment={
                "MYCODE_TEAM": snapshot.team.team_name,
                "MYCODE_TEAM_MEMBER": member.member_name,
                "MYCODE_TEAM_ROLE": member.role_name,
                "MYCODE_HOME": str(self._store.home),
            },
            workspace_root=member.worktree_root,
            repository_root=snapshot.team.repository_root,
            repository_id=snapshot.team.repository_id,
            branch_name=member.branch_name,
            mailbox_path=member.mailbox_path,
            context_path=member.context_path,
            wake_endpoint=member.wake_endpoint,
            task_id=member.task_id,
            batch_id=member.batch_id,
            goal=batch.goal,
            approval_required=member.approval_required,
            read_only=task.kind.value == "read_only",
            revision=member.revision,
        )

    def _mark_member_failed(self, team_name: str, member: MemberRecord) -> None:
        try:
            latest = self._store.load(team_name)
            failed = replace(
                member,
                state=MemberState.FAILED,
                revision=member.revision + 1,
                updated_at=self._clock(),
            )
            members = tuple(
                failed if current.member_name == member.member_name else current
                for current in latest.members
            )
            registry = dict(latest.registry)
            registry.pop(member.member_name, None)
            self._store.save(replace(latest, members=members, registry=registry))
        except Exception:
            return

    def _mark_member_blocked(self, team_name: str, member: MemberRecord) -> None:
        try:
            latest = self._store.load(team_name)
            blocked = replace(
                member,
                state=MemberState.BLOCKED,
                revision=member.revision + 1,
                updated_at=self._clock(),
            )
            members = tuple(
                blocked if current.member_name == member.member_name else current
                for current in latest.members
            )
            self._store.save(replace(latest, members=members))
        except Exception:
            return

    async def _stop_in_process_members(self) -> None:
        if self._backend is None:
            return
        stop = getattr(self._backend, "stop", None)
        if not callable(stop):
            return
        for handle in list(self._backend_handles.values()):
            endpoint = getattr(handle, "wake_endpoint", None)
            if getattr(endpoint, "backend", None) is not ResolvedBackend.IN_PROCESS:
                continue
            result = stop(handle, force=False)
            if asyncio.iscoroutine(result):
                await result

    def _backend_environment(
        self,
        *,
        requested_backend: MemberBackend,
        workspace_root: Path,
        member_name: str,
    ) -> BackendEnvironment:
        return BackendEnvironment(
            requested_backend=requested_backend,
            platform=platform.system().lower() or "unknown",
            shell_name=Path(os.environ.get("SHELL") or os.environ.get("COMSPEC") or "shell").name,
            tmux_available=shutil.which("tmux") is not None,
            terminal_available=shutil.which("wt") is not None,
            in_process_available=True,
            coordinator_enabled=coordinator_enabled_from_env(self._config),
            workspace_root=workspace_root,
            repository_root=self._repository_root,
            member_name=member_name,
        )

    def _mailbox_or_error(self) -> MailboxStore:
        if self._mailbox is None:
            raise TeamError(code="team_inactive", phase="mailbox", message="team is not active")
        return self._mailbox

    async def _wait_for_shutdown_ack(
        self,
        member_name: str,
        shutdown_message_id: str,
        *,
        seen_response_ids: frozenset[str],
    ) -> bool:
        deadline = asyncio.get_running_loop().time() + self._config.graceful_shutdown_timeout_seconds
        interval = min(self._config.lock_retry_interval_seconds, 0.05)
        while True:
            if self._has_shutdown_response(
                member_name,
                seen_response_ids,
            ) or self._has_shutdown_checkpoint(member_name, shutdown_message_id):
                return True
            if asyncio.get_running_loop().time() >= deadline:
                return False
            await asyncio.sleep(interval)

    def _has_shutdown_response(self, member_name: str, seen_response_ids: frozenset[str]) -> bool:
        return bool(self._shutdown_response_ids(member_name) - seen_response_ids)

    def _shutdown_response_ids(self, member_name: str) -> frozenset[str]:
        try:
            messages = self._mailbox_or_error().receive("lead")
        except Exception:
            return frozenset()
        return frozenset(
            message.message_id
            for message in messages
            if (
                message.protocol is MessageProtocol.SHUTDOWN_RESPONSE
                and message.sender == member_name
            )
        )

    def _has_shutdown_checkpoint(self, member_name: str, shutdown_message_id: str) -> bool:
        try:
            memory = JsonConversationMemory(
                path=self._store.context_path(self._team_name or "", member_name),
                max_bytes=self._config.context_max_bytes,
            )
        except Exception:
            return False
        checkpoint = memory.checkpoint
        return (
            checkpoint.get("last_message_id") == shutdown_message_id
            or checkpoint.get("shutdown_request_id") == shutdown_message_id
        )

    def _replace_member(self, snapshot: TeamSnapshot, replacement: MemberRecord) -> None:
        members = tuple(
            replacement if member.member_name == replacement.member_name else member
            for member in snapshot.members
        )
        self._store.save(replace(snapshot, members=members))

    async def _release_lead_lease(self) -> None:
        lease = self._lead_file_lease
        if lease is None:
            return
        self._lead_file_lease = None
        try:
            await lease.release()
        except TeamError:
            return


def _find_member(members: tuple[MemberRecord, ...], member_name: str) -> MemberRecord:
    for member in members:
        if member.member_name == member_name:
            return member
    raise TeamError(code="missing_member", phase="member", message=f"missing member: {member_name}", member_name=member_name)


__all__ = ["TeamService"]
