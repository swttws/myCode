from __future__ import annotations

from mycode.tool import ToolRegistry
from mycode.team.tool_names import LEAD_TEAM_TOOL_NAMES, MEMBER_TEAM_TOOL_NAMES
from .lifecycle_tools import TeamArchiveTool, TeamAttachTool, TeamCreateTool, TeamStatusTool
from .orchestration_tools import TeamBatchIntegrateTool, TeamBatchStartTool, TeamMemberSpawnTool, TeamMemberTerminateTool
from .task_tools import TeamTaskClaimTool, TeamTaskCreateTool, TeamTaskDeleteTool, TeamTaskGetTool, TeamTaskListTool, TeamTaskTransitionTool, TeamTaskUpdateTool
from .protocol_tools import TeamMessageSendTool, TeamPlanDecideTool, TeamPlanSubmitTool, TeamShutdownRequestTool, TeamShutdownResponseTool, TeamStatusUpdateTool


def register_parent_team_tools(registry: ToolRegistry, service) -> None:
    tools = (
        TeamCreateTool(service), TeamAttachTool(service), TeamStatusTool(service), TeamArchiveTool(service),
        TeamBatchStartTool(service), TeamBatchIntegrateTool(service), TeamMemberSpawnTool(service), TeamMemberTerminateTool(service),
        TeamTaskCreateTool(service), TeamTaskListTool(service), TeamTaskGetTool(service), TeamTaskUpdateTool(service),
        TeamTaskDeleteTool(service), TeamTaskClaimTool(service), TeamTaskTransitionTool(service), TeamPlanDecideTool(service),
        TeamMessageSendTool(service), TeamShutdownRequestTool(service),
    )
    for tool in tools:
        registry.register(tool)


def register_member_team_tools(registry: ToolRegistry, service, *, member_name: str) -> None:
    tools = (
        TeamTaskCreateTool(service, member_name), TeamTaskListTool(service, member_name), TeamTaskGetTool(service, member_name),
        TeamTaskUpdateTool(service, member_name), TeamTaskDeleteTool(service, member_name), TeamTaskClaimTool(service, member_name),
        TeamTaskTransitionTool(service, member_name), TeamPlanSubmitTool(service, member_name), TeamMessageSendTool(service, member_name),
        TeamStatusUpdateTool(service, member_name), TeamShutdownResponseTool(service, member_name),
    )
    for tool in tools:
        registry.register(tool)


__all__ = [
    "TeamCreateTool", "TeamAttachTool", "TeamStatusTool", "TeamArchiveTool", "TeamBatchStartTool", "TeamBatchIntegrateTool",
    "TeamMemberSpawnTool", "TeamMemberTerminateTool", "TeamTaskCreateTool", "TeamTaskListTool", "TeamTaskGetTool", "TeamTaskUpdateTool",
    "TeamTaskDeleteTool", "TeamTaskClaimTool", "TeamTaskTransitionTool", "TeamPlanSubmitTool", "TeamPlanDecideTool", "TeamMessageSendTool",
    "TeamStatusUpdateTool", "TeamShutdownRequestTool", "TeamShutdownResponseTool", "register_parent_team_tools", "register_member_team_tools",
]
