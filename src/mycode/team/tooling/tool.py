from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from mycode.team.domain.models import (
    ApprovalState,
    MemberBackend,
    MessageProtocol,
    TeamError,
    TaskKind,
    TaskPatch,
    TaskResult,
    TeamMessage,
    TeamTask,
    TeamTaskState,
)
from mycode.tool import ToolDefinition, ToolKind, ToolResult, ToolRuntimeScope, ToolWorkspaceScope

logger = logging.getLogger("mycode.team.tool")


def _context_text(**context: object) -> str:
    return " ".join(f"{key}={value}" for key, value in context.items() if value is not None)


class TeamTool:
    def __init__(self, *, service, name: str = "team", member_name: str | None = None) -> None:
        if name not in _TOOL_ACTIONS:
            raise ValueError("unknown team tool name")
        if member_name is not None and (type(member_name) is not str or not member_name):
            raise ValueError("member_name must be a non-empty string")
        self._service = service
        self._name = name
        self._member_name = member_name

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name=self._name,
            description=_tool_description(self._name),
            parameters=_build_parameters(self._name),
            kind=ToolKind.WRITE,
            requires_approval=False,
            runtime_scope=ToolRuntimeScope.PARENT_ONLY,
            workspace_scope=(
                ToolWorkspaceScope.WORKSPACE_AWARE
                if self._name == "team_member"
                else ToolWorkspaceScope.SHARED_ONLY
            ),
        )

    async def execute_async(self, arguments, context=None) -> ToolResult:
        if not isinstance(arguments, dict):
            return _failure(self._name, "invalid_team_arguments", "team arguments must be an object")
        action = arguments.get("action")
        if type(action) is not str or not action:
            return _failure(self._name, "missing_team_action", "team action is required")
        if action not in _TOOL_ACTIONS[self._name]:
            return _failure(self._name, "unknown_team_action", f"unknown team action: {action}")
        spec = _ACTION_SPECS[action]
        missing = set(spec["required"]) - set(arguments)
        if missing:
            return _failure(
                self._name,
                "missing_team_argument",
                f"missing required argument: {sorted(missing)[0]}",
            )
        unknown = set(arguments) - set(spec["allowed"])
        if unknown:
            return _failure(
                self._name,
                "unknown_team_argument",
                f"unknown team argument: {sorted(unknown)[0]}",
            )
        try:
            if action in {"create", "attach"}:
                snapshot = await self._service.create_or_attach(
                    _required_string(arguments, "team_name"),
                    goal=_optional_string(arguments.get("goal")),
                )
                return _success(
                    self._name,
                    {
                        "team_name": snapshot.team.team_name,
                        "state": snapshot.team.state.value,
                        "activated": True,
                    },
                )
            if action == "status":
                snapshot = await self._service.status()
                return _success(
                    self._name,
                    {
                        "team_name": snapshot.team.team_name,
                        "state": snapshot.team.state.value,
                        "member_count": len(snapshot.members),
                        "batch_count": len(snapshot.batches),
                    },
                )
            if action == "start_batch":
                batch = await self._service.start_batch(_required_string(arguments, "goal"))
                return _success(
                    self._name,
                    {
                        "batch_id": batch.batch_id,
                        "goal": batch.goal,
                        "state": batch.state.value,
                    },
                )
            if action == "spawn_member":
                member = await self._service.spawn_member(
                    member_name=_required_string(arguments, "member_name"),
                    role_name=_required_string(arguments, "role_name"),
                    role_revision=_required_int(arguments, "role_revision"),
                    requested_backend=MemberBackend(_required_string(arguments, "requested_backend")),
                    task_id=_required_string(arguments, "task_id"),
                    batch_id=_required_string(arguments, "batch_id"),
                    goal=_required_string(arguments, "goal"),
                    read_only=_required_bool(arguments, "read_only"),
                    approval_required=_required_bool(arguments, "approval_required"),
                )
                state = member.state.value
                return _success(
                    self._name,
                    {
                        "member_name": member.member_name,
                        "state": state,
                    },
                )
            if action == "terminate_member":
                member = await self._service.terminate_member(
                    _required_string(arguments, "member_name"),
                    force=_optional_bool(arguments, "force", False),
                )
                state = member.state.value
                return _success(self._name, {"member_name": member.member_name, "state": state})
            if action == "send_message":
                return await self._send_protocol_message(arguments, protocol=None)
            if action == "create_task":
                task = _make_task(arguments)
                created = self._service.create_task(task)
                return _success(self._name, _task_content(created))
            if action == "list_tasks":
                tasks = self._service.list_tasks(_optional_string(arguments.get("batch_id")))
                return _success(self._name, {"tasks": [_task_content(task) for task in tasks]})
            if action == "get_task":
                task = self._service.get_task(_required_string(arguments, "task_id"))
                return _success(self._name, _task_content(task))
            if action == "update_task":
                if self._name == "team_member":
                    self._resolve_member_name_argument(arguments)
                    self._ensure_member_task_owner(arguments)
                task = self._service.update_task(
                    _required_string(arguments, "task_id"),
                    _required_int(arguments, "expected_revision"),
                    _make_task_patch(arguments),
                )
                return _success(self._name, _task_content(task))
            if action == "delete_task":
                self._service.delete_task(
                    _required_string(arguments, "task_id"),
                    _required_int(arguments, "expected_revision"),
                )
                return _success(self._name, {"task_id": _required_string(arguments, "task_id"), "deleted": True})
            if action == "claim_task":
                member_name = self._resolve_member_name_argument(arguments)
                task = self._service.claim_task(
                    _required_string(arguments, "task_id"),
                    member_name,
                    _required_int(arguments, "expected_revision"),
                )
                return _success(self._name, _task_content(task))
            if action == "transition_task":
                if self._name == "team_member":
                    self._ensure_member_task_owner(arguments)
                    self._ensure_member_can_run(arguments)
                task = self._service.transition_task(
                    _required_string(arguments, "task_id"),
                    _required_int(arguments, "expected_revision"),
                    TeamTaskState(_required_string(arguments, "state")),
                    _make_task_result(arguments),
                    _optional_string(arguments.get("error")) or _optional_string(arguments.get("reason")),
                )
                return _success(self._name, _task_content(task))
            if action == "plan_submit":
                if self._name == "team_member":
                    self._resolve_member_name_argument(arguments)
                    self._ensure_member_task_owner(arguments)
                task = self._service.update_task(
                    _required_string(arguments, "task_id"),
                    _required_int(arguments, "expected_revision"),
                    TaskPatch(
                        plan_revision=_required_int(arguments, "plan_revision"),
                        approval_state=ApprovalState.PENDING,
                    ),
                )
                task = self._service.transition_task(
                    task.task_id,
                    task.revision,
                    TeamTaskState.AWAITING_APPROVAL,
                )
                receipt = await self._send_protocol_message(arguments, protocol=MessageProtocol.PLAN_SUBMIT)
                content = _task_content(task)
                content["message_id"] = receipt.content["message_id"]
                return _success(self._name, content)
            if action == "plan_decision":
                approved = _required_bool(arguments, "approved")
                plan_revision = _required_int(arguments, "plan_revision")
                if not approved and _optional_string(arguments.get("reason")) is None:
                    raise ValueError("reason must be present when rejecting a plan")
                task = self._service.get_task(_required_string(arguments, "task_id"))
                if task.plan_revision != plan_revision:
                    raise TeamError(
                        code="plan_revision_mismatch",
                        phase="team",
                        message="plan revision does not match the current task revision",
                        task_id=task.task_id,
                    )
                task = self._service.update_task(
                    _required_string(arguments, "task_id"),
                    _required_int(arguments, "expected_revision"),
                    TaskPatch(
                        approval_state=ApprovalState.APPROVED if approved else ApprovalState.REJECTED,
                    ),
                )
                if approved:
                    task = self._service.transition_task(
                        task.task_id,
                        task.revision,
                        TeamTaskState.RUNNING,
                    )
                content = _task_content(task)
                if "message_id" in arguments:
                    receipt = await self._send_protocol_message(arguments, protocol=MessageProtocol.PLAN_DECISION)
                    content["message_id"] = receipt.content["message_id"]
                return _success(self._name, content)
            if action == "status_update":
                return await self._send_protocol_message(arguments, protocol=MessageProtocol.STATUS_UPDATE)
            if action == "shutdown_request":
                return await self._send_protocol_message(arguments, protocol=MessageProtocol.SHUTDOWN_REQUEST)
            if action == "shutdown_response":
                return await self._send_protocol_message(arguments, protocol=MessageProtocol.SHUTDOWN_RESPONSE)
            if action == "integrate":
                report = await self._service.integrate_batch(_required_string(arguments, "batch_id"))
                return _success(
                    self._name,
                    {
                        "batch_id": report.batch_id,
                        "state": report.state.value,
                        "result_commit_id": report.result_commit_id,
                        "conflict_task_id": report.conflict_task_id,
                        "integrated_member_names": list(report.integrated_member_names),
                    },
                )
            if action == "archive":
                team = await self._service.archive()
                return _success(
                    self._name,
                    {
                        "team_name": team.team_name,
                        "state": team.state.value,
                    },
                )
        except Exception as exc:
            logger.exception(
                _context_text(
                    tool_name=self._name,
                    action=action,
                    team_name=arguments.get("team_name"),
                    member_name=arguments.get("member_name") or self._member_name,
                    batch_id=arguments.get("batch_id"),
                    task_id=arguments.get("task_id"),
                    message_id=arguments.get("message_id"),
                )
            )
            code = exc.code if isinstance(exc, TeamError) else "team_action_failed"
            return _failure(self._name, code, str(exc))
        return _failure(self._name, "unknown_team_action", f"unknown team action: {action}")

    async def _send_protocol_message(
        self,
        arguments: dict[str, object],
        *,
        protocol: MessageProtocol | None,
    ) -> ToolResult:
        if protocol is None:
            broadcast = _optional_bool(arguments, "broadcast", False)
            protocol = MessageProtocol.BROADCAST if broadcast else MessageProtocol.MESSAGE
        else:
            supplied_broadcast = _optional_bool(arguments, "broadcast", False)
            broadcast = protocol is MessageProtocol.BROADCAST or supplied_broadcast
        target_name = None if broadcast else _optional_string(arguments.get("target_name"))
        if target_name is None and not broadcast:
            target_name = "lead" if self._name == "team_member" else _required_string(arguments, "target_name")
        sender = self._resolve_sender(arguments)
        body = _required_string(arguments, "body")
        message = TeamMessage(
            message_id=_required_string(arguments, "message_id"),
            protocol=protocol,
            sender=sender,
            target_name=target_name,
            broadcast=broadcast,
            body=body,
            summary=_optional_string(arguments.get("summary")) or body,
            timestamp=datetime.now(timezone.utc),
            task_id=_optional_string(arguments.get("task_id")),
            batch_id=_optional_string(arguments.get("batch_id")),
        )
        receipt = await self._service.send_message(message)
        return _success(
            self._name,
            {
                "message_id": receipt.message_id,
                "recipient_names": list(receipt.recipient_names),
                "fanout_count": receipt.fanout_count,
            },
        )

    def _resolve_member_name_argument(self, arguments: dict[str, object]) -> str:
        supplied = _optional_string(arguments.get("member_name")) if "member_name" in arguments else None
        if self._name == "team_member":
            if self._member_name is None:
                raise ValueError("member_name must be bound for team_member")
            if supplied is not None and supplied != self._member_name:
                raise ValueError("member_name must match bound team member")
            return self._member_name
        if supplied is not None:
            return supplied
        if self._member_name is not None:
            return self._member_name
        return _required_string(arguments, "member_name")

    def _ensure_member_task_owner(self, arguments: dict[str, object]):
        if self._name != "team_member" or self._member_name is None:
            return None
        task = self._service.get_task(_required_string(arguments, "task_id"))
        if task.owner != self._member_name:
            raise TeamError(
                code="task_owner_mismatch",
                phase="team",
                message="task owner does not match bound team member",
                task_id=task.task_id,
                member_name=self._member_name,
            )
        return task

    def _ensure_member_can_run(self, arguments: dict[str, object]) -> None:
        if self._name != "team_member" or self._member_name is None:
            return
        state = _required_string(arguments, "state")
        if state != TeamTaskState.RUNNING.value:
            return
        task = self._service.get_task(_required_string(arguments, "task_id"))
        if task.state is TeamTaskState.BLOCKED:
            raise TeamError(
                code="blocked_recovery_requires_lead",
                phase="team",
                message="only the team lead can recover a blocked task",
                task_id=task.task_id,
                member_name=self._member_name,
            )
        if self._service.member_requires_approval(
            self._member_name,
            _required_string(arguments, "task_id"),
        ):
            if task.approval_state is not ApprovalState.APPROVED:
                raise TeamError(
                    code="approval_required",
                    phase="team",
                    message="approval is required before running this task",
                    task_id=task.task_id,
                    member_name=self._member_name,
                )

    def _resolve_sender(self, arguments: dict[str, object]) -> str:
        supplied = _optional_string(arguments.get("sender")) if "sender" in arguments else None
        if self._name == "team_member":
            if self._member_name is None:
                raise ValueError("sender requires a bound team member")
            if supplied is not None and supplied != self._member_name:
                raise ValueError("sender must match bound team member")
            return self._member_name
        return supplied or self._member_name or "lead"


_TOOL_DESCRIPTIONS = {
    "team": "创建、接管、查看和协调持久化本地团队。",
    "team_lead": "编排团队批次、成员、任务、消息、审批和本地集成。",
    "team_member": "领取团队任务、提交计划、报告状态并交换团队消息。",
}


_TOOL_ACTIONS = {
    "team": frozenset(
        {
            "create",
            "attach",
            "status",
            "start_batch",
            "spawn_member",
            "send_message",
            "archive",
        }
    ),
    "team_lead": frozenset(
        {
            "status",
            "start_batch",
            "spawn_member",
            "terminate_member",
            "send_message",
            "create_task",
            "list_tasks",
            "get_task",
            "update_task",
            "delete_task",
            "claim_task",
            "transition_task",
            "plan_decision",
            "shutdown_request",
            "integrate",
            "archive",
        }
    ),
    "team_member": frozenset(
        {
            "send_message",
            "create_task",
            "list_tasks",
            "get_task",
            "update_task",
            "claim_task",
            "transition_task",
            "plan_submit",
            "status_update",
            "shutdown_response",
        }
    ),
}


_FIELD_SPECS = {
    "action": {"type": "string", "description": "要执行的团队动作，必须选择当前工具支持的动作。"},
    "team_name": {"type": "string", "description": "团队名称，创建或接管团队时使用，必须是非空字符串。"},
    "goal": {"type": "string", "description": "团队或批次的总体目标，启动批次时必填。"},
    "member_name": {"type": "string", "description": "团队成员名称；启动、终止或指定任务负责人时使用。"},
    "role_name": {"type": "string", "description": "成员使用的角色名称，启动成员时必填。"},
    "role_revision": {"type": "integer", "description": "角色定义版本号，必须使用最近读取到的版本。"},
    "requested_backend": {
        "type": "string",
        "enum": ["auto", "tmux", "terminal", "in_process"],
        "description": "成员启动方式；auto 表示自动选择可用后端。",
    },
    "task_id": {"type": "string", "description": "任务标识；操作已有任务时使用，必须是非空字符串。"},
    "batch_id": {"type": "string", "description": "批次标识；将任务或成员关联到批次时使用。"},
    "read_only": {"type": "boolean", "description": "成员是否只能读取工作区；启动成员时必填。"},
    "approval_required": {"type": "boolean", "description": "成员执行写入操作前是否需要审批；启动成员时必填。"},
    "force": {"type": "boolean", "description": "终止成员时是否强制执行，默认为 false。"},
    "message_id": {"type": "string", "description": "消息唯一标识，发送团队消息时必填。"},
    "target_name": {"type": "string", "description": "消息接收成员名称；广播消息时不使用，成员工具默认发送给 lead。"},
    "body": {"type": "string", "description": "消息正文或计划内容，发送消息时必填。"},
    "summary": {"type": "string", "description": "消息或任务结果摘要，可选；未提供时使用正文。"},
    "broadcast": {"type": "boolean", "description": "是否广播给所有成员；为 true 时不需要 target_name。"},
    "sender": {"type": "string", "description": "消息发送者名称；成员工具必须与绑定成员一致。"},
    "title": {"type": "string", "description": "任务标题，创建任务时必填。"},
    "description": {"type": "string", "description": "任务详细说明，创建任务时必填。"},
    "dependency_ids": {
        "type": "array",
        "items": {"type": "string"},
        "description": "任务依赖的任务标识列表，可为空。",
    },
    "kind": {
        "type": "string",
        "enum": ["code", "read_only"],
        "description": "任务类型：code 表示编码任务，read_only 表示只读分析任务。",
    },
    "expected_revision": {"type": "integer", "description": "并发更新保护版本号，必须使用最近一次读取到的 revision。"},
    "state": {
        "type": "string",
        "enum": ["pending", "claimed", "awaiting_approval", "running", "blocked", "completed", "failed", "cancelled"],
        "description": "任务目标状态，必须使用列出的状态值。",
    },
    "approved": {"type": "boolean", "description": "是否批准计划；拒绝时必须同时提供 reason。"},
    "reason": {"type": "string", "description": "拒绝计划或说明任务原因；拒绝计划时必填。"},
    "error": {"type": "string", "description": "任务失败或阻塞时的错误信息，可选。"},
    "plan_revision": {"type": "integer", "description": "计划版本号，必须与当前任务计划版本匹配。"},
    "commit_id": {"type": "string", "description": "任务结果对应的提交标识，可选。"},
    "verification_summary": {"type": "string", "description": "任务结果的验证摘要，可选。"},
    "details": {"type": "string", "description": "任务结果的补充详情，可选。"},
}


def _action_spec(action: str, description: str, required: tuple[str, ...], allowed: tuple[str, ...]) -> dict[str, object]:
    fields = ("action",) + tuple(name for name in allowed if name != "action")
    properties = {name: dict(_FIELD_SPECS[name]) for name in fields}
    properties["action"]["enum"] = [action]
    return {"description": description, "required": required, "allowed": frozenset(allowed), "properties": properties}


_ACTION_SPECS = {
    "create": _action_spec("create", "创建或连接一个团队。", ("action", "team_name"), ("action", "team_name", "goal")),
    "attach": _action_spec("attach", "接管一个已有团队。", ("action", "team_name"), ("action", "team_name", "goal")),
    "status": _action_spec("status", "查看团队当前状态。", ("action",), ("action",)),
    "start_batch": _action_spec("start_batch", "启动一个团队批次。", ("action", "goal"), ("action", "goal")),
    "spawn_member": _action_spec(
        "spawn_member",
        "启动一个团队成员。",
        ("action", "member_name", "role_name", "role_revision", "requested_backend", "task_id", "batch_id", "goal", "read_only", "approval_required"),
        ("action", "member_name", "role_name", "role_revision", "requested_backend", "task_id", "batch_id", "goal", "read_only", "approval_required"),
    ),
    "terminate_member": _action_spec("terminate_member", "终止一个团队成员。", ("action", "member_name"), ("action", "member_name", "force")),
    "send_message": _action_spec("send_message", "向团队成员发送消息。", ("action", "message_id", "body"), ("action", "message_id", "target_name", "task_id", "batch_id", "body", "summary", "broadcast", "sender")),
    "create_task": _action_spec("create_task", "创建一个团队任务。", ("action", "task_id", "batch_id", "title", "description", "kind"), ("action", "task_id", "batch_id", "title", "description", "dependency_ids", "kind")),
    "list_tasks": _action_spec("list_tasks", "列出团队任务。", ("action",), ("action", "batch_id")),
    "get_task": _action_spec("get_task", "读取一个团队任务。", ("action", "task_id"), ("action", "task_id")),
    "update_task": _action_spec("update_task", "更新团队任务。", ("action", "task_id", "expected_revision"), ("action", "task_id", "expected_revision", "title", "description", "dependency_ids", "kind", "member_name", "plan_revision")),
    "delete_task": _action_spec("delete_task", "删除一个团队任务。", ("action", "task_id", "expected_revision"), ("action", "task_id", "expected_revision")),
    "claim_task": _action_spec("claim_task", "领取一个团队任务。", ("action", "task_id", "expected_revision"), ("action", "task_id", "member_name", "expected_revision")),
    "transition_task": _action_spec("transition_task", "转换团队任务状态。", ("action", "task_id", "expected_revision", "state"), ("action", "task_id", "expected_revision", "state", "summary", "commit_id", "verification_summary", "details", "reason", "error")),
    "plan_submit": _action_spec("plan_submit", "提交任务计划并请求审批。", ("action", "message_id", "task_id", "expected_revision", "plan_revision", "body"), ("action", "message_id", "task_id", "batch_id", "target_name", "expected_revision", "plan_revision", "body", "summary", "sender")),
    "plan_decision": _action_spec("plan_decision", "批准或拒绝任务计划。", ("action", "task_id", "expected_revision", "plan_revision", "approved"), ("action", "message_id", "target_name", "task_id", "batch_id", "expected_revision", "plan_revision", "approved", "reason", "body", "summary", "sender")),
    "status_update": _action_spec("status_update", "发送成员状态更新。", ("action", "message_id", "body"), ("action", "message_id", "target_name", "task_id", "batch_id", "body", "summary", "sender")),
    "shutdown_request": _action_spec("shutdown_request", "请求成员完成后停止。", ("action", "message_id", "body"), ("action", "message_id", "target_name", "body", "summary", "sender")),
    "shutdown_response": _action_spec("shutdown_response", "响应成员停止请求。", ("action", "message_id", "body"), ("action", "message_id", "target_name", "body", "summary", "sender")),
    "integrate": _action_spec("integrate", "集成一个已完成批次。", ("action", "batch_id"), ("action", "batch_id")),
    "archive": _action_spec("archive", "归档当前团队。", ("action",), ("action",)),
}

# 兼容旧的内部引用；允许字段集合始终从动作规格派生。
_ACTION_ARGUMENTS = {action: spec["allowed"] for action, spec in _ACTION_SPECS.items()}


def _build_parameters(tool_name: str) -> dict[str, object]:
    actions = sorted(_TOOL_ACTIONS[tool_name])
    properties = {name: dict(spec) for name, spec in _FIELD_SPECS.items()}
    properties["action"]["enum"] = actions
    return {
        "type": "object",
        "description": "必须提供 action。不同 action 使用不同参数；请只提供对应动作允许的字段。",
        "properties": properties,
        "required": ["action"],
        "additionalProperties": False,
    }


def _tool_description(tool_name: str) -> str:
    action_lines = "；".join(
        f"{action}：{_ACTION_SPECS[action]['description']}必填 {', '.join(_ACTION_SPECS[action]['required'][1:]) or '无额外参数'}"
        for action in sorted(_TOOL_ACTIONS[tool_name])
    )
    return f"{_TOOL_DESCRIPTIONS[tool_name]}必须提供 action。不同 action 使用不同参数：{action_lines}"


def _success(tool_name: str, content: dict[str, Any]) -> ToolResult:
    return ToolResult(ok=True, tool_name=tool_name, content=content)


def _failure(tool_name: str, reason_code: str, message: str) -> ToolResult:
    return ToolResult(
        ok=False,
        tool_name=tool_name,
        content={"reason_code": reason_code, "message": message},
        error=message,
    )


def _required_string(arguments: dict[str, object], name: str) -> str:
    value = arguments.get(name)
    if type(value) is not str or not value:
        raise ValueError(f"{name} must be a non-empty string")
    return value


def _optional_string(value: object) -> str | None:
    if value is None:
        return None
    if type(value) is not str or not value:
        raise ValueError("optional string must be a non-empty string")
    return value


def _required_int(arguments: dict[str, object], name: str) -> int:
    value = arguments.get(name)
    if type(value) is not int or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")
    return value


def _required_bool(arguments: dict[str, object], name: str) -> bool:
    value = arguments.get(name)
    if type(value) is not bool:
        raise ValueError(f"{name} must be a bool")
    return value


def _optional_bool(arguments: dict[str, object], name: str, default: bool) -> bool:
    value = arguments.get(name, default)
    if type(value) is not bool:
        raise ValueError(f"{name} must be a bool")
    return value


def _optional_string_tuple(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list | tuple):
        raise ValueError("dependency_ids must be a list of strings")
    result = []
    for item in value:
        if type(item) is not str or not item:
            raise ValueError("dependency_ids must contain non-empty strings")
        result.append(item)
    return tuple(result)


def _make_task(arguments: dict[str, object]) -> TeamTask:
    return TeamTask(
        task_id=_required_string(arguments, "task_id"),
        batch_id=_required_string(arguments, "batch_id"),
        title=_required_string(arguments, "title"),
        description=_required_string(arguments, "description"),
        dependency_ids=_optional_string_tuple(arguments.get("dependency_ids")),
        kind=TaskKind(_required_string(arguments, "kind")),
    )


def _make_task_patch(arguments: dict[str, object]) -> TaskPatch:
    owner = _optional_string(arguments.get("member_name"))
    return TaskPatch(
        title=_optional_string(arguments.get("title")),
        description=_optional_string(arguments.get("description")),
        dependency_ids=(
            _optional_string_tuple(arguments.get("dependency_ids"))
            if "dependency_ids" in arguments
            else None
        ),
        kind=TaskKind(_required_string(arguments, "kind")) if "kind" in arguments else None,
        owner=owner,
        plan_revision=_required_int(arguments, "plan_revision") if "plan_revision" in arguments else None,
    )


def _make_task_result(arguments: dict[str, object]) -> TaskResult | None:
    if not any(
        key in arguments
        for key in {"summary", "commit_id", "verification_summary", "details"}
    ):
        return None
    return TaskResult(
        summary=_optional_string(arguments.get("summary")) or "completed",
        commit_id=_optional_string(arguments.get("commit_id")),
        verification_summary=_optional_string(arguments.get("verification_summary")),
        details=_optional_string(arguments.get("details")),
    )


def _task_content(task: TeamTask) -> dict[str, object]:
    content: dict[str, object] = {
        "task_id": task.task_id,
        "batch_id": task.batch_id,
        "title": task.title,
        "state": task.state.value,
        "kind": task.kind.value,
        "owner": task.owner,
        "revision": task.revision,
        "plan_revision": task.plan_revision,
        "approval_state": task.approval_state.value,
        "dependency_ids": list(task.dependency_ids),
    }
    if task.result is not None:
        content["result"] = {
            "summary": task.result.summary,
            "commit_id": task.result.commit_id,
            "verification_summary": task.result.verification_summary,
        }
    if task.error is not None:
        content["error"] = task.error
    return content


__all__ = ["TeamTool"]
