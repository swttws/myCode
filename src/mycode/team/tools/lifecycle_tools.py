from __future__ import annotations

from mycode.tool import ToolDefinition, ToolKind, ToolRuntimeScope
from mycode.team.tool_helpers import (
    error_result,
    field,
    failure_result,
    maybe_await,
    optional_string,
    required_string,
    schema,
    success_result,
    validate_object_arguments,
)


class _LifecycleTool:
    tool_name = ""
    description = ""
    properties: dict = {}
    required: tuple[str, ...] = ()
    kind = ToolKind.WRITE

    def __init__(self, service) -> None:
        self._service = service

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            self.tool_name,
            self.description,
            schema(self.properties, self.required),
            self.kind,
            requires_approval=False,
            runtime_scope=ToolRuntimeScope.PARENT_ONLY,
        )

    async def execute_async(self, arguments, context=None):
        invalid = validate_object_arguments(arguments, self.properties, self.tool_name)
        if invalid:
            return invalid
        arguments = arguments or {}
        missing = [name for name in self.required if name not in arguments]
        if missing:
            return failure_result(
                self.tool_name,
                "missing_argument",
                f"缺少必填参数：{missing[0]}",
                missing[0],
            )
        try:
            return await self._execute(arguments)
        except Exception as exc:
            return error_result(self.tool_name, exc)


class TeamCreateTool(_LifecycleTool):
    tool_name = "team_create"
    description = "创建一个新的持久化团队。"
    properties = {
        "team_name": field("团队名称，必须是非空字符串。"),
        "goal": field("团队总体目标，可选。"),
    }
    required = ("team_name",)

    async def _execute(self, arguments):
        name = required_string(arguments, "team_name", self.tool_name)
        if not isinstance(name, str):
            return name
        goal = optional_string(arguments, "goal", self.tool_name)
        if not isinstance(goal, (str, type(None))):
            return goal
        snapshot = await maybe_await(self._service.create_team(name, goal=goal))
        return success_result(
            self.tool_name,
            {
                "team_name": snapshot.team.team_name,
                "state": snapshot.team.state.value,
                "activated": True,
            },
        )


class TeamAttachTool(_LifecycleTool):
    tool_name = "team_attach"
    description = "接管一个已存在的团队。"
    properties = {"team_name": field("要接管的团队名称，必须是非空字符串。")}
    required = ("team_name",)

    async def _execute(self, arguments):
        name = required_string(arguments, "team_name", self.tool_name)
        if not isinstance(name, str):
            return name
        snapshot = await maybe_await(self._service.attach_team(name))
        return success_result(
            self.tool_name,
            {
                "team_name": snapshot.team.team_name,
                "state": snapshot.team.state.value,
                "activated": True,
            },
        )


class TeamStatusTool(_LifecycleTool):
    tool_name = "team_status"
    description = "查看当前团队、成员和批次状态。"
    kind = ToolKind.READ

    async def _execute(self, arguments):
        snapshot = await maybe_await(self._service.status())
        return success_result(
            self.tool_name,
            {
                "team_name": snapshot.team.team_name,
                "state": snapshot.team.state.value,
                "member_count": len(snapshot.members),
                "batch_count": len(snapshot.batches),
            },
        )


class TeamArchiveTool(_LifecycleTool):
    tool_name = "team_archive"
    description = "归档一个已停止且安静的团队。"

    async def _execute(self, arguments):
        team = await maybe_await(self._service.archive())
        return success_result(
            self.tool_name,
            {
                "team_name": team.team_name,
                "state": team.state.value,
                "archived": team.state.value == "archived",
            },
        )


__all__ = ["TeamCreateTool", "TeamAttachTool", "TeamStatusTool", "TeamArchiveTool"]
