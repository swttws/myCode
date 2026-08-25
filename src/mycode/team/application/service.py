from __future__ import annotations

import asyncio
import json
import logging
import os
import platform
import shutil
import sys
import time
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

from mycode.team.execution.backends import BackendSelector
from mycode.team.infrastructure.config import TeamConfig, coordinator_enabled_from_env
from mycode.team.infrastructure.context import JsonConversationMemory
from mycode.team.domain.roles import LEAD_ROLE_NAME
from mycode.team.infrastructure.events import TeamEventStore
from mycode.team.application.integration import IntegrationService
from mycode.team.infrastructure.locking import FileLease
from mycode.team.execution.notifier import TeamEventNotifier
from mycode.team.domain.models import (
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
    TaskKind,
    TeamTaskState,
    WakeEndpoint,
)
from mycode.team.tooling.policy import TeamToolPolicy
from mycode.team.domain.state import (
    TeamPhase,
    TeamRuntimeRole,
    TeamRuntimeState,
    TeamToolManifest,
    build_tool_manifest,
    phase_for_snapshot,
)
from mycode.team.tooling.tool_names import LEAD_TEAM_TOOL_NAMES, PARENT_TEAM_TOOL_NAMES
from mycode.team.infrastructure.storage import TeamStore
from mycode.team.application.tasks import TaskBoard
from mycode.team.infrastructure.requests import TeamRequest, TeamRequestKind, TeamRequestState, TeamRequestStore


logger = logging.getLogger("mycode.team.service")


def _log_fields(**context: object) -> dict[str, object]:
    return {key: value for key, value in context.items() if value is not None and value != ""}


def _event_value(value: object) -> object:
    return getattr(value, "value", value)


def _elapsed_ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)


def _member_worker_argv(
    team_name: str,
    member_name: str,
    *,
    home: Path,
    config_path: Path | None,
) -> tuple[str, ...]:
    argv = (
        sys.executable,
        "-m",
        "mycode",
        "--team-worker",
        f"{team_name}/{member_name}",
        "--home",
        str(home),
    )
    if config_path is not None:
        argv += ("--config", str(config_path))
    return argv


def _member_worker_pythonpath() -> str:
    source_root = str(Path(__file__).resolve().parents[2])
    existing = os.environ.get("PYTHONPATH")
    return source_root if not existing else source_root + os.pathsep + existing


def _is_event_driven_backend(member: MemberRecord | None) -> bool:
    """Check if a member's backend supports event-driven consumption."""
    if member is None:
        return False
    return member.resolved_backend in (None, ResolvedBackend.IN_PROCESS)


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
        config_path: Path | None = None,
        event_notifier: TeamEventNotifier | None = None,
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
        self._config_path = Path(config_path).resolve(strict=False) if config_path is not None else None
        self._team_name: str | None = None
        self._lead_file_lease: FileLease | None = None
        self._lead_lease: LeadLease | None = None
        self._events: TeamEventStore | None = None
        self._notifier_owned = event_notifier is None
        self._notifier = event_notifier or TeamEventNotifier()
        self._task_board: TaskBoard | None = None
        self._backend_handles: dict[str, object] = {}
        self._shutdown_response_cache: dict[str, set[str]] = {}
        self._pending_shutdown_responses: dict[str, dict[str, asyncio.Future[None]]] = {}
        self._request_store = TeamRequestStore(store)
        self._runtime_state = TeamRuntimeState.inactive()

    @property
    def store(self) -> TeamStore:
        return self._store

    @property
    def event_store(self) -> TeamEventStore:
        return self._events_or_error()

    @property
    def event_notifier(self) -> TeamEventNotifier:
        return self._notifier

    @property
    def task_board(self) -> TaskBoard:
        if self._task_board is None:
            raise TeamError(code="team_inactive", phase="task_board", message="team is not active")
        return self._task_board

    @property
    def team_name(self) -> str | None:
        return self._team_name

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
        started = time.perf_counter()
        logger.info(
            "team.activate.started",
            extra=_log_fields(
                team_name=team_name,
                action="create" if create else "attach",
                phase="activate",
                goal=goal,
                path=self._store.lead_lock_path(team_name),
            ),
        )
        if self._team_name == team_name and self._lead_lease is not None:
            snapshot = self._with_lease(self._load_snapshot(team_name))
            logger.info(
                "team.activate.completed",
                extra=_log_fields(
                    team_name=team_name,
                    action="create" if create else "attach",
                    phase="activate",
                    state=_event_value(snapshot.team.state),
                    duration_ms=_elapsed_ms(started),
                    path=self._store.lead_lock_path(team_name),
                    reused=True,
                ),
            )
            return snapshot
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
        if self._team_name != team_name:
            self._shutdown_response_cache.clear()
            if self._notifier_owned:
                self._notifier = TeamEventNotifier()
        self._team_name = team_name
        try:
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
            self._events = TeamEventStore(team_name, store=self._store, config=self._config)
            self._task_board = TaskBoard(self._store, team_name, config=self._config, lock_owner=self._lead_owner)
            snapshot = self._load_snapshot(team_name)
            await self._restore_event_subscriptions(snapshot)
            if not create:
                await self._restore_in_process_members(snapshot)
            leased = self._with_lease(snapshot)
            logger.info(
                "team.activate.completed",
                extra=_log_fields(
                    team_name=team_name,
                    action="create" if create else "attach",
                    phase="activate",
                    state=_event_value(leased.team.state),
                    duration_ms=_elapsed_ms(started),
                    path=self._store.lead_lock_path(team_name),
                ),
            )
            return leased
        except Exception:
            logger.exception(
                "team.activate.failed",
                extra=_log_fields(
                    team_name=team_name,
                    action="create" if create else "attach",
                    phase="activate",
                    duration_ms=_elapsed_ms(started),
                    path=self._store.lead_lock_path(team_name),
                ),
            )
            try:
                await self._release_lead_lease()
            except Exception:
                logger.exception(
                    "team.activate.cleanup_failed",
                    extra=_log_fields(
                        team_name=team_name,
                        action="create" if create else "attach",
                        phase="activate",
                        path=self._store.lead_lock_path(team_name),
                    ),
                )
            await self._clear_activation_state()
            raise

    async def status(self) -> TeamSnapshot:
        started = time.perf_counter()
        try:
            snapshot = self._with_lease(self._active_snapshot())
            logger.info(
                "team.status.started",
                extra=_log_fields(
                    team_name=snapshot.team.team_name,
                    action="status",
                    phase="status",
                ),
            )
            logger.info(
                "team.status.completed",
                extra=_log_fields(
                    team_name=snapshot.team.team_name,
                    action="status",
                    phase="status",
                    state=_event_value(snapshot.team.state),
                    member_count=len(snapshot.members),
                    batch_count=len(snapshot.batches),
                    duration_ms=_elapsed_ms(started),
                ),
            )
            return snapshot
        except Exception:
            logger.exception(
                "team.status.failed",
                extra=_log_fields(
                    team_name=self._team_name,
                    action="status",
                    phase="status",
                    duration_ms=_elapsed_ms(started),
                ),
            )
            raise

    async def start_batch(self, goal: str) -> BatchRecord:
        snapshot = self._active_snapshot()
        started = time.perf_counter()
        logger.info(
            "team.batch.started",
            extra=_log_fields(
                team_name=snapshot.team.team_name,
                action="start_batch",
                phase="batch",
                goal=goal,
            ),
        )
        try:
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
            logger.info(
                "team.batch.completed",
                extra=_log_fields(
                    team_name=snapshot.team.team_name,
                    action="start_batch",
                    phase="batch",
                    batch_id=batch.batch_id,
                    goal=batch.goal,
                    state=_event_value(batch.state),
                    duration_ms=_elapsed_ms(started),
                ),
            )
            return batch
        except Exception:
            logger.exception(
                "team.batch.failed",
                extra=_log_fields(
                    team_name=snapshot.team.team_name,
                    action="start_batch",
                    phase="batch",
                    goal=goal,
                    duration_ms=_elapsed_ms(started),
                ),
            )
            raise

    async def spawn_member(
        self,
        *,
        member_name: str,
        role_name: str,
        role_revision: int | None = None,
        requested_backend: MemberBackend | None = None,
        task_id: str,
        batch_id: str | None = None,
        goal: str,
        read_only: bool | None = None,
        approval_required: bool | None = None,
    ) -> MemberRecord:
        snapshot = self._active_snapshot()
        (
            role_revision,
            requested_backend,
            batch_id,
            read_only,
            approval_required,
        ) = self._resolve_member_spawn_parameters(
            role_name=role_name,
            role_revision=role_revision,
            requested_backend=requested_backend,
            task_id=task_id,
            batch_id=batch_id,
            read_only=read_only,
            approval_required=approval_required,
        )
        started = time.perf_counter()
        logger.info(
            "team.member.spawn.started",
            extra=_log_fields(
                team_name=snapshot.team.team_name,
                action="spawn_member",
                phase="spawn",
                member_name=member_name,
                role_name=role_name,
                batch_id=batch_id,
                task_id=task_id,
                goal=goal,
                requested_backend=_event_value(requested_backend),
                read_only=read_only,
                approval_required=approval_required,
            ),
        )
        lease = None
        member = None
        try:
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
                argv=_member_worker_argv(
                    snapshot.team.team_name,
                    member_name,
                    home=self._store.home,
                    config_path=self._config_path,
                ),
                environment={
                    "MYCODE_TEAM": snapshot.team.team_name,
                    "MYCODE_TEAM_MEMBER": member_name,
                    "MYCODE_TEAM_ROLE": role_name,
                    "MYCODE_HOME": str(self._store.home),
                    "PYTHONPATH": _member_worker_pythonpath(),
                    **({"MYCODE_CONFIG": str(self._config_path)} if self._config_path is not None else {}),
                },
                workspace_root=workspace.root,
                repository_root=workspace.repository_root,
                repository_id=workspace.repository_id,
                branch_name=workspace.branch_name or f"mycode/team/{snapshot.team.team_name}/{member_name}",
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
            handle = await self._start_backend(spec)
        except Exception:
            logger.exception(
                "team.member.spawn.failed",
                extra=_log_fields(
                    team_name=snapshot.team.team_name,
                    action="spawn_member",
                    phase="spawn",
                    member_name=member_name,
                    role_name=role_name,
                    batch_id=batch_id,
                    task_id=task_id,
                    duration_ms=_elapsed_ms(started),
                ),
            )
            if member is not None:
                self._mark_member_failed(snapshot.team.team_name, member)
            if lease is not None:
                await self._release_member_worktree(lease)
            raise
        endpoint = handle.wake_endpoint
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
        self._subscribe_role(member_name)
        if _is_event_driven_backend(member):
            await self.send_message(
                TeamMessage(
                    message_id=f"assignment-{member_name}-{task_id}",
                    protocol=MessageProtocol.TASK_ASSIGNMENT,
                    sender="lead",
                    target_name=member_name,
                    broadcast=False,
                    body=f"Task: {task_id}\nGoal: {goal}",
                    summary=f"Task assignment: {task_id}",
                    timestamp=self._clock(),
                    task_id=task_id,
                    batch_id=batch_id,
                )
            )
        logger.info(
            "team.member.spawn.completed",
            extra=_log_fields(
                team_name=snapshot.team.team_name,
                action="spawn_member",
                phase="spawn",
                member_name=member_name,
                role_name=role_name,
                batch_id=batch_id,
                task_id=task_id,
                state=_event_value(member.state),
                resolved_backend=_event_value(member.resolved_backend),
                duration_ms=_elapsed_ms(started),
            ),
        )
        return member

    async def terminate_member(self, member_name: str, *, force: bool = False) -> MemberRecord:
        snapshot = self._active_snapshot()
        started = time.perf_counter()
        logger.info(
            "team.member.terminate.started",
            extra=_log_fields(
                team_name=snapshot.team.team_name,
                action="terminate_member",
                phase="terminate",
                member_name=member_name,
                force=force,
            ),
        )
        try:
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
                    await self._backend.wake(handle)
                graceful = await self._wait_for_shutdown_ack(
                    member_name,
                    shutdown_message_id,
                    seen_response_ids=seen_response_ids,
                )
                if not graceful:
                    graceful = self._has_shutdown_response(member_name, seen_response_ids)
            if handle is not None and self._backend is not None:
                await self._backend.stop(handle, force=force or not graceful)
            updated = replace(
                member,
                state=MemberState.STOPPED,
                revision=member.revision + 1,
                updated_at=self._clock(),
            )
            self._replace_member(snapshot, updated)
            logger.info(
                "team.member.terminate.completed",
                extra=_log_fields(
                    team_name=snapshot.team.team_name,
                    action="terminate_member",
                    phase="terminate",
                    member_name=member_name,
                    force=force,
                    state=_event_value(updated.state),
                    duration_ms=_elapsed_ms(started),
                ),
            )
            return updated
        except Exception:
            logger.exception(
                "team.member.terminate.failed",
                extra=_log_fields(
                    team_name=snapshot.team.team_name,
                    action="terminate_member",
                    phase="terminate",
                    member_name=member_name,
                    force=force,
                    duration_ms=_elapsed_ms(started),
                ),
            )
            raise

    async def send_message(self, message):
        started = time.perf_counter()
        logger.info(
            "team.message.send.started",
            extra=_log_fields(
                team_name=self._team_name,
                action="send_message",
                phase="message",
                message_id=message.message_id,
                target_name=message.target_name,
                batch_id=message.batch_id,
                task_id=message.task_id,
            ),
        )
        pending_response = self._begin_shutdown_response(message)
        try:
            self._validate_message_sender(message)
            receipt = self._events_or_error().append_message(
                message,
                recipients=self._event_recipients(message),
            )
            self._remember_shutdown_response(message, receipt.recipient_names)
            for recipient in receipt.recipient_names:
                await self._notifier.notify(recipient)
                if recipient != "lead":
                    await self._wake_member(recipient)
            logger.info(
                "team.message.sent",
                extra=_log_fields(
                    team_name=self._team_name,
                    action="send_message",
                    phase="message",
                    message_id=receipt.message_id,
                    target_name=message.target_name,
                    batch_id=message.batch_id,
                    task_id=message.task_id,
                    duration_ms=_elapsed_ms(started),
                ),
            )
            return receipt
        except Exception:
            logger.exception(
                "team.message.send.failed",
                extra=_log_fields(
                    team_name=self._team_name,
                    action="send_message",
                    phase="message",
                    message_id=message.message_id,
                    target_name=message.target_name,
                    batch_id=message.batch_id,
                    task_id=message.task_id,
                    duration_ms=_elapsed_ms(started),
                ),
            )
            raise
        finally:
            self._finish_shutdown_response(pending_response)

    def create_request(self, request: TeamRequest) -> TeamRequest:
        snapshot = self._active_snapshot()
        if request.team_name != snapshot.team.team_name:
            raise TeamError(code="request_team_mismatch", phase="request", message="request team does not match active team")
        return self._request_store.create(request)

    def list_requests(self, *, state: TeamRequestState | None = None) -> tuple[TeamRequest, ...]:
        snapshot = self._active_snapshot()
        return self._request_store.list(snapshot.team.team_name, state=state)

    def get_request(self, request_id: str) -> TeamRequest:
        snapshot = self._active_snapshot()
        return self._request_store.get(snapshot.team.team_name, request_id)

    async def resolve_request(
        self,
        request_id: str,
        *,
        resolution: str,
        resolved_by: str = "lead",
        state: TeamRequestState = TeamRequestState.RESOLVED,
        message_id: str | None = None,
        protocol: MessageProtocol = MessageProtocol.CLARIFICATION_RESPONSE,
    ) -> TeamRequest:
        current = self.get_request(request_id)
        if current.kind is TeamRequestKind.USER_DECISION and resolved_by == "lead":
            raise TeamError(code="user_decision_pending", phase="request", message="user decision must be resolved by the user")
        resolved = self._request_store.resolve(
            current.team_name,
            request_id,
            state=state,
            resolution=resolution,
            resolved_by=resolved_by,
        )
        if current.member_name != "lead":
            body = json.dumps(
                {"request_id": request_id, "resolution": resolution, "approved": state is TeamRequestState.RESOLVED},
                ensure_ascii=False,
                separators=(",", ":"),
            )
            await self.send_message(
                TeamMessage(
                    message_id=message_id or f"response-{request_id}",
                    protocol=protocol,
                    sender="lead",
                    target_name=current.member_name,
                    broadcast=False,
                    body=body,
                    summary=resolution,
                    task_id=current.task_id,
                    batch_id=current.batch_id,
                    timestamp=self._clock(),
                )
            )
        return resolved

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

    def _resolve_member_spawn_parameters(
        self,
        *,
        role_name: str,
        role_revision: int | None,
        requested_backend: MemberBackend | None,
        task_id: str,
        batch_id: str | None,
        read_only: bool | None,
        approval_required: bool | None,
    ) -> tuple[int, MemberBackend, str, bool, bool]:
        task = self.task_board.get(task_id)
        resolved_batch_id = batch_id or task.batch_id
        if resolved_batch_id != task.batch_id:
            raise TeamError(
                code="task_batch_mismatch",
                phase="spawn",
                message="成员任务与批次不匹配",
                task_id=task_id,
                batch_id=resolved_batch_id,
            )
        resolved_read_only = task.kind is TaskKind.READ_ONLY if read_only is None else read_only
        return (
            self._resolve_role_revision(role_name) if role_revision is None else role_revision,
            requested_backend or MemberBackend.AUTO,
            resolved_batch_id,
            resolved_read_only,
            (not resolved_read_only) if approval_required is None else approval_required,
        )

    def _resolve_role_revision(self, role_name: str) -> int:
        if type(role_name) is not str or not role_name:
            raise ValueError("role_name must be a non-empty string")
        return 0

    async def integrate_batch(self, batch_id: str, *, lead_workspace_root: Path | None = None):
        snapshot = self._active_snapshot()
        started = time.perf_counter()
        logger.info(
            "team.batch.integrate.started",
            extra=_log_fields(
                team_name=snapshot.team.team_name,
                action="integrate_batch",
                phase="integrate",
                batch_id=batch_id,
            ),
        )
        try:
            if self._worktree_service is None:
                raise TeamError(code="git_unavailable", phase="integrate", message="git gateway unavailable")
            git = self._worktree_service.git
            service = IntegrationService(
                store=self._store,
                team_name=snapshot.team.team_name,
                task_board=self.task_board,
                git=git,
                clock=self._clock,
            )
            report = await service.integrate(batch_id, lead_workspace_root=lead_workspace_root or snapshot.team.repository_root)
            logger.info(
                "team.batch.integrate.completed",
                extra=_log_fields(
                    team_name=snapshot.team.team_name,
                    action="integrate_batch",
                    phase="integrate",
                    batch_id=batch_id,
                    state=_event_value(report.state),
                    duration_ms=_elapsed_ms(started),
                ),
            )
            return report
        except Exception:
            logger.exception(
                "team.batch.integrate.failed",
                extra=_log_fields(
                    team_name=snapshot.team.team_name,
                    action="integrate_batch",
                    phase="integrate",
                    batch_id=batch_id,
                    duration_ms=_elapsed_ms(started),
                ),
            )
            raise

    async def archive(self):
        snapshot = self._active_snapshot()
        started = time.perf_counter()
        logger.info(
            "team.archive.started",
            extra=_log_fields(
                team_name=snapshot.team.team_name,
                action="archive",
                phase="archive",
            ),
        )
        try:
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
                    MemberState.AWAITING_INPUT,
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
            logger.info(
                "team.archive.completed",
                extra=_log_fields(
                    team_name=snapshot.team.team_name,
                    action="archive",
                    phase="archive",
                    state=_event_value(archived.state),
                    duration_ms=_elapsed_ms(started),
                ),
            )
            return archived
        except Exception:
            logger.exception(
                "team.archive.failed",
                extra=_log_fields(
                    team_name=snapshot.team.team_name,
                    action="archive",
                    phase="archive",
                    duration_ms=_elapsed_ms(started),
                ),
            )
            raise

    async def clear_session(self) -> None:
        started = time.perf_counter()
        team_name = self._team_name
        logger.info(
            "team.session.clear.started",
            extra=_log_fields(
                team_name=team_name,
                action="clear_session",
                phase="session",
            ),
        )
        try:
            await self._stop_in_process_members()
            await self._release_lead_lease()
        except Exception:
            logger.exception(
                "team.session.clear.failed",
                extra=_log_fields(
                    team_name=team_name,
                    action="clear_session",
                    phase="session",
                    duration_ms=_elapsed_ms(started),
                ),
            )
            raise
        finally:
            await self._clear_activation_state()
        logger.info(
            "team.session.cleared",
            extra=_log_fields(
                team_name=team_name,
                action="clear_session",
                phase="session",
                duration_ms=_elapsed_ms(started),
            ),
        )

    async def close(self) -> None:
        await self.clear_session()

    def current_policy(self) -> TeamToolPolicy | None:
        state = self.runtime_state()
        if state.phase is TeamPhase.INACTIVE:
            return None
        return TeamToolPolicy(
            role=state.role,
            coordinator_enabled=state.coordinator_mode,
        )

    def visible_team_tools(self, candidates: frozenset[str] | None = None) -> frozenset[str]:
        if candidates is None:
            candidates = (
                LEAD_TEAM_TOOL_NAMES
                | PARENT_TEAM_TOOL_NAMES
                | frozenset({
                    "team",
                    "team_lead",
                    "team_member",
                    "read_file",
                    "write_file",
                    "edit_file",
                    "run_command",
                    "Agent",
                    "find_files",
                    "search_code",
                })
            )
        state = self.runtime_state()
        visible = build_tool_manifest(state, candidates).visible_names
        if state.phase is TeamPhase.INACTIVE and "team" in candidates:
            visible = visible | frozenset({"team"})
        return visible

    def runtime_state(self) -> TeamRuntimeState:
        if self._team_name is None:
            return self._runtime_state
        snapshot = self._load_snapshot(self._team_name)
        return self._refresh_runtime_state(snapshot)

    def tool_manifest(self, candidates: frozenset[str] | None = None) -> TeamToolManifest:
        if candidates is None:
            candidates = self.visible_team_tools()
        state = self.runtime_state()
        return build_tool_manifest(state, candidates)

    def prompt_context(self) -> str:
        return self.runtime_state().prompt_context()

    def _load_snapshot(self, team_name: str) -> TeamSnapshot:
        return self._store.load(team_name)

    def _subscribe_role(self, role_name: str) -> None:
        self._events_or_error().register_role(role_name)
        self._notifier.register_queue(role_name)

    async def _restore_event_subscriptions(self, snapshot: TeamSnapshot) -> None:
        role_names = (LEAD_ROLE_NAME, *(member.member_name for member in snapshot.members))
        for role_name in role_names:
            self._subscribe_role(role_name)
        await self._notify_pending_roles(role_names)

    async def _restore_in_process_members(self, snapshot: TeamSnapshot) -> None:
        if self._backend is None:
            return
        for member in snapshot.members:
            if member.resolved_backend is not ResolvedBackend.IN_PROCESS:
                continue
            if member.state in {MemberState.STOPPED, MemberState.FAILED}:
                continue
            if member.member_name in self._backend_handles:
                continue
            try:
                await self._wake_member(member.member_name)
            except Exception:
                logger.exception(
                    "team.member.restore.failed",
                    extra=_log_fields(
                        team_name=snapshot.team.team_name,
                        action="restore_member",
                        phase="restore",
                        member_name=member.member_name,
                    ),
                )

    async def _notify_pending_roles(self, role_names: tuple[str, ...]) -> None:
        events = self._events_or_error()
        pending = tuple(role_name for role_name in role_names if events.next_event(role_name) is not None)
        if pending:
            await self._notifier.notify_many(pending)

    def _with_lease(self, snapshot: TeamSnapshot) -> TeamSnapshot:
        return replace(snapshot, lead_lease=self._lead_lease)

    def _active_snapshot(self) -> TeamSnapshot:
        if self._team_name is None:
            raise TeamError(code="team_inactive", phase="service", message="team is not active")
        snapshot = self._load_snapshot(self._team_name)
        self._refresh_runtime_state(snapshot)
        return snapshot

    def _refresh_runtime_state(self, snapshot: TeamSnapshot) -> TeamRuntimeState:
        if snapshot.team.state is TeamState.ARCHIVED:
            phase = TeamPhase.ARCHIVED
            batch_id = None
        else:
            batches = tuple(snapshot.batches)
            active_batch = next(
                (
                    batch
                    for batch in reversed(batches)
                    if batch.state
                    not in {BatchState.CANCELLED, BatchState.FAILED, BatchState.COMPLETED}
                ),
                None,
            )
            batch_id = active_batch.batch_id if active_batch is not None else None
            tasks = self._task_board.list(batch_id) if batch_id is not None and self._task_board is not None else ()
            phase = phase_for_snapshot(
                active=snapshot.team.state is TeamState.ACTIVE,
                archived=False,
                has_batch=active_batch is not None,
                has_tasks=bool(tasks),
                has_dispatchable_task=any(task.state is TeamTaskState.PENDING for task in tasks),
                has_running_work=any(
                    member.state
                    in {
                        MemberState.PROVISIONING,
                        MemberState.RUNNING,
                        MemberState.AWAITING_APPROVAL,
                        MemberState.AWAITING_INPUT,
                        MemberState.BLOCKED,
                    }
                    for member in snapshot.members
                )
                or any(
                    task.state
                    in {
                        TeamTaskState.CLAIMED,
                        TeamTaskState.RUNNING,
                        TeamTaskState.BLOCKED,
                        TeamTaskState.AWAITING_APPROVAL,
                        TeamTaskState.AWAITING_INPUT,
                    }
                    for task in tasks
                ),
                all_tasks_completed=bool(tasks)
                and all(task.state is TeamTaskState.COMPLETED for task in tasks),
            )
        current = self._runtime_state
        changed = (
            current.phase is not phase
            or current.team_name != snapshot.team.team_name
            or current.batch_id != batch_id
            or current.role is not TeamRuntimeRole.LEAD
        )
        epoch = current.manifest_epoch + 1 if changed else current.manifest_epoch
        self._runtime_state = TeamRuntimeState(
            role=TeamRuntimeRole.LEAD,
            phase=phase,
            team_name=snapshot.team.team_name,
            batch_id=batch_id,
            manifest_epoch=epoch,
            coordinator_mode=True,
            ordinary_agent_allowed=False,
            local_write_allowed=False,
            command_allowed=False,
        )
        return self._runtime_state

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
        if self._worktree_service is not None:
            return self._worktree_service.git.capture_head(self._repository_root)
        return "0" * 40

    async def _prepare_member_worktree(
        self,
        *,
        team_name: str,
        member_name: str,
        role_name: str,
        base_commit: str,
    ):
        if self._worktree_service is not None:
            return await self._worktree_service.prepare_member(
                team_name=team_name,
                member_name=member_name,
                role_name=role_name,
                base_commit=base_commit,
            )
        raise TeamError(code="worktree_unavailable", phase="spawn", message="worktree service unavailable")

    async def _release_member_worktree(self, lease) -> None:
        if self._worktree_service is None:
            return
        await self._worktree_service.release(lease)

    async def _start_backend(self, spec: MemberLaunchSpec):
        if self._backend is None:
            raise TeamError(
                code="backend_unavailable",
                phase="spawn",
                message="team backend is not configured",
                team_name=spec.team_name,
                member_name=spec.member_name,
            )
        return await self._backend.start(spec)

    async def _wake_member(self, member_name: str) -> None:
        if self._backend is None:
            return
        handle = self._backend_handles.get(member_name)
        started = time.perf_counter()
        if handle is not None:
            try:
                logger.info(
                    "team.member.wake.started",
                    extra=_log_fields(
                        team_name=self._team_name,
                        action="wake_member",
                        phase="wake",
                        member_name=member_name,
                    ),
                )
                await self._backend.wake(handle)
                logger.info(
                    "team.member.wake.completed",
                    extra=_log_fields(
                        team_name=self._team_name,
                        action="wake_member",
                        phase="wake",
                        member_name=member_name,
                        duration_ms=_elapsed_ms(started),
                    ),
                )
            except Exception:
                logger.exception(
                    "team.member.wake.failed",
                    extra=_log_fields(
                        team_name=self._team_name,
                        action="wake_member",
                        phase="wake",
                        member_name=member_name,
                        duration_ms=_elapsed_ms(started),
                    ),
                )
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
            logger.info(
                "team.member.wake.started",
                extra=_log_fields(
                    team_name=snapshot.team.team_name,
                    action="wake_member",
                    phase="wake",
                    member_name=member_name,
                ),
            )
            self._backend_handles[member_name] = await self._start_backend(spec)
            logger.info(
                "team.member.wake.completed",
                extra=_log_fields(
                    team_name=snapshot.team.team_name,
                    action="wake_member",
                    phase="wake",
                    member_name=member_name,
                    duration_ms=_elapsed_ms(started),
                ),
            )
        except Exception:
            logger.exception(
                "team.member.wake.failed",
                extra=_log_fields(
                    team_name=snapshot.team.team_name,
                    action="wake_member",
                    phase="wake",
                    member_name=member_name,
                    duration_ms=_elapsed_ms(started),
                ),
            )
            self._mark_member_blocked(snapshot.team.team_name, member)
            raise

    def _launch_spec_from_member(self, snapshot: TeamSnapshot, member: MemberRecord) -> MemberLaunchSpec:
        if (
            member.resolved_backend is None
            or member.worktree_root is None
            or member.branch_name is None
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
            argv=_member_worker_argv(
                snapshot.team.team_name,
                member.member_name,
                home=self._store.home,
                config_path=self._config_path,
            ),
            environment={
                "MYCODE_TEAM": snapshot.team.team_name,
                "MYCODE_TEAM_MEMBER": member.member_name,
                "MYCODE_TEAM_ROLE": member.role_name,
                "MYCODE_HOME": str(self._store.home),
                "PYTHONPATH": _member_worker_pythonpath(),
                **({"MYCODE_CONFIG": str(self._config_path)} if self._config_path is not None else {}),
            },
            workspace_root=member.worktree_root,
            repository_root=snapshot.team.repository_root,
            repository_id=snapshot.team.repository_id,
            branch_name=member.branch_name,
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
        for handle in list(self._backend_handles.values()):
            endpoint = handle.wake_endpoint
            if endpoint.backend is not ResolvedBackend.IN_PROCESS:
                continue
            await self._backend.stop(handle, force=False)

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

    def _events_or_error(self) -> TeamEventStore:
        if self._events is None:
            raise TeamError(code="team_not_active", phase="events", message="team is not active")
        return self._events

    def _event_recipients(self, message: TeamMessage) -> tuple[str, ...]:
        from mycode.team.application.messaging import event_recipients

        return event_recipients(message, self._active_snapshot(), backend_supported=_is_event_driven_backend)

    def _validate_message_sender(self, message: TeamMessage) -> None:
        from mycode.team.application.messaging import validate_message_sender

        validate_message_sender(message, self._active_snapshot(), backend_supported=_is_event_driven_backend)

    async def _wait_for_shutdown_ack(
        self,
        member_name: str,
        shutdown_message_id: str,
        *,
        seen_response_ids: frozenset[str],
    ) -> bool:
        deadline = asyncio.get_running_loop().time() + self._config.graceful_shutdown_timeout_seconds
        interval = min(self._config.lock_retry_interval_seconds, 0.05)
        disk_interval = max(interval, 0.1)
        next_disk_check = asyncio.get_running_loop().time() + disk_interval
        while asyncio.get_running_loop().time() < deadline:
            if self._cached_shutdown_response_ids(member_name) - seen_response_ids:
                return True
            pending = self._pending_shutdown_response_futures(member_name, seen_response_ids)
            if pending:
                await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
                continue
            now = asyncio.get_running_loop().time()
            if now >= next_disk_check:
                if self._event_shutdown_response_ids(member_name) - seen_response_ids:
                    return True
                next_disk_check = now + disk_interval
            await asyncio.sleep(interval)
        return self._has_shutdown_checkpoint(member_name, shutdown_message_id)

    def _has_shutdown_response(self, member_name: str, seen_response_ids: frozenset[str]) -> bool:
        if self._cached_shutdown_response_ids(member_name) - seen_response_ids:
            return True
        return bool(self._event_shutdown_response_ids(member_name) - seen_response_ids)

    def _shutdown_response_ids(self, member_name: str) -> frozenset[str]:
        return self._cached_shutdown_response_ids(member_name) | self._event_shutdown_response_ids(member_name)

    def _cached_shutdown_response_ids(self, member_name: str) -> frozenset[str]:
        return frozenset(self._shutdown_response_cache.get(member_name, set()))

    def _event_shutdown_response_ids(self, member_name: str) -> frozenset[str]:
        try:
            events = self._events_or_error().events_for_role("lead")
        except Exception:
            return frozenset()
        return frozenset(
            event.message.message_id
            for event in events
            if (
                event.message.protocol is MessageProtocol.SHUTDOWN_RESPONSE
                and event.message.sender == member_name
            )
        )

    def _begin_shutdown_response(self, message: TeamMessage) -> tuple[str, str] | None:
        if message.protocol is not MessageProtocol.SHUTDOWN_RESPONSE:
            return None
        if not (message.broadcast or message.target_name == "lead"):
            return None
        future = asyncio.get_running_loop().create_future()
        self._pending_shutdown_responses.setdefault(message.sender, {})[message.message_id] = future
        return (message.sender, message.message_id)

    def _finish_shutdown_response(self, response_key: tuple[str, str] | None) -> None:
        if response_key is None:
            return
        member_name, message_id = response_key
        futures = self._pending_shutdown_responses.get(member_name)
        if futures is None:
            return
        future = futures.pop(message_id, None)
        if not futures:
            self._pending_shutdown_responses.pop(member_name, None)
        if future is not None and not future.done():
            future.set_result(None)

    def _pending_shutdown_response_futures(
        self,
        member_name: str,
        seen_response_ids: frozenset[str],
    ) -> set[asyncio.Future[None]]:
        return {
            future
            for message_id, future in self._pending_shutdown_responses.get(member_name, {}).items()
            if message_id not in seen_response_ids and not future.done()
        }

    def _remember_shutdown_response(self, message: TeamMessage, recipient_names: tuple[str, ...]) -> None:
        if message.protocol is not MessageProtocol.SHUTDOWN_RESPONSE:
            return
        if "lead" not in recipient_names:
            return
        self._shutdown_response_cache.setdefault(message.sender, set()).add(message.message_id)

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

    async def _clear_activation_state(self) -> None:
        self._team_name = None
        self._lead_lease = None
        self._events = None
        if self._notifier_owned:
            self._notifier = TeamEventNotifier()
        self._task_board = None
        self._backend_handles.clear()
        self._shutdown_response_cache.clear()
        self._pending_shutdown_responses.clear()
        self._runtime_state = TeamRuntimeState.inactive()


def _find_member(members: tuple[MemberRecord, ...], member_name: str) -> MemberRecord:
    for member in members:
        if member.member_name == member_name:
            return member
    raise TeamError(code="missing_member", phase="member", message=f"missing member: {member_name}", member_name=member_name)


__all__ = ["TeamService"]
