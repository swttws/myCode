from __future__ import annotations

import asyncio

from mycode.team.tooling.tool_helpers import failure_result, required_bool, required_int, required_string, validate_object_arguments
from mycode.team.tooling.tool_names import LEAD_TEAM_TOOL_NAMES, MEMBER_TEAM_TOOL_NAMES, PARENT_TEAM_TOOL_NAMES, TEAM_TOOL_NAMES
from mycode.team.tooling.lead_tools import (
    TeamArchiveTool, TeamAttachTool, TeamBatchIntegrateTool, TeamBatchStartTool, TeamCreateTool,
    TeamMemberSpawnTool, TeamMemberTerminateTool, TeamMessageSendTool, TeamPlanDecideTool,
    TeamShutdownRequestTool, TeamStatusTool,
    TeamTaskClaimTool, TeamTaskCreateTool, TeamTaskDeleteTool, TeamTaskGetTool, TeamTaskListTool,
    TeamTaskTransitionTool, TeamTaskUpdateTool, register_lead_team_tools,
)
from mycode.team.tooling.member_tools import (
    TeamPlanSubmitTool,
    TeamShutdownResponseTool,
    TeamStatusUpdateTool,
    register_member_team_tools,
)
from mycode.tool import ToolRegistry


def test_team_tool_name_sets_are_complete_and_disjoint_from_legacy_names():
    assert len(TEAM_TOOL_NAMES) == 26
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


def test_team_member_spawn_schema_uses_service_derived_parameters():
    definition = TeamMemberSpawnTool(object()).definition

    assert definition.parameters["required"] == ["member_name", "role_name", "task_id", "goal"]
    assert set(definition.parameters["properties"]) == {"member_name", "role_name", "task_id", "goal"}


def test_registration_functions_expose_exact_role_sets():
    parent = ToolRegistry()
    register_lead_team_tools(parent, object())
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
import asyncio

import pytest

def test_lifecycle_tool_logs_exception_before_structured_failure(caplog):
    class BoomService:
        async def create_team(self, name, goal=None):
            raise RuntimeError("create failed")

    caplog.set_level("ERROR")
    tool = TeamCreateTool(BoomService())

    result = asyncio.run(tool.execute_async({"team_name": "alpha", "goal": "ship"}))

    assert result.ok is False
    assert result.content["reason_code"] == "team_action_failed"
    record = next((item for item in caplog.records if item.name.endswith("lead_tools")), None)
    assert record is not None
    assert record.exc_info is not None
    assert "team_create" in record.message
    assert "team_name=alpha" in record.message
