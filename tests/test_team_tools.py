from __future__ import annotations

from mycode.team.tool_helpers import failure_result, required_bool, required_int, required_string, validate_object_arguments
from mycode.team.tool_names import LEAD_TEAM_TOOL_NAMES, MEMBER_TEAM_TOOL_NAMES, PARENT_TEAM_TOOL_NAMES, TEAM_TOOL_NAMES
from mycode.team.tools import (
    TeamArchiveTool, TeamAttachTool, TeamBatchIntegrateTool, TeamBatchStartTool, TeamCreateTool,
    TeamMemberSpawnTool, TeamMemberTerminateTool, TeamMessageSendTool, TeamPlanDecideTool, TeamPlanSubmitTool,
    TeamShutdownRequestTool, TeamShutdownResponseTool, TeamStatusTool, TeamStatusUpdateTool,
    TeamTaskClaimTool, TeamTaskCreateTool, TeamTaskDeleteTool, TeamTaskGetTool, TeamTaskListTool,
    TeamTaskTransitionTool, TeamTaskUpdateTool, register_member_team_tools, register_parent_team_tools,
)
from mycode.tool import ToolRegistry


def test_team_tool_name_sets_are_complete_and_disjoint_from_legacy_names():
    assert len(TEAM_TOOL_NAMES) == 21
    assert PARENT_TEAM_TOOL_NAMES <= TEAM_TOOL_NAMES
    assert LEAD_TEAM_TOOL_NAMES <= TEAM_TOOL_NAMES
    assert MEMBER_TEAM_TOOL_NAMES <= TEAM_TOOL_NAMES
    assert not TEAM_TOOL_NAMES & {"team", "team_lead", "team_member"}


def test_each_team_tool_has_atomic_schema_without_action_router():
    classes = (
        TeamCreateTool, TeamAttachTool, TeamStatusTool, TeamArchiveTool, TeamBatchStartTool,
        TeamBatchIntegrateTool, TeamMemberSpawnTool, TeamMemberTerminateTool, TeamTaskCreateTool,
        TeamTaskListTool, TeamTaskGetTool, TeamTaskUpdateTool, TeamTaskDeleteTool, TeamTaskClaimTool,
        TeamTaskTransitionTool, TeamPlanSubmitTool, TeamPlanDecideTool, TeamMessageSendTool,
        TeamStatusUpdateTool, TeamShutdownRequestTool, TeamShutdownResponseTool,
    )
    for cls in classes:
        kwargs = {"member_name": "dev"} if cls in {TeamPlanSubmitTool, TeamStatusUpdateTool, TeamShutdownResponseTool} else {}
        definition = cls(object(), **kwargs).definition
        assert definition.parameters["type"] == "object"
        assert definition.parameters["additionalProperties"] is False
        assert "action" not in definition.parameters["properties"]
        assert "operation" not in definition.parameters["properties"]


def test_registration_functions_expose_exact_role_sets():
    parent = ToolRegistry()
    register_parent_team_tools(parent, object())
    assert {item.name for item in parent.definitions()} == PARENT_TEAM_TOOL_NAMES | LEAD_TEAM_TOOL_NAMES
    member = ToolRegistry()
    register_member_team_tools(member, object(), member_name="dev")
    assert {item.name for item in member.definitions()} == MEMBER_TEAM_TOOL_NAMES


def test_argument_helpers_return_structured_chinese_failures_without_side_effects():
    assert validate_object_arguments({"extra": 1}, {"value"}, "team_test").content["field"] == "extra"
    assert required_string({}, "value", "team_test").content["reason_code"] == "missing_argument"
    assert required_int({"value": -1}, "value", "team_test").content["reason_code"] == "invalid_argument"
    assert required_bool({"value": "yes"}, "value", "team_test").content["reason_code"] == "invalid_argument"
    assert failure_result("team_test", "bad", "参数错误").ok is False
