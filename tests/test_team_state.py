from __future__ import annotations

from mycode.team.domain.state import (
    SupervisorState,
    TeamPhase,
    TeamRuntimeRole,
    TeamRuntimeState,
    build_tool_manifest,
    phase_for_snapshot,
)


def test_supervisor_states_use_stable_wire_values() -> None:
    assert SupervisorState.IDLE.value == "idle"
    assert SupervisorState.RUNNING_LEAD.value == "running_lead"
    assert SupervisorState.WAITING_MEMBER.value == "waiting_member"
    assert SupervisorState.WAITING_USER.value == "waiting_user"
    assert SupervisorState.COMPLETED.value == "completed"
    assert SupervisorState.FAILED.value == "failed"
    assert SupervisorState.STOPPING.value == "stopping"


def test_inactive_state_only_exposes_lifecycle_tools() -> None:
    state = TeamRuntimeState.inactive()

    assert state.role is TeamRuntimeRole.PARENT
    assert state.phase is TeamPhase.INACTIVE
    assert state.team_name is None
    assert state.coordinator_mode is False
    assert state.ordinary_agent_allowed is True

    manifest = build_tool_manifest(
        state,
        frozenset({"team_create", "team_attach", "team_status", "team_batch_start", "Agent"}),
    )
    assert manifest.visible_names == frozenset({"team_create", "team_attach", "team_status", "Agent"})


def test_lead_phases_are_derived_from_snapshot_facts() -> None:
    assert phase_for_snapshot(active=True) is TeamPhase.LEAD_READY
    assert phase_for_snapshot(active=True, has_batch=True) is TeamPhase.TASK_PLANNING
    assert phase_for_snapshot(active=True, has_batch=True, has_tasks=True, has_dispatchable_task=True) is TeamPhase.DISPATCH_READY
    assert phase_for_snapshot(active=True, has_batch=True, has_tasks=True, has_running_work=True) is TeamPhase.EXECUTING
    assert phase_for_snapshot(active=True, has_batch=True, has_tasks=True, all_tasks_completed=True) is TeamPhase.INTEGRATING
    assert phase_for_snapshot(active=True, archived=True) is TeamPhase.ARCHIVED


def test_lead_manifest_includes_only_phase_ready_operations() -> None:
    state = TeamRuntimeState(
        role=TeamRuntimeRole.LEAD,
        phase=TeamPhase.TASK_PLANNING,
        team_name="team-a",
        batch_id="batch-1",
        manifest_epoch=2,
        coordinator_mode=True,
        ordinary_agent_allowed=False,
        local_write_allowed=False,
        command_allowed=False,
    )

    manifest = build_tool_manifest(
        state,
        frozenset(
            {
                "team_status",
                "team_task_create",
                "team_member_spawn",
                "team_batch_integrate",
                "Agent",
                "read_file",
                "write_file",
            }
        ),
    )

    assert manifest.epoch == 2
    assert manifest.role is TeamRuntimeRole.LEAD
    assert manifest.phase is TeamPhase.TASK_PLANNING
    assert "team_status" in manifest.visible_names
    assert "team_task_create" in manifest.visible_names
    assert "team_member_spawn" not in manifest.visible_names
    assert "team_batch_integrate" not in manifest.visible_names
    assert "Agent" not in manifest.visible_names
    assert "read_file" in manifest.visible_names
    assert "write_file" not in manifest.visible_names
    assert manifest.forbidden_actions_zh


def test_member_state_is_isolated_from_lead_and_lifecycle_tools() -> None:
    state = TeamRuntimeState(
        role=TeamRuntimeRole.MEMBER,
        phase=TeamPhase.EXECUTING,
        team_name="team-a",
        batch_id="batch-1",
        manifest_epoch=4,
        coordinator_mode=False,
        ordinary_agent_allowed=False,
        local_write_allowed=True,
        command_allowed=False,
    )

    manifest = build_tool_manifest(
        state,
        frozenset(
            {
                "team_create",
                "team_status",
                "team_task_get",
                "team_status_update",
                "team_batch_integrate",
                "Agent",
                "read_file",
                "write_file",
            }
        ),
    )

    assert manifest.visible_names == frozenset({"team_task_get", "team_status_update", "read_file", "write_file"})
