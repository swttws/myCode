from __future__ import annotations

TEAM_TOOL_NAMES = frozenset(
    {
        "team_create", "team_attach", "team_status", "team_archive",
        "team_batch_start", "team_batch_integrate", "team_member_spawn", "team_member_terminate",
        "team_task_create", "team_task_list", "team_task_get", "team_task_update",
        "team_task_delete", "team_task_claim", "team_task_transition",
        "team_plan_submit", "team_plan_decide", "team_message_send", "team_status_update",
        "team_shutdown_request", "team_shutdown_response", "team_clarification_request",
        "team_request_list", "team_clarification_respond", "team_tool_approval_respond",
        "team_user_decision_request",
    }
)
PARENT_TEAM_TOOL_NAMES = frozenset({"team_create", "team_attach", "team_status"})
LEAD_TEAM_TOOL_NAMES = frozenset(
    {
        "team_status", "team_archive", "team_batch_start", "team_batch_integrate",
        "team_member_spawn", "team_member_terminate", "team_task_create", "team_task_list",
        "team_task_get", "team_task_update", "team_task_delete", "team_task_claim",
        "team_task_transition", "team_plan_decide", "team_message_send", "team_shutdown_request",
        "team_request_list", "team_clarification_respond", "team_tool_approval_respond",
        "team_user_decision_request",
    }
)
MEMBER_TEAM_TOOL_NAMES = frozenset(
    {
        "team_task_create", "team_task_list", "team_task_get", "team_task_update",
        "team_task_delete", "team_task_claim", "team_task_transition", "team_plan_submit",
        "team_message_send", "team_status_update", "team_shutdown_response",
        "team_clarification_request",
    }
)
READ_TEAM_TOOL_NAMES = frozenset({"team_status", "team_task_list", "team_task_get"})
WRITE_TEAM_TOOL_NAMES = TEAM_TOOL_NAMES - READ_TEAM_TOOL_NAMES
LEGACY_TEAM_TOOL_NAMES = frozenset({"team", "team_lead", "team_member"})

__all__ = [
    "TEAM_TOOL_NAMES", "PARENT_TEAM_TOOL_NAMES", "LEAD_TEAM_TOOL_NAMES", "MEMBER_TEAM_TOOL_NAMES",
    "READ_TEAM_TOOL_NAMES", "WRITE_TEAM_TOOL_NAMES", "LEGACY_TEAM_TOOL_NAMES",
]
