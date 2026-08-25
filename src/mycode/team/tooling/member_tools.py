from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from mycode.team.domain.models import (
    MemberState,
    MessageProtocol,
    TaskKind,
    TaskPatch,
    TaskResult,
    TeamMessage,
    TeamTask,
    TeamTaskState,
    TeamError,
    ApprovalState,
)
from mycode.team.tooling.tool_helpers import (
    enum_value,
    error_result,
    field,
    failure_result,
    maybe_await,
    message_content,
    optional_bool,
    optional_string,
    required_int,
    required_string,
    schema,
    success_result,
    task_content,
    validate_object_arguments,
)
from mycode.team.infrastructure.requests import TeamRequest, TeamRequestKind, TeamRequestState
from mycode.tool import ToolDefinition, ToolKind, ToolRuntimeScope, ToolWorkspaceScope

logger = logging.getLogger("mycode.team.member_tools")


def _context_text(**context: object) -> str:
    return " ".join(f"{key}={value}" for key, value in context.items() if value is not None)


class _TaskTool:
    tool_name = ""
    description = ""
    properties: dict = {}
    required: tuple[str, ...] = ()
    kind = ToolKind.WRITE

    def __init__(self, service, member_name: str | None = None) -> None:
        if member_name is not None and (
            type(member_name) is not str or not member_name
        ):
            raise ValueError("member_name must be a non-empty string")
        self._service = service
        self._member_name = member_name

    @property
    def definition(self):
        return ToolDefinition(
            self.tool_name,
            self.description,
            schema(self.properties, self.required),
            self.kind,
            requires_approval=False,
            runtime_scope=ToolRuntimeScope.PARENT_ONLY,
            workspace_scope=ToolWorkspaceScope.WORKSPACE_AWARE
            if self._member_name
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
            logger.exception(
                _context_text(
                    tool_name=self.tool_name,
                    member_name=self._member_name,
                    task_id=arguments.get("task_id"),
                    batch_id=arguments.get("batch_id"),
                )
            )
            return error_result(self.tool_name, exc)

    def _bound_member(
        self, arguments, *, required: bool = False
    ) -> str | None | object:
        supplied = arguments.get("member_name")
        if self._member_name:
            if supplied is not None and supplied != self._member_name:
                return failure_result(
                    self.tool_name,
                    "member_identity_mismatch",
                    "成员身份必须与运行时绑定身份一致",
                    "member_name",
                )
            return self._member_name
        if required and (type(supplied) is not str or not supplied):
            return failure_result(
                self.tool_name,
                "missing_argument",
                "缺少必填参数：member_name",
                "member_name",
            )
        return supplied

    def _owner_check(self, arguments):
        if not self._member_name:
            return None
        task = self._service.get_task(
            _required(arguments, "task_id", self.tool_name)
        )
        if task.owner != self._member_name:
            return TeamError(
                code="task_owner_mismatch",
                phase="team",
                message="任务所有者与绑定成员不匹配",
                task_id=task.task_id,
                member_name=self._member_name,
            )
        return task


def _required(a, name, tool):
    value = required_string(a, name, tool)
    if not isinstance(value, str):
        raise ValueError(value.error or "参数错误")
    return value


_TASK_CREATE_FIELDS = {
    "task_id": field("任务标识。"),
    "batch_id": field("所属批次标识。"),
    "title": field("任务标题。"),
    "description": field("任务说明。"),
    "dependency_ids": field("依赖任务标识列表。", "array", items={"type": "string"}),
    "kind": field("任务类型。", enum=[x.value for x in TaskKind]),
}


class TeamTaskCreateTool(_TaskTool):
    tool_name = "team_task_create"
    description = "创建一个团队任务。"
    properties = _TASK_CREATE_FIELDS
    required = ("task_id", "batch_id", "title", "description", "kind")

    async def _execute(self, a):
        strings = {}
        for name in ("task_id", "batch_id", "title", "description"):
            value = required_string(a, name, self.tool_name)
            if not isinstance(value, str):
                return value
            strings[name] = value
        kind = enum_value(a, "kind", TaskKind, self.tool_name)
        if not isinstance(kind, TaskKind):
            return kind
        deps = a.get("dependency_ids", [])
        if type(deps) is not list or any(
            type(item) is not str or not item for item in deps
        ):
            return failure_result(
                self.tool_name,
                "invalid_argument",
                "参数“dependency_ids”必须是字符串列表",
                "dependency_ids",
            )
        task = TeamTask(
            **strings, dependency_ids=tuple(deps), kind=kind, owner=self._member_name
        )
        created = await maybe_await(self._service.create_task(task))
        return success_result(self.tool_name, task_content(created))


class TeamTaskListTool(_TaskTool):
    tool_name = "team_task_list"
    description = "列出团队任务。"
    properties = {"batch_id": field("按批次过滤，可选。")}
    kind = ToolKind.READ

    async def _execute(self, a):
        batch = optional_string(a, "batch_id", self.tool_name)
        if not isinstance(batch, (str, type(None))):
            return batch
        tasks = await maybe_await(self._service.list_tasks(batch))
        return success_result(
            self.tool_name, {"tasks": [task_content(task) for task in tasks]}
        )


class TeamTaskGetTool(_TaskTool):
    tool_name = "team_task_get"
    description = "读取一个团队任务。"
    properties = {"task_id": field("任务标识。")}
    required = ("task_id",)
    kind = ToolKind.READ

    async def _execute(self, a):
        return success_result(
            self.tool_name,
            task_content(
                await maybe_await(
                    self._service.get_task(
                        _required(a, "task_id", self.tool_name)
                    )
                )
            ),
        )


_PATCH_FIELDS = {
    "task_id": field("任务标识。"),
    "expected_revision": field("并发保护版本号。", "integer"),
    "title": field("新的任务标题。"),
    "description": field("新的任务说明。"),
    "dependency_ids": field("新的依赖列表。", "array", items={"type": "string"}),
    "kind": field("新的任务类型。", enum=[x.value for x in TaskKind]),
    "plan_revision": field("新的计划版本。", "integer"),
    "member_name": field("Lead 指定的成员名称。"),
}


class TeamTaskUpdateTool(_TaskTool):
    tool_name = "team_task_update"
    description = "更新团队任务。"
    properties = _PATCH_FIELDS
    required = ("task_id", "expected_revision")

    async def _execute(self, a):
        if self._member_name:
            owner = await maybe_await(self._owner_check(a))
            if isinstance(owner, Exception):
                raise owner
        rev = required_int(a, "expected_revision", self.tool_name)
        if not isinstance(rev, int):
            return rev
        patch_kwargs = {}
        for name in ("title", "description"):
            if name in a:
                value = required_string(a, name, self.tool_name)
                if not isinstance(value, str):
                    return value
                patch_kwargs[name] = value
        if "dependency_ids" in a:
            deps = a["dependency_ids"]
            if type(deps) is not list or any(type(x) is not str or not x for x in deps):
                return failure_result(
                    self.tool_name,
                    "invalid_argument",
                    "参数“dependency_ids”必须是字符串列表",
                    "dependency_ids",
                )
            patch_kwargs["dependency_ids"] = tuple(deps)
        if "kind" in a:
            kind = enum_value(a, "kind", TaskKind, self.tool_name)
            if not isinstance(kind, TaskKind):
                return kind
            patch_kwargs["kind"] = kind
        if "plan_revision" in a:
            plan = required_int(a, "plan_revision", self.tool_name)
            if not isinstance(plan, int):
                return plan
            patch_kwargs["plan_revision"] = plan
        patch = TaskPatch(**patch_kwargs)
        updated = await maybe_await(
            self._service.update_task(_required(a, "task_id", self.tool_name), rev, patch)
        )
        return success_result(self.tool_name, task_content(updated))


class TeamTaskDeleteTool(_TaskTool):
    tool_name = "team_task_delete"
    description = "删除尚未开始的团队任务。"
    properties = {
        "task_id": field("任务标识。"),
        "expected_revision": field("并发保护版本号。", "integer"),
    }
    required = ("task_id", "expected_revision")

    async def _execute(self, a):
        if self._member_name:
            owner = await maybe_await(self._owner_check(a))
            if isinstance(owner, Exception):
                raise owner
        rev = required_int(a, "expected_revision", self.tool_name)
        if not isinstance(rev, int):
            return rev
        task_id = _required(a, "task_id", self.tool_name)
        await maybe_await(self._service.delete_task(task_id, rev))
        return success_result(self.tool_name, {"task_id": task_id, "deleted": True})


class TeamTaskClaimTool(_TaskTool):
    tool_name = "team_task_claim"
    description = "领取一个可执行的团队任务。"
    properties = {
        "task_id": field("任务标识。"),
        "expected_revision": field("并发保护版本号。", "integer"),
        "member_name": field("Lead 指定的成员名称，可选。"),
    }
    required = ("task_id", "expected_revision")

    @property
    def definition(self):
        definition = super().definition
        required = (
            ["task_id", "expected_revision"]
            if self._member_name
            else ["task_id", "expected_revision", "member_name"]
        )
        return ToolDefinition(
            definition.name,
            definition.description,
            schema(self.properties, required),
            definition.kind,
            requires_approval=definition.requires_approval,
            runtime_scope=definition.runtime_scope,
            workspace_scope=definition.workspace_scope,
        )

    async def _execute(self, a):
        member = self._bound_member(a, required=not bool(self._member_name))
        if isinstance(member, Exception) or not isinstance(member, str):
            return member
        rev = required_int(a, "expected_revision", self.tool_name)
        if not isinstance(rev, int):
            return rev
        task = await maybe_await(
            self._service.claim_task(_required(a, "task_id", self.tool_name), member, rev)
        )
        return success_result(self.tool_name, task_content(task))


class TeamTaskTransitionTool(_TaskTool):
    tool_name = "team_task_transition"
    description = "转换团队任务状态并记录结果。"
    properties = {
        "task_id": field("任务标识。"),
        "expected_revision": field("并发保护版本号。", "integer"),
        "state": field("目标状态。", enum=[x.value for x in TeamTaskState]),
        "summary": field("完成结果摘要。"),
        "commit_id": field("结果提交标识。"),
        "verification_summary": field("验证摘要。"),
        "details": field("结果详情。"),
        "error": field("阻塞或失败原因。"),
    }
    required = ("task_id", "expected_revision", "state")

    async def _execute(self, a):
        if self._member_name:
            owner = await maybe_await(self._owner_check(a))
            if isinstance(owner, Exception):
                raise owner
            current = owner
            state = a.get("state")
            if state == TeamTaskState.RUNNING.value:
                if current.state is TeamTaskState.BLOCKED:
                    raise TeamError(
                        code="blocked_recovery_requires_lead",
                        phase="team",
                        message="只有 Lead 可以恢复 blocked 任务",
                    )
                if (
                    await maybe_await(self._service.member_requires_approval(self._member_name, current.task_id))
                    and current.approval_state is not ApprovalState.APPROVED
                ):
                    raise TeamError(
                        code="approval_required",
                        phase="team",
                        message="任务运行前需要审批",
                    )
        rev = required_int(a, "expected_revision", self.tool_name)
        if not isinstance(rev, int):
            return rev
        state = enum_value(a, "state", TeamTaskState, self.tool_name)
        if not isinstance(state, TeamTaskState):
            return state
        result = None
        if any(
            name in a
            for name in ("summary", "commit_id", "verification_summary", "details")
        ):
            summary = required_string(a, "summary", self.tool_name)
            if not isinstance(summary, str):
                return summary
            result = TaskResult(
                summary=summary,
                commit_id=a.get("commit_id"),
                verification_summary=a.get("verification_summary"),
                details=a.get("details"),
            )
        task = await maybe_await(
            self._service.transition_task(
                _required(a, "task_id", self.tool_name),
                rev,
                state,
                result,
                a.get("error"),
            )
        )
        return success_result(self.tool_name, task_content(task))


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
        if not self.member_only and not self.allow_member_binding and member_name is not None:
            raise ValueError("lead-only tool cannot bind member_name")
        self._service = service
        self._member_name = member_name

    @property
    def definition(self):
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
                self.tool_name,
                "missing_argument",
                f"缺少必填参数：{missing}",
                missing,
            )
        try:
            return await self._execute(arguments)
        except Exception as exc:
            logger.exception(
                _context_text(
                    tool_name=self.tool_name,
                    member_name=self._member_name,
                    message_id=arguments.get("message_id"),
                    task_id=arguments.get("task_id"),
                    batch_id=arguments.get("batch_id"),
                    target_name=arguments.get("target_name"),
                )
            )
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
        broadcast = optional_bool(arguments, "broadcast", False, self.tool_name)
        if not isinstance(broadcast, bool):
            return broadcast
        target = None if broadcast else arguments.get("target_name")
        if not broadcast and (type(target) is not str or not target):
            target = "lead" if self._member_name else None
        if target is None and not broadcast:
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
        receipt = await maybe_await(
            self._service.send_message(
                TeamMessage(
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
            )
        )
        return success_result(self.tool_name, message_content(receipt))


class TeamMessageSendTool(_ProtocolTool):
    tool_name = "team_message_send"
    description = "向团队成员发送定向或广播消息。"
    properties = _MESSAGE_FIELDS
    required = ("message_id", "body")
    allow_member_binding = True

    async def _execute(self, a):
        return await self._send(
            a,
            MessageProtocol.BROADCAST if a.get("broadcast", False) else MessageProtocol.MESSAGE,
        )


class TeamStatusUpdateTool(_ProtocolTool):
    tool_name = "team_status_update"
    description = "发送绑定成员的状态更新。"
    properties = _MESSAGE_FIELDS
    required = ("message_id", "body")
    member_only = True

    async def _execute(self, a):
        return await self._send(a, MessageProtocol.STATUS_UPDATE)


class TeamClarificationRequestTool(_ProtocolTool):
    """Persist a member question and pause its task until Lead responds."""

    tool_name = "team_clarification_request"
    description = "向 Lead 请求澄清并暂停当前任务。"
    member_only = True
    properties = {
        "message_id": field("澄清请求消息标识。"),
        "request_id": field("持久化请求标识。"),
        "task_id": field("关联任务标识。"),
        "batch_id": field("关联批次标识，可选。"),
        "expected_revision": field("任务当前版本号，可选。", "integer"),
        "question": field("需要 Lead 判断的问题。"),
        "options": field("可选决策项。", "array", items={"type": "string"}),
        "context_summary": field("提供给 Lead 的安全上下文摘要。"),
    }
    required = ("message_id", "request_id", "task_id", "question", "context_summary")

    async def _execute(self, a):
        task_id = _required(a, "task_id", self.tool_name)
        request_id = _required(a, "request_id", self.tool_name)
        question = _required(a, "question", self.tool_name)
        context_summary = _required(a, "context_summary", self.tool_name)
        current = await maybe_await(self._service.get_task(task_id))
        if current.owner != self._member_name:
            raise TeamError(
                code="task_owner_mismatch",
                phase="request",
                message="任务所有者与绑定成员不匹配",
                task_id=task_id,
                member_name=self._member_name,
            )
        options = a.get("options", [])
        if type(options) is not list or any(type(option) is not str or not option for option in options):
            return failure_result(
                self.tool_name,
                "invalid_argument",
                "参数 options 必须是非空字符串列表",
                "options",
            )
        batch_id = a.get("batch_id") or current.batch_id
        if type(batch_id) is not str or not batch_id:
            return failure_result(self.tool_name, "invalid_argument", "batch_id 无效", "batch_id")
        expected_revision = a.get("expected_revision", current.revision)
        if type(expected_revision) is not int:
            return failure_result(
                self.tool_name,
                "invalid_argument",
                "expected_revision 必须是整数",
                "expected_revision",
            )
        team_name = self._service.team_name
        if type(team_name) is not str or not team_name:
            return failure_result(self.tool_name, "invalid_service", "服务未提供 team_name")
        request = TeamRequest(
            request_id=request_id,
            team_name=team_name,
            batch_id=batch_id,
            task_id=task_id,
            member_name=self._member_name,
            kind=TeamRequestKind.CLARIFICATION,
            question=question,
            options=tuple(options),
            context_summary=context_summary,
            state=TeamRequestState.PENDING,
            created_at=datetime.now(timezone.utc),
        )
        await maybe_await(self._service.create_request(request))
        await maybe_await(
            self._service.transition_task(
                task_id,
                expected_revision,
                TeamTaskState.AWAITING_INPUT,
            )
        )
        await maybe_await(self._service.set_member_state(MemberState.AWAITING_INPUT))
        a = dict(a)
        a["task_id"] = task_id
        a["batch_id"] = batch_id
        a["body"] = json.dumps(
            {
                "request_id": request_id,
                "question": question,
                "options": options,
                "context_summary": context_summary,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
        a.setdefault("summary", question)
        sent = await self._send(a, MessageProtocol.CLARIFICATION_REQUEST)
        if not sent.ok:
            return sent
        content = dict(sent.content)
        content["request_id"] = request_id
        content["task_id"] = task_id
        return success_result(self.tool_name, content)


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
        current = await maybe_await(self._service.get_task(task_id))
        if current.owner != self._member_name:
            raise TeamError(
                code="task_owner_mismatch",
                phase="plan",
                message="任务所有者与绑定成员不匹配",
            )
        updated = await maybe_await(
            self._service.update_task(
                task_id,
                rev,
                TaskPatch(plan_revision=plan_rev, approval_state=ApprovalState.PENDING),
            )
        )
        awaiting = await maybe_await(
            self._service.transition_task(
                task_id,
                updated.revision,
                TeamTaskState.AWAITING_APPROVAL,
            )
        )
        sent = await self._send(a, MessageProtocol.PLAN_SUBMIT)
        if not sent.ok:
            return sent
        content = task_content(awaiting)
        content["message_id"] = sent.content["message_id"]
        return success_result(self.tool_name, content)


def register_member_team_tools(registry, service, *, member_name: str) -> None:
    tools = (
        TeamTaskCreateTool(service, member_name),
        TeamTaskListTool(service, member_name),
        TeamTaskGetTool(service, member_name),
        TeamTaskUpdateTool(service, member_name),
        TeamTaskDeleteTool(service, member_name),
        TeamTaskClaimTool(service, member_name),
        TeamTaskTransitionTool(service, member_name),
        TeamPlanSubmitTool(service, member_name),
        TeamMessageSendTool(service, member_name),
        TeamStatusUpdateTool(service, member_name),
        TeamClarificationRequestTool(service, member_name),
        TeamShutdownResponseTool(service, member_name),
    )
    for tool in tools:
        registry.register(tool)


__all__ = [
    "TeamTaskCreateTool",
    "TeamTaskListTool",
    "TeamTaskGetTool",
    "TeamTaskUpdateTool",
    "TeamTaskDeleteTool",
    "TeamTaskClaimTool",
    "TeamTaskTransitionTool",
    "TeamPlanSubmitTool",
    "TeamMessageSendTool",
    "TeamStatusUpdateTool",
    "TeamClarificationRequestTool",
    "TeamShutdownResponseTool",
    "register_member_team_tools",
]
