from dataclasses import FrozenInstanceError, fields, is_dataclass
from datetime import datetime, timezone
from pathlib import Path

import pytest

import mycode.team as team
from mycode.team import (
    ApprovalState,
    BackendEnvironment,
    BackendHandle,
    BackendSelection,
    BatchRecord,
    BatchState,
    DeliveryReceipt,
    IntegrationReport,
    LeadLease,
    MemberBackend,
    MemberLaunchSpec,
    MemberRecord,
    MemberSpec,
    MemberState,
    MessageProtocol,
    ResolvedBackend,
    TaskKind,
    TaskPatch,
    TaskResult,
    TeamError,
    TeamMessage,
    TeamRecord,
    TeamSnapshot,
    TeamState,
    TeamTask,
    TeamTaskState,
    WakeEndpoint,
)


def utc_now() -> datetime:
    return datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)


def test_team_enums_use_stable_wire_values():
    assert TeamState.ACTIVE.value == "active"
    assert TeamState.ARCHIVED.value == "archived"
    assert TeamState.RECOVERY_REQUIRED.value == "recovery_required"

    assert MemberState.PROVISIONING.value == "provisioning"
    assert MemberState.RUNNING.value == "running"
    assert MemberState.IDLE.value == "idle"
    assert MemberState.AWAITING_APPROVAL.value == "awaiting_approval"
    assert MemberState.AWAITING_INPUT.value == "awaiting_input"
    assert MemberState.BLOCKED.value == "blocked"
    assert MemberState.STOPPING.value == "stopping"
    assert MemberState.STOPPED.value == "stopped"
    assert MemberState.FAILED.value == "failed"

    assert MemberBackend.AUTO.value == "auto"
    assert MemberBackend.TMUX.value == "tmux"
    assert MemberBackend.TERMINAL.value == "terminal"
    assert MemberBackend.IN_PROCESS.value == "in_process"

    assert ResolvedBackend.TMUX.value == "tmux"
    assert ResolvedBackend.WINDOWS_TERMINAL.value == "windows_terminal"
    assert ResolvedBackend.IN_PROCESS.value == "in_process"

    assert TaskKind.CODE.value == "code"
    assert TaskKind.READ_ONLY.value == "read_only"

    assert TeamTaskState.PENDING.value == "pending"
    assert TeamTaskState.CLAIMED.value == "claimed"
    assert TeamTaskState.AWAITING_APPROVAL.value == "awaiting_approval"
    assert TeamTaskState.AWAITING_INPUT.value == "awaiting_input"
    assert TeamTaskState.RUNNING.value == "running"
    assert TeamTaskState.BLOCKED.value == "blocked"
    assert TeamTaskState.COMPLETED.value == "completed"
    assert TeamTaskState.FAILED.value == "failed"
    assert TeamTaskState.CANCELLED.value == "cancelled"

    assert BatchState.PENDING.value == "pending"
    assert BatchState.ACTIVE.value == "active"
    assert BatchState.BLOCKED.value == "blocked"
    assert BatchState.INTEGRATING.value == "integrating"
    assert BatchState.COMPLETED.value == "completed"
    assert BatchState.FAILED.value == "failed"
    assert BatchState.CANCELLED.value == "cancelled"

    assert ApprovalState.PENDING.value == "pending"
    assert ApprovalState.APPROVED.value == "approved"
    assert ApprovalState.REJECTED.value == "rejected"
    assert ApprovalState.CANCELLED.value == "cancelled"

    assert MessageProtocol.MESSAGE.value == "message"
    assert MessageProtocol.BROADCAST.value == "broadcast"
    assert MessageProtocol.PLAN_SUBMIT.value == "plan_submit"
    assert MessageProtocol.PLAN_DECISION.value == "plan_decision"
    assert MessageProtocol.STATUS_UPDATE.value == "status_update"
    assert MessageProtocol.SHUTDOWN_REQUEST.value == "shutdown_request"
    assert MessageProtocol.SHUTDOWN_RESPONSE.value == "shutdown_response"
    assert MessageProtocol.TASK_ASSIGNMENT.value == "task_assignment"
    assert MessageProtocol.CLARIFICATION_REQUEST.value == "clarification_request"
    assert MessageProtocol.CLARIFICATION_RESPONSE.value == "clarification_response"
    assert MessageProtocol.TOOL_APPROVAL_REQUEST.value == "tool_approval_request"
    assert MessageProtocol.TOOL_APPROVAL_RESPONSE.value == "tool_approval_response"
    assert MessageProtocol.TASK_RESULT.value == "task_result"


def test_team_models_are_frozen_and_have_expected_fields(tmp_path: Path):
    now = utc_now()
    worktree_root = tmp_path / "worktree"
    context_path = tmp_path / "context.json"
    lock_path = tmp_path / "lead.lock"
    artifact_path = tmp_path / "artifact.txt"
    artifact_path.write_text("ok", encoding="utf-8")

    wake_endpoint = WakeEndpoint(
        member_name="dev",
        backend=ResolvedBackend.TMUX,
        endpoint="tmux:team-a/dev",
        revision=2,
    )
    team_record = TeamRecord(
        team_name="team-a",
        repository_root=tmp_path,
        repository_id="repo-123",
        target_branch="main",
        state=TeamState.ACTIVE,
        revision=3,
        lead_owner="lead",
        max_members=16,
        max_active_members=4,
        created_at=now,
        updated_at=now,
    )
    member_record = MemberRecord(
        member_name="dev",
        role_name="general",
        role_revision=7,
        requested_backend=MemberBackend.AUTO,
        resolved_backend=ResolvedBackend.TMUX,
        state=MemberState.RUNNING,
        approval_required=True,
        worktree_root=worktree_root,
        branch_name="mycode/team-a/dev",
        context_path=context_path,
        wake_endpoint=wake_endpoint,
        task_id="task-1",
        batch_id="batch-1",
        revision=4,
        created_at=now,
        updated_at=now,
        last_seen_at=now,
    )
    batch_record = BatchRecord(
        batch_id="batch-1",
        goal="ship the thing",
        baseline_commit="0123456789abcdef0123456789abcdef01234567",
        state=BatchState.ACTIVE,
        task_id="task-1",
        revision=5,
        integration_diagnostics=("ready",),
        created_at=now,
        updated_at=now,
    )
    task_result = TaskResult(
        summary="done",
        commit_id="89abcdef0123456789abcdef0123456789abcdef",
        verification_summary="tests passed",
        details="resolved cleanly",
        artifact_paths=(artifact_path,),
        diagnostics=("clean",),
    )
    team_task = TeamTask(
        task_id="task-1",
        batch_id="batch-1",
        title="Implement piece",
        description="Do the actual work",
        dependency_ids=("task-0",),
        kind=TaskKind.CODE,
        owner="dev",
        state=TeamTaskState.COMPLETED,
        plan_revision=2,
        approval_state=ApprovalState.APPROVED,
        result=task_result,
        error=None,
        revision=6,
        created_at=now,
        updated_at=now,
    )
    team_message = TeamMessage(
        message_id="msg-1",
        protocol=MessageProtocol.MESSAGE,
        sender="lead",
        target_name="dev",
        broadcast=False,
        body="Please continue.",
        summary="continue",
        timestamp=now,
        read=True,
        delivered=True,
        task_id="task-1",
        batch_id="batch-1",
    )
    lead_lease = LeadLease(
        team_name="team-a",
        owner="lead",
        lock_path=lock_path,
        token="lease-1",
        acquired_at=now,
        process_id=1234,
        revision=1,
        expires_at=now,
    )
    snapshot = TeamSnapshot(
        team=team_record,
        members=(member_record,),
        batches=(batch_record,),
        registry={"dev": wake_endpoint},
        lead_lease=lead_lease,
    )
    member_spec = MemberSpec(
        member_name="dev",
        role_name="general",
        role_revision=7,
        requested_backend=MemberBackend.AUTO,
        goal="ship the thing",
        batch_id="batch-1",
        task_id="task-1",
        read_only=False,
        approval_required=True,
    )
    launch_spec = MemberLaunchSpec(
        team_name="team-a",
        member_name="dev",
        role_name="general",
        role_revision=7,
        requested_backend=MemberBackend.AUTO,
        resolved_backend=ResolvedBackend.TMUX,
        argv=("python", "-m", "mycode"),
        environment={"A": "1"},
        workspace_root=worktree_root,
        repository_root=tmp_path,
        repository_id="repo-123",
        branch_name="mycode/team-a/dev",
        context_path=context_path,
        wake_endpoint=wake_endpoint,
        task_id="task-1",
        batch_id="batch-1",
        goal="ship the thing",
        approval_required=True,
        read_only=False,
        revision=1,
    )
    backend_environment = BackendEnvironment(
        requested_backend=MemberBackend.AUTO,
        platform="win32",
        shell_name="powershell",
        tmux_available=False,
        terminal_available=True,
        in_process_available=True,
        coordinator_enabled=True,
        workspace_root=tmp_path,
        repository_root=tmp_path,
        member_name="dev",
        diagnostics=("capability ok",),
    )
    backend_selection = BackendSelection(
        requested_backend=MemberBackend.AUTO,
        resolved_backend=ResolvedBackend.WINDOWS_TERMINAL,
        available=True,
        reason_code="terminal_available",
        reason="Windows Terminal available",
        environment=backend_environment,
        fallback_chain=(ResolvedBackend.WINDOWS_TERMINAL,),
    )
    backend_handle = BackendHandle(
        wake_endpoint=wake_endpoint,
        process_id=1234,
        started_at=now,
        token="handle-1",
    )
    delivery_receipt = DeliveryReceipt(
        message_id="msg-1",
        recipient_names=("dev",),
        delivered_at=now,
        fanout_count=1,
        duplicate_count=0,
    )
    integration_report = IntegrationReport(
        batch_id="batch-1",
        state=BatchState.COMPLETED,
        target_ref_before="refs/heads/main",
        target_ref_after="refs/heads/main",
        result_commit_id="fedcba9876543210fedcba9876543210fedcba98",
        conflict_task_id=None,
        integrated_member_names=("dev",),
        diagnostics=("merged",),
        started_at=now,
        completed_at=now,
    )

    models = (
        TeamRecord,
        MemberRecord,
        BatchRecord,
        TeamTask,
        TeamMessage,
        TeamSnapshot,
        TaskPatch,
        TaskResult,
        MemberSpec,
        MemberLaunchSpec,
        BackendEnvironment,
        BackendSelection,
        BackendHandle,
        LeadLease,
        WakeEndpoint,
        DeliveryReceipt,
        IntegrationReport,
        TeamError,
    )
    for model in models:
        assert is_dataclass(model)
        assert model.__dataclass_params__.frozen is True

    assert [field.name for field in fields(TeamRecord)] == [
        "team_name",
        "repository_root",
        "repository_id",
        "target_branch",
        "state",
        "revision",
        "lead_owner",
        "max_members",
        "max_active_members",
        "created_at",
        "updated_at",
    ]
    assert [field.name for field in fields(MemberRecord)] == [
        "member_name",
        "role_name",
        "role_revision",
        "requested_backend",
        "resolved_backend",
        "state",
        "approval_required",
        "worktree_root",
        "branch_name",
        "context_path",
        "wake_endpoint",
        "task_id",
        "batch_id",
        "revision",
        "created_at",
        "updated_at",
        "last_seen_at",
    ]
    assert [field.name for field in fields(BatchRecord)] == [
        "batch_id",
        "goal",
        "baseline_commit",
        "state",
        "task_id",
        "revision",
        "integration_diagnostics",
        "created_at",
        "updated_at",
        "completed_at",
        "result_commit_id",
    ]
    assert [field.name for field in fields(TeamTask)] == [
        "task_id",
        "batch_id",
        "title",
        "description",
        "dependency_ids",
        "kind",
        "owner",
        "state",
        "plan_revision",
        "approval_state",
        "result",
        "error",
        "revision",
        "created_at",
        "updated_at",
    ]
    assert [field.name for field in fields(TeamMessage)] == [
        "message_id",
        "protocol",
        "sender",
        "target_name",
        "broadcast",
        "body",
        "summary",
        "timestamp",
        "read",
        "delivered",
        "task_id",
        "batch_id",
    ]
    assert [field.name for field in fields(TeamSnapshot)] == [
        "team",
        "members",
        "batches",
        "registry",
        "lead_lease",
    ]
    assert [field.name for field in fields(TaskPatch)] == [
        "title",
        "description",
        "dependency_ids",
        "kind",
        "owner",
        "plan_revision",
        "approval_state",
    ]
    assert [field.name for field in fields(TaskResult)] == [
        "summary",
        "commit_id",
        "verification_summary",
        "details",
        "artifact_paths",
        "diagnostics",
    ]
    assert [field.name for field in fields(MemberSpec)] == [
        "member_name",
        "role_name",
        "role_revision",
        "requested_backend",
        "goal",
        "batch_id",
        "task_id",
        "read_only",
        "approval_required",
    ]
    assert [field.name for field in fields(MemberLaunchSpec)] == [
        "team_name",
        "member_name",
        "role_name",
        "role_revision",
        "requested_backend",
        "resolved_backend",
        "argv",
        "environment",
        "workspace_root",
        "repository_root",
        "repository_id",
        "branch_name",
        "context_path",
        "wake_endpoint",
        "task_id",
        "batch_id",
        "goal",
        "approval_required",
        "read_only",
        "revision",
    ]
    assert [field.name for field in fields(BackendEnvironment)] == [
        "requested_backend",
        "platform",
        "shell_name",
        "tmux_available",
        "terminal_available",
        "in_process_available",
        "coordinator_enabled",
        "workspace_root",
        "repository_root",
        "member_name",
        "diagnostics",
    ]
    assert [field.name for field in fields(BackendSelection)] == [
        "requested_backend",
        "resolved_backend",
        "available",
        "reason_code",
        "reason",
        "environment",
        "fallback_chain",
    ]
    assert [field.name for field in fields(BackendHandle)] == [
        "wake_endpoint",
        "process_id",
        "started_at",
        "token",
    ]
    assert [field.name for field in fields(LeadLease)] == [
        "team_name",
        "owner",
        "lock_path",
        "token",
        "acquired_at",
        "process_id",
        "revision",
        "expires_at",
    ]
    assert [field.name for field in fields(WakeEndpoint)] == [
        "member_name",
        "backend",
        "endpoint",
        "revision",
    ]
    assert [field.name for field in fields(DeliveryReceipt)] == [
        "message_id",
        "recipient_names",
        "delivered_at",
        "fanout_count",
        "duplicate_count",
    ]
    assert [field.name for field in fields(IntegrationReport)] == [
        "batch_id",
        "state",
        "target_ref_before",
        "target_ref_after",
        "result_commit_id",
        "conflict_task_id",
        "integrated_member_names",
        "diagnostics",
        "started_at",
        "completed_at",
    ]
    assert [field.name for field in fields(TeamError)] == [
        "code",
        "phase",
        "message",
        "team_name",
        "member_name",
        "batch_id",
        "task_id",
        "path",
        "revision",
    ]

    assert team_record.target_branch == "main"
    assert member_record.wake_endpoint == wake_endpoint
    assert batch_record.state is BatchState.ACTIVE
    assert team_task.result == task_result
    assert team_message.read is True
    assert snapshot.registry["dev"] == wake_endpoint
    assert dict(launch_spec.environment) == {"A": "1"}
    assert backend_selection.resolved_backend is ResolvedBackend.WINDOWS_TERMINAL
    assert backend_handle.wake_endpoint == wake_endpoint
    assert delivery_receipt.fanout_count == 1
    assert integration_report.result_commit_id == "fedcba9876543210fedcba9876543210fedcba98"

    with pytest.raises(FrozenInstanceError):
        team_record.revision = 4


def test_team_models_reject_invalid_state_combinations_and_paths(tmp_path: Path):
    now = utc_now()
    worktree_root = tmp_path / "worktree"
    context_path = tmp_path / "context.json"

    with pytest.raises(ValueError, match="absolute path"):
        TeamRecord(
            team_name="team-a",
            repository_root=Path("relative"),
            repository_id="repo-123",
            target_branch="main",
            state=TeamState.ACTIVE,
            revision=0,
            max_members=16,
            max_active_members=4,
            created_at=now,
            updated_at=now,
        )

    with pytest.raises(ValueError, match="max_active_members"):
        TeamRecord(
            team_name="team-a",
            repository_root=tmp_path,
            repository_id="repo-123",
            target_branch="main",
            state=TeamState.ACTIVE,
            revision=0,
            max_members=2,
            max_active_members=3,
            created_at=now,
            updated_at=now,
        )

    with pytest.raises(ValueError, match="resolved_backend"):
        MemberRecord(
            member_name="dev",
            role_name="general",
            role_revision=1,
            requested_backend=MemberBackend.AUTO,
            state=MemberState.RUNNING,
            worktree_root=worktree_root,
            branch_name="mycode/team-a/dev",
            context_path=context_path,
            revision=0,
            created_at=now,
            updated_at=now,
        )

    with pytest.raises(ValueError, match="wake_endpoint"):
        MemberRecord(
            member_name="dev",
            role_name="general",
            role_revision=1,
            requested_backend=MemberBackend.AUTO,
            resolved_backend=ResolvedBackend.TMUX,
            state=MemberState.RUNNING,
            worktree_root=worktree_root,
            branch_name="mycode/team-a/dev",
            context_path=context_path,
            revision=0,
            created_at=now,
            updated_at=now,
        )

    with pytest.raises(ValueError, match="completed_at"):
        BatchRecord(
            batch_id="batch-1",
            goal="ship",
            baseline_commit="0123456789abcdef0123456789abcdef01234567",
            state=BatchState.COMPLETED,
            task_id="task-1",
            revision=1,
            created_at=now,
            updated_at=now,
        )

    with pytest.raises(ValueError, match="result"):
        TeamTask(
            task_id="task-1",
            batch_id="batch-1",
            title="work",
            description="do work",
            dependency_ids=(),
            kind=TaskKind.CODE,
            owner="dev",
            state=TeamTaskState.COMPLETED,
            plan_revision=1,
            approval_state=ApprovalState.APPROVED,
            error=None,
            revision=1,
            created_at=now,
            updated_at=now,
        )

    with pytest.raises(ValueError, match="error"):
        TeamTask(
            task_id="task-1",
            batch_id="batch-1",
            title="work",
            description="do work",
            dependency_ids=(),
            kind=TaskKind.CODE,
            owner="dev",
            state=TeamTaskState.FAILED,
            plan_revision=1,
            approval_state=ApprovalState.REJECTED,
            result=TaskResult(summary="done"),
            revision=1,
            created_at=now,
            updated_at=now,
        )

    with pytest.raises(ValueError, match="target_name"):
        TeamMessage(
            message_id="msg-1",
            protocol=MessageProtocol.MESSAGE,
            sender="lead",
            target_name=None,
            broadcast=False,
            body="hello",
            summary="summary",
            timestamp=now,
            read=False,
            delivered=True,
        )

    with pytest.raises(ValueError, match="broadcast"):
        TeamMessage(
            message_id="msg-1",
            protocol=MessageProtocol.BROADCAST,
            sender="lead",
            target_name="dev",
            broadcast=True,
            body="hello",
            summary="summary",
            timestamp=now,
            read=False,
            delivered=True,
        )

    with pytest.raises(ValueError, match="available"):
        BackendSelection(
            requested_backend=MemberBackend.AUTO,
            resolved_backend=None,
            available=True,
            reason_code="terminal_unavailable",
            reason="no terminal",
        )

    with pytest.raises(ValueError, match="fanout_count"):
        DeliveryReceipt(
            message_id="msg-1",
            recipient_names=("dev", "ops"),
            delivered_at=now,
            fanout_count=1,
        )

    with pytest.raises(ValueError, match="revision"):
        TeamError(
            code="team_failed",
            phase="load",
            message="failed",
            path=tmp_path,
            revision=-1,
        )


def test_team_error_and_team_snapshot_validate_identity_and_exports(tmp_path: Path):
    now = utc_now()
    team_record = TeamRecord(
        team_name="team-a",
        repository_root=tmp_path,
        repository_id="repo-123",
        target_branch="main",
        state=TeamState.ACTIVE,
        revision=0,
        max_members=16,
        max_active_members=4,
        created_at=now,
        updated_at=now,
    )
    wake_endpoint = WakeEndpoint(
        member_name="dev",
        backend=ResolvedBackend.TMUX,
        endpoint="tmux:team-a/dev",
        revision=1,
    )
    snapshot = TeamSnapshot(
        team=team_record,
        members=(),
        batches=(),
        registry={"dev": wake_endpoint},
    )
    error = TeamError(
        code="team_failed",
        phase="load",
        message="load failed",
        team_name="team-a",
        member_name="dev",
        batch_id="batch-1",
        task_id="task-1",
        path=tmp_path,
        revision=1,
    )

    assert str(error) == "load failed"
    assert error.code == "team_failed"
    assert error.team_name == "team-a"
    assert snapshot.registry["dev"] == wake_endpoint
    assert set(team.__all__) == {
        "ApprovalState",
        "BackendEnvironment",
        "BackendHandle",
        "BackendSelection",
        "BatchRecord",
        "BatchState",
            "DeliveryReceipt",
            "EventFailure",
            "EventRecipientType",
            "IntegrationReport",
        "LeadLease",
        "MemberBackend",
        "MemberLaunchSpec",
        "MemberRecord",
        "MemberSpec",
        "MemberState",
        "MessageProtocol",
        "ResolvedBackend",
        "TaskKind",
        "TaskPatch",
        "TaskResult",
            "TeamError",
            "TeamEvent",
            "TeamEventState",
        "TeamMessage",
        "TeamRecord",
        "TeamSnapshot",
        "TeamState",
            "TeamTask",
                "TeamTaskState",
                "RoleEventCursor",
            "WakeEndpoint",
            "TeamRequest",
            "TeamRequestKind",
            "TeamRequestState",
            "TeamRequestStore",
        }
    assert all(not name.startswith("_") for name in team.__all__)
