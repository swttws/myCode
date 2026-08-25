from __future__ import annotations

import logging
from datetime import datetime, timezone

from mycode.team.tooling.member_tools import (
    TeamMessageSendTool,
    TeamTaskClaimTool,
    TeamTaskCreateTool,
    TeamTaskDeleteTool,
    TeamTaskGetTool,
    TeamTaskListTool,
    TeamTaskTransitionTool,
    TeamTaskUpdateTool,
    _required,
)
from mycode.team.domain.models import (
    ApprovalState,
    MessageProtocol,
    TaskPatch,
    TeamError,
    TeamMessage,
    TeamTaskState,
)
from mycode.team.infrastructure.requests import TeamRequest, TeamRequestKind, TeamRequestState
from mycode.team.tooling.tool_helpers import (
    batch_content,
    error_result,
    failure_result,
    field,
    maybe_await,
    message_content,
    optional_bool,
    optional_string,
    required_bool,
    required_int,
    required_string,
    schema,
    success_result,
    task_content,
    validate_object_arguments,
)
from mycode.tool import ToolDefinition, ToolExecutionControl, ToolKind, ToolResult, ToolRuntimeScope


logger = logging.getLogger("mycode.team.lead_tools")


def _context_text(**context: object) -> str:
    return " ".join(f"{key}={value}" for key, value in context.items() if value is not None)


class _LeadTool:
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
        missing = next((name for name in self.required if name not in arguments), None)
        if missing:
            return failure_result(self.tool_name, "missing_argument", f"缺少必填参数：{missing}", missing)
        try:
            return await self._execute(arguments)
        except Exception as exc:
            logger.exception(
                _context_text(
                    tool_name=self.tool_name,
                    team_name=arguments.get("team_name"),
                    member_name=arguments.get("member_name"),
                    batch_id=arguments.get("batch_id"),
                    task_id=arguments.get("task_id"),
                    message_id=arguments.get("message_id"),
                )
            )
            return error_result(self.tool_name, exc)


class TeamCreateTool(_LeadTool):
    tool_name = "team_create"
    description = "创建一个新的持久化团队。"
    properties = {
        "team_name": field("团队名称，必须是非空字符串。"),
        "goal": field("团队总体目标，可选。"),
    }
    required = ("team_name",)

    async def _execute(self, arguments):
        team_name = required_string(arguments, "team_name", self.tool_name)
        if not isinstance(team_name, str):
            return team_name
        goal = optional_string(arguments, "goal", self.tool_name)
        if not isinstance(goal, (str, type(None))):
            return goal
        snapshot = await maybe_await(self._service.create_team(team_name, goal=goal))
        return ToolResult(
            ok=True,
            tool_name=self.tool_name,
            content={
                "team_name": snapshot.team.team_name,
                "state": snapshot.team.state.value,
                "activated": True,
            },
            control=ToolExecutionControl(stop_current_round=True, replan_next_round=True),
        )


class TeamAttachTool(_LeadTool):
    tool_name = "team_attach"
    description = "接管一个已存在的团队。"
    properties = {"team_name": field("要接管的团队名称，必须是非空字符串。")}
    required = ("team_name",)

    async def _execute(self, arguments):
        team_name = required_string(arguments, "team_name", self.tool_name)
        if not isinstance(team_name, str):
            return team_name
        snapshot = await maybe_await(self._service.attach_team(team_name))
        return ToolResult(
            ok=True,
            tool_name=self.tool_name,
            content={
                "team_name": snapshot.team.team_name,
                "state": snapshot.team.state.value,
                "activated": True,
            },
            control=ToolExecutionControl(stop_current_round=True, replan_next_round=True),
        )


class TeamStatusTool(_LeadTool):
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


class TeamArchiveTool(_LeadTool):
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


class TeamBatchStartTool(_LeadTool):
    tool_name = "team_batch_start"
    description = "启动一个团队批次。"
    properties = {"goal": field("批次目标，必须是非空字符串。")}
    required = ("goal",)

    async def _execute(self, arguments):
        goal = required_string(arguments, "goal", self.tool_name)
        if not isinstance(goal, str):
            return goal
        batch = await maybe_await(self._service.start_batch(goal))
        return success_result(self.tool_name, batch_content(batch))


class TeamBatchIntegrateTool(_LeadTool):
    tool_name = "team_batch_integrate"
    description = "整合一个已完成的团队批次。"
    properties = {"batch_id": field("待整合的批次标识。")}
    required = ("batch_id",)

    async def _execute(self, arguments):
        batch_id = required_string(arguments, "batch_id", self.tool_name)
        if not isinstance(batch_id, str):
            return batch_id
        report = await maybe_await(self._service.integrate_batch(batch_id))
        return success_result(
            self.tool_name,
            {
                "batch_id": report.batch_id,
                "state": report.state.value,
                "result_commit_id": report.result_commit_id,
                "conflict_task_id": report.conflict_task_id,
                "integrated_member_names": list(report.integrated_member_names),
            },
        )


class TeamMemberSpawnTool(_LeadTool):
    tool_name = "team_member_spawn"
    description = "派生一个绑定任务的团队成员；批次、角色版本、后端和审批限制由服务端推导。"
    properties = {
        "member_name": field("成员名称。"),
        "role_name": field("成员角色名称。"),
        "task_id": field("成员负责的任务标识。"),
        "goal": field("成员工作目标。"),
    }
    required = ("member_name", "role_name", "task_id", "goal")

    async def _execute(self, arguments):
        values = {}
        for name in self.required:
            value = required_string(arguments, name, self.tool_name)
            if not isinstance(value, str):
                return value
            values[name] = value
        member = await maybe_await(self._service.spawn_member(**values))
        return success_result(
            self.tool_name,
            {
                "member_name": member.member_name,
                "state": member.state.value,
            },
        )


class TeamMemberTerminateTool(_LeadTool):
    tool_name = "team_member_terminate"
    description = "终止一个团队成员。"
    properties = {
        "member_name": field("要终止的成员名称。"),
        "force": field("是否强制终止，默认 false。", "boolean"),
    }
    required = ("member_name",)

    async def _execute(self, arguments):
        member_name = required_string(arguments, "member_name", self.tool_name)
        if not isinstance(member_name, str):
            return member_name
        force = optional_bool(arguments, "force", False, self.tool_name)
        if not isinstance(force, bool):
            return force
        member = await maybe_await(self._service.terminate_member(member_name, force=force))
        return success_result(
            self.tool_name,
            {
                "member_name": member.member_name,
                "state": member.state.value,
            },
        )


_MESSAGE_FIELDS = {
    "message_id": field("消息唯一标识。"),
    "target_name": field("定向消息目标。"),
    "broadcast": field("是否广播，默认 false。", "boolean"),
    "body": field("消息正文。"),
    "summary": field("消息摘要，可选。"),
    "sender": field("发送者，Lead 默认。"),
    "task_id": field("关联任务标识，可选。"),
    "batch_id": field("关联批次标识，可选。"),
}


async def _send_lead_message(service, tool_name: str, arguments, protocol: MessageProtocol):
    broadcast = optional_bool(arguments, "broadcast", False, tool_name)
    if not isinstance(broadcast, bool):
        return broadcast
    target_name = None if broadcast else arguments.get("target_name")
    if not broadcast and (type(target_name) is not str or not target_name):
        return failure_result(tool_name, "missing_argument", "定向消息必须提供 target_name", "target_name")
    body = required_string(arguments, "body", tool_name)
    if not isinstance(body, str):
        return body
    summary = optional_string(arguments, "summary", tool_name)
    if not isinstance(summary, (str, type(None))):
        return summary
    receipt = await maybe_await(
        service.send_message(
            TeamMessage(
                message_id=_required(arguments, "message_id", tool_name),
                protocol=protocol,
                sender=arguments.get("sender") or "lead",
                target_name=target_name,
                broadcast=broadcast,
                body=body,
                summary=summary or body,
                timestamp=datetime.now(timezone.utc),
                task_id=arguments.get("task_id"),
                batch_id=arguments.get("batch_id"),
            )
        )
    )
    return success_result(tool_name, message_content(receipt))


class TeamShutdownRequestTool(_LeadTool):
    tool_name = "team_shutdown_request"
    description = "请求团队成员保存检查点并停止。"
    properties = _MESSAGE_FIELDS
    required = ("message_id", "body")

    async def _execute(self, arguments):
        return await _send_lead_message(self._service, self.tool_name, arguments, MessageProtocol.SHUTDOWN_REQUEST)


class TeamPlanDecideTool(_LeadTool):
    tool_name = "team_plan_decide"
    description = "校验并批准或拒绝团队任务计划。"
    properties = {
        "message_id": field("决定消息标识，可选。"),
        "target_name": field("通知目标，可选。"),
        "task_id": field("任务标识。"),
        "batch_id": field("批次标识，可选。"),
        "expected_revision": field("任务版本号。", "integer"),
        "plan_revision": field("待审批计划版本。", "integer"),
        "approved": field("是否批准。", "boolean"),
        "reason": field("拒绝原因。"),
        "body": field("决定正文，可选。"),
        "summary": field("决定摘要，可选。"),
        "sender": field("发送者，Lead 默认。"),
    }
    required = ("task_id", "expected_revision", "plan_revision", "approved")

    async def _execute(self, arguments):
        task_id = _required(arguments, "task_id", self.tool_name)
        revision = required_int(arguments, "expected_revision", self.tool_name)
        plan_revision = required_int(arguments, "plan_revision", self.tool_name)
        approved = required_bool(arguments, "approved", self.tool_name)
        if not isinstance(revision, int):
            return revision
        if not isinstance(plan_revision, int):
            return plan_revision
        if not isinstance(approved, bool):
            return approved
        current = await maybe_await(self._service.get_task(task_id))
        if current.plan_revision != plan_revision:
            raise TeamError(
                code="plan_revision_mismatch",
                phase="plan",
                message="计划版本与当前任务不匹配",
            )
        if not approved and (type(arguments.get("reason")) is not str or not arguments.get("reason")):
            return failure_result(self.tool_name, "missing_argument", "拒绝计划时必须提供 reason", "reason")
        updated = await maybe_await(
            self._service.update_task(
                task_id,
                revision,
                TaskPatch(approval_state=ApprovalState.APPROVED if approved else ApprovalState.REJECTED),
            )
        )
        if approved:
            updated = await maybe_await(
                self._service.transition_task(
                    task_id,
                    updated.revision,
                    TeamTaskState.RUNNING,
                )
            )
        content = task_content(updated)
        if "message_id" in arguments:
            sent = await _send_lead_message(self._service, self.tool_name, arguments, MessageProtocol.PLAN_DECISION)
            if not sent.ok:
                return sent
            content["message_id"] = sent.content["message_id"]
        return success_result(self.tool_name, content)


def _request_content(request) -> dict[str, object]:
    return {
        "request_id": request.request_id,
        "team_name": request.team_name,
        "batch_id": request.batch_id,
        "task_id": request.task_id,
        "member_name": request.member_name,
        "kind": request.kind.value,
        "question": request.question,
        "options": list(request.options),
        "context_summary": request.context_summary,
        "state": request.state.value,
        "resolution": request.resolution,
        "resolved_by": request.resolved_by,
    }


class TeamRequestListTool(_LeadTool):
    """List durable requests for the active team without running the user flow."""

    tool_name = "team_request_list"
    description = "查看当前团队的协作请求。"
    kind = ToolKind.READ
    properties = {"state": field("按请求状态过滤，可选。", enum=[item.value for item in TeamRequestState])}

    async def _execute(self, arguments):
        state = arguments.get("state")
        if state is not None:
            try:
                state = TeamRequestState(state)
            except ValueError:
                return failure_result(self.tool_name, "invalid_argument", "state 无效", "state")
        requests = await maybe_await(self._service.list_requests(state=state))
        return success_result(self.tool_name, {"requests": [_request_content(item) for item in requests]})


class TeamClarificationRespondTool(_LeadTool):
    tool_name = "team_clarification_respond"
    description = "直接回答成员澄清问题并唤醒成员。"
    properties = {
        "request_id": field("澄清请求标识。"),
        "resolution": field("给成员的明确答案。"),
        "message_id": field("响应消息标识，可选。"),
    }
    required = ("request_id", "resolution")

    async def _execute(self, arguments):
        request_id = _required(arguments, "request_id", self.tool_name)
        resolution = _required(arguments, "resolution", self.tool_name)
        request = await maybe_await(self._service.get_request(request_id))
        if request.kind is not TeamRequestKind.CLARIFICATION:
            raise TeamError(code="request_kind_mismatch", phase="request", message="request is not a clarification request")
        resolved = await maybe_await(
            self._service.resolve_request(
                request_id,
                resolution=resolution,
                resolved_by="lead",
                state=TeamRequestState.RESOLVED,
                message_id=arguments.get("message_id"),
                protocol=MessageProtocol.CLARIFICATION_RESPONSE,
            )
        )
        return success_result(self.tool_name, _request_content(resolved))


class TeamToolApprovalRespondTool(_LeadTool):
    tool_name = "team_tool_approval_respond"
    description = "批准或拒绝成员的工具动作请求。"
    properties = {
        "request_id": field("工具审批请求标识。"),
        "approved": field("是否批准。", "boolean"),
        "resolution": field("审批说明。"),
        "message_id": field("响应消息标识，可选。"),
    }
    required = ("request_id", "approved", "resolution")

    async def _execute(self, arguments):
        request_id = _required(arguments, "request_id", self.tool_name)
        approved = required_bool(arguments, "approved", self.tool_name)
        resolution = _required(arguments, "resolution", self.tool_name)
        if not isinstance(approved, bool):
            return approved
        request = await maybe_await(self._service.get_request(request_id))
        if request.kind is not TeamRequestKind.TOOL_APPROVAL:
            raise TeamError(code="request_kind_mismatch", phase="request", message="request is not a tool approval request")
        resolved = await maybe_await(
            self._service.resolve_request(
                request_id,
                resolution=resolution,
                resolved_by="lead",
                state=TeamRequestState.RESOLVED if approved else TeamRequestState.REJECTED,
                message_id=arguments.get("message_id"),
                protocol=MessageProtocol.TOOL_APPROVAL_RESPONSE,
            )
        )
        return success_result(self.tool_name, _request_content(resolved))


class TeamUserDecisionRequestTool(_LeadTool):
    tool_name = "team_user_decision_request"
    description = "将 Lead 无法确定的业务判断提交为待用户处理的请求。"
    properties = {
        "request_id": field("用户决策请求标识。"),
        "question": field("需要用户判断的问题。"),
        "options": field("可选决策项。", "array", items={"type": "string"}),
        "context_summary": field("给用户展示的安全上下文摘要。"),
        "batch_id": field("关联批次标识，可选。"),
        "task_id": field("关联任务标识，可选。"),
    }
    required = ("request_id", "question", "context_summary")

    async def _execute(self, arguments):
        request_id = _required(arguments, "request_id", self.tool_name)
        question = _required(arguments, "question", self.tool_name)
        context_summary = _required(arguments, "context_summary", self.tool_name)
        options = arguments.get("options", [])
        if type(options) is not list or any(type(item) is not str or not item for item in options):
            return failure_result(self.tool_name, "invalid_argument", "options 必须是字符串列表", "options")
        team_name = self._service.team_name
        if type(team_name) is not str or not team_name:
            return failure_result(self.tool_name, "invalid_service", "服务未提供 team_name")
        request = TeamRequest(
            request_id=request_id,
            team_name=team_name,
            batch_id=arguments.get("batch_id"),
            task_id=arguments.get("task_id"),
            member_name="lead",
            kind=TeamRequestKind.USER_DECISION,
            question=question,
            options=tuple(options),
            context_summary=context_summary,
            state=TeamRequestState.PENDING,
            created_at=datetime.now(timezone.utc),
        )
        created = await maybe_await(self._service.create_request(request))
        return success_result(self.tool_name, _request_content(created))


def register_lead_team_tools(registry, service) -> None:
    tools = (
        TeamCreateTool(service),
        TeamAttachTool(service),
        TeamStatusTool(service),
        TeamArchiveTool(service),
        TeamBatchStartTool(service),
        TeamBatchIntegrateTool(service),
        TeamMemberSpawnTool(service),
        TeamMemberTerminateTool(service),
        TeamTaskCreateTool(service),
        TeamTaskListTool(service),
        TeamTaskGetTool(service),
        TeamTaskUpdateTool(service),
        TeamTaskDeleteTool(service),
        TeamTaskClaimTool(service),
        TeamTaskTransitionTool(service),
        TeamPlanDecideTool(service),
        TeamRequestListTool(service),
        TeamClarificationRespondTool(service),
        TeamToolApprovalRespondTool(service),
        TeamUserDecisionRequestTool(service),
        TeamMessageSendTool(service),
        TeamShutdownRequestTool(service),
    )
    for tool in tools:
        registry.register(tool)


def register_parent_team_tools(registry, service) -> None:
    register_lead_team_tools(registry, service)


__all__ = [
    "TeamCreateTool",
    "TeamAttachTool",
    "TeamStatusTool",
    "TeamArchiveTool",
    "TeamBatchStartTool",
    "TeamBatchIntegrateTool",
    "TeamMemberSpawnTool",
    "TeamMemberTerminateTool",
    "TeamTaskCreateTool",
    "TeamTaskListTool",
    "TeamTaskGetTool",
    "TeamTaskUpdateTool",
    "TeamTaskDeleteTool",
    "TeamTaskClaimTool",
    "TeamTaskTransitionTool",
    "TeamPlanDecideTool",
    "TeamRequestListTool",
    "TeamClarificationRespondTool",
    "TeamToolApprovalRespondTool",
    "TeamUserDecisionRequestTool",
    "TeamMessageSendTool",
    "TeamShutdownRequestTool",
    "register_lead_team_tools",
    "register_parent_team_tools",
]
