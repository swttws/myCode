from __future__ import annotations

from datetime import datetime, timezone

from mycode.team.models import (
    ApprovalState,
    MessageProtocol,
    TaskPatch,
    TeamError,
    TeamMessage,
    TeamTaskState,
)
from mycode.team.tool_helpers import (
    error_result,
    failure_result,
    field,
    maybe_await,
    optional_bool,
    optional_string,
    required_bool,
    required_int,
    required_string,
    schema,
    success_result,
    task_content,
    validate_object_arguments,
    message_content,
)
from mycode.team.tools.task_tools import _service_method


_MESSAGE_FIELDS = {
    "message_id": field("消息唯一标识。"),
    "target_name": field("定向消息目标；广播时省略。"),
    "broadcast": field("是否广播，默认 false。", "boolean"),
    "body": field("消息正文。"),
    "summary": field("消息摘要，可选。"),
    "sender": field("发送者；Member 不能覆盖绑定身份。"),
    "task_id": field("关联任务标识，可选。"),
    "batch_id": field("关联批次标识，可选。"),
}


class _ProtocolTool:
    tool_name = ""
    description = ""
    properties: dict = {}
    required: tuple[str, ...] = ()
    member_only = False
    allow_member_binding = False

    def __init__(self, service, member_name: str | None = None) -> None:
        if self.member_only and (type(member_name) is not str or not member_name):
            raise ValueError("member_name must be a non-empty string")
        if (
            not self.member_only
            and not self.allow_member_binding
            and member_name is not None
        ):
            raise ValueError("lead-only tool cannot bind member_name")
        self._service = service
        self._member_name = member_name

    @property
    def definition(self):
        from mycode.tool import (
            ToolDefinition,
            ToolKind,
            ToolRuntimeScope,
            ToolWorkspaceScope,
        )

        return ToolDefinition(
            self.tool_name,
            self.description,
            schema(self.properties, self.required),
            ToolKind.WRITE,
            requires_approval=False,
            runtime_scope=ToolRuntimeScope.PARENT_ONLY,
            workspace_scope=ToolWorkspaceScope.WORKSPACE_AWARE
            if self.member_only
            else ToolWorkspaceScope.SHARED_ONLY,
        )

    async def execute_async(self, arguments, context=None):
        invalid = validate_object_arguments(arguments, self.properties, self.tool_name)
        if invalid:
            return invalid
        arguments = arguments or {}
        missing = next((name for name in self.required if name not in arguments), None)
        if missing:
            return failure_result(
                self.tool_name, "missing_argument", f"缺少必填参数：{missing}", missing
            )
        try:
            return await self._execute(arguments)
        except Exception as exc:
            return error_result(self.tool_name, exc)

    def _sender(self, arguments):
        supplied = arguments.get("sender")
        if self._member_name:
            if supplied is not None and supplied != self._member_name:
                raise TeamError(
                    code="member_identity_mismatch",
                    phase="message",
                    message="发送者必须与绑定成员一致",
                )
            return self._member_name
        return supplied or "lead"

    async def _send(self, arguments, protocol: MessageProtocol):
        broadcast = arguments.get("broadcast", False)
        if type(broadcast) is not bool:
            return failure_result(
                self.tool_name,
                "invalid_argument",
                "参数“broadcast”必须是布尔值",
                "broadcast",
            )
        target = arguments.get("target_name")
        if broadcast:
            target = None
        elif type(target) is not str or not target:
            if self._member_name:
                target = "lead"
            else:
                return failure_result(
                    self.tool_name,
                    "missing_argument",
                    "定向消息必须提供 target_name",
                    "target_name",
                )
        body = required_string(arguments, "body", self.tool_name)
        if not isinstance(body, str):
            return body
        summary = optional_string(arguments, "summary", self.tool_name)
        if not isinstance(summary, (str, type(None))):
            return summary
        message = TeamMessage(
            message_id=_required(arguments, "message_id", self.tool_name),
            protocol=protocol,
            sender=self._sender(arguments),
            target_name=target,
            broadcast=broadcast,
            body=body,
            summary=summary or body,
            timestamp=datetime.now(timezone.utc),
            task_id=arguments.get("task_id"),
            batch_id=arguments.get("batch_id"),
        )
        receipt = await maybe_await(self._service.send_message(message))
        return success_result(self.tool_name, message_content(receipt))


def _required(a, name, tool):
    value = required_string(a, name, tool)
    if not isinstance(value, str):
        raise ValueError(value.error or "参数错误")
    return value


class TeamMessageSendTool(_ProtocolTool):
    tool_name = "team_message_send"
    description = "向团队成员发送定向或广播消息。"
    properties = _MESSAGE_FIELDS
    required = ("message_id", "body")
    allow_member_binding = True

    async def _execute(self, a):
        return await self._send(
            a,
            MessageProtocol.BROADCAST
            if a.get("broadcast", False)
            else MessageProtocol.MESSAGE,
        )


class TeamStatusUpdateTool(_ProtocolTool):
    tool_name = "team_status_update"
    description = "发送绑定成员的状态更新。"
    properties = _MESSAGE_FIELDS
    required = ("message_id", "body")
    member_only = True

    async def _execute(self, a):
        return await self._send(a, MessageProtocol.STATUS_UPDATE)


class TeamShutdownRequestTool(_ProtocolTool):
    tool_name = "team_shutdown_request"
    description = "请求团队成员保存检查点并停止。"
    properties = _MESSAGE_FIELDS
    required = ("message_id", "body")

    async def _execute(self, a):
        return await self._send(a, MessageProtocol.SHUTDOWN_REQUEST)


class TeamShutdownResponseTool(_ProtocolTool):
    tool_name = "team_shutdown_response"
    description = "发送绑定成员的关停响应。"
    properties = _MESSAGE_FIELDS
    required = ("message_id", "body")
    member_only = True

    async def _execute(self, a):
        return await self._send(a, MessageProtocol.SHUTDOWN_RESPONSE)


class TeamPlanSubmitTool(_ProtocolTool):
    tool_name = "team_plan_submit"
    description = "提交任务计划并请求 Lead 审批。"
    member_only = True
    properties = {
        "message_id": field("计划消息标识。"),
        "task_id": field("任务标识。"),
        "batch_id": field("批次标识，可选。"),
        "expected_revision": field("任务版本号。", "integer"),
        "plan_revision": field("计划版本号。", "integer"),
        "body": field("计划正文。"),
        "summary": field("计划摘要，可选。"),
        "target_name": field("Lead 名称，可选。"),
        "sender": field("发送者，不能覆盖绑定成员。"),
    }
    required = ("message_id", "task_id", "expected_revision", "plan_revision", "body")

    async def _execute(self, a):
        task_id = _required(a, "task_id", self.tool_name)
        rev = required_int(a, "expected_revision", self.tool_name)
        plan_rev = required_int(a, "plan_revision", self.tool_name)
        if not isinstance(rev, int):
            return rev
        if not isinstance(plan_rev, int):
            return plan_rev
        current = await maybe_await(_service_method(self._service, "get_task")(task_id))
        if getattr(current, "owner", None) != self._member_name:
            raise TeamError(
                code="task_owner_mismatch",
                phase="plan",
                message="任务所有者与绑定成员不匹配",
            )
        updated = await maybe_await(
            _service_method(self._service, "update_task")(
                task_id,
                rev,
                TaskPatch(plan_revision=plan_rev, approval_state=ApprovalState.PENDING),
            )
        )
        awaiting = await maybe_await(
            _service_method(self._service, "transition_task")(
                task_id, updated.revision, TeamTaskState.AWAITING_APPROVAL
            )
        )
        sent = await self._send(a, MessageProtocol.PLAN_SUBMIT)
        if not sent.ok:
            return sent
        content = task_content(awaiting)
        content["message_id"] = sent.content["message_id"]
        return success_result(self.tool_name, content)


class TeamPlanDecideTool(_ProtocolTool):
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

    async def _execute(self, a):
        task_id = _required(a, "task_id", self.tool_name)
        rev = required_int(a, "expected_revision", self.tool_name)
        plan_rev = required_int(a, "plan_revision", self.tool_name)
        approved = required_bool(a, "approved", self.tool_name)
        if not isinstance(rev, int):
            return rev
        if not isinstance(plan_rev, int):
            return plan_rev
        if not isinstance(approved, bool):
            return approved
        current = await maybe_await(_service_method(self._service, "get_task")(task_id))
        if current.plan_revision != plan_rev:
            raise TeamError(
                code="plan_revision_mismatch",
                phase="plan",
                message="计划版本与当前任务不匹配",
            )
        if not approved and (type(a.get("reason")) is not str or not a.get("reason")):
            return failure_result(
                self.tool_name,
                "missing_argument",
                "拒绝计划时必须提供 reason",
                "reason",
            )
        updated = await maybe_await(
            _service_method(self._service, "update_task")(
                task_id,
                rev,
                TaskPatch(
                    approval_state=ApprovalState.APPROVED
                    if approved
                    else ApprovalState.REJECTED
                ),
            )
        )
        if approved:
            updated = await maybe_await(
                _service_method(self._service, "transition_task")(
                    task_id, updated.revision, TeamTaskState.RUNNING
                )
            )
        content = task_content(updated)
        if "message_id" in a:
            sent = await self._send(a, MessageProtocol.PLAN_DECISION)
            if not sent.ok:
                return sent
            content["message_id"] = sent.content["message_id"]
        return success_result(self.tool_name, content)


__all__ = [
    "TeamPlanSubmitTool",
    "TeamPlanDecideTool",
    "TeamMessageSendTool",
    "TeamStatusUpdateTool",
    "TeamShutdownRequestTool",
    "TeamShutdownResponseTool",
]
