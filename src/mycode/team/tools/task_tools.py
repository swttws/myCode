from __future__ import annotations

from mycode.team.models import TaskKind, TaskPatch, TaskResult, TeamTask, TeamTaskState, TeamError, ApprovalState
from mycode.team.tool_helpers import (
    as_path_list, enum_value, error_result, field, failure_result, maybe_await, optional_string,
    required_int, required_string, schema, success_result, task_content, validate_object_arguments,
)
from mycode.tool import ToolDefinition, ToolKind, ToolRuntimeScope, ToolWorkspaceScope


def _service_method(service, name: str):
    method = getattr(service, name, None)
    if callable(method):
        return method
    board = service.task_board
    return getattr(board, {"create_task": "create", "list_tasks": "list", "get_task": "get", "update_task": "update", "delete_task": "delete", "claim_task": "claim", "transition_task": "transition"}[name])


class _TaskTool:
    tool_name = ""
    description = ""
    properties: dict = {}
    required: tuple[str, ...] = ()
    kind = ToolKind.WRITE

    def __init__(self, service, member_name: str | None = None) -> None:
        if member_name is not None and (type(member_name) is not str or not member_name):
            raise ValueError("member_name must be a non-empty string")
        self._service = service
        self._member_name = member_name

    @property
    def definition(self):
        return ToolDefinition(self.tool_name, self.description, schema(self.properties, self.required), self.kind, requires_approval=False, runtime_scope=ToolRuntimeScope.PARENT_ONLY, workspace_scope=ToolWorkspaceScope.WORKSPACE_AWARE if self._member_name else ToolWorkspaceScope.SHARED_ONLY)

    async def execute_async(self, arguments, context=None):
        invalid = validate_object_arguments(arguments, self.properties, self.tool_name)
        if invalid: return invalid
        arguments = arguments or {}
        missing = next((name for name in self.required if name not in arguments), None)
        if missing: return failure_result(self.tool_name, "missing_argument", f"缺少必填参数：{missing}", missing)
        try: return await self._execute(arguments)
        except Exception as exc: return error_result(self.tool_name, exc)

    def _bound_member(self, arguments, *, required: bool = False) -> str | None | object:
        supplied = arguments.get("member_name")
        if self._member_name:
            if supplied is not None and supplied != self._member_name:
                return failure_result(self.tool_name, "member_identity_mismatch", "成员身份必须与运行时绑定身份一致", "member_name")
            return self._member_name
        if required and (type(supplied) is not str or not supplied):
            return failure_result(self.tool_name, "missing_argument", "缺少必填参数：member_name", "member_name")
        return supplied

    def _owner_check(self, arguments):
        if not self._member_name: return None
        task = _service_method(self._service, "get_task")(_required(arguments, "task_id", self.tool_name))
        if getattr(task, "owner", None) != self._member_name:
            return TeamError(code="task_owner_mismatch", phase="team", message="任务所有者与绑定成员不匹配", task_id=task.task_id, member_name=self._member_name)
        return task


def _required(a, name, tool):
    value = required_string(a, name, tool)
    if not isinstance(value, str): raise ValueError(value.error or "参数错误")
    return value


_TASK_CREATE_FIELDS = {
    "task_id": field("任务标识。"), "batch_id": field("所属批次标识。"), "title": field("任务标题。"),
    "description": field("任务说明。"), "dependency_ids": field("依赖任务标识列表。", "array", items={"type": "string"}),
    "kind": field("任务类型。", enum=[x.value for x in TaskKind]),
}


class TeamTaskCreateTool(_TaskTool):
    tool_name = "team_task_create"; description = "创建一个团队任务。"; properties = _TASK_CREATE_FIELDS; required = ("task_id", "batch_id", "title", "description", "kind")
    async def _execute(self, a):
        strings = {}
        for name in ("task_id", "batch_id", "title", "description"):
            value = required_string(a, name, self.tool_name)
            if not isinstance(value, str): return value
            strings[name] = value
        kind = enum_value(a, "kind", TaskKind, self.tool_name)
        if not isinstance(kind, TaskKind): return kind
        deps = a.get("dependency_ids", [])
        if type(deps) is not list or any(type(item) is not str or not item for item in deps):
            return failure_result(self.tool_name, "invalid_argument", "参数“dependency_ids”必须是字符串列表", "dependency_ids")
        task = TeamTask(**strings, dependency_ids=tuple(deps), kind=kind, owner=self._member_name)
        created = await maybe_await(_service_method(self._service, "create_task")(task))
        return success_result(self.tool_name, task_content(created))


class TeamTaskListTool(_TaskTool):
    tool_name = "team_task_list"; description = "列出团队任务。"; properties = {"batch_id": field("按批次过滤，可选。")} ; kind = ToolKind.READ
    async def _execute(self, a):
        batch = optional_string(a, "batch_id", self.tool_name)
        if not isinstance(batch, (str, type(None))): return batch
        tasks = await maybe_await(_service_method(self._service, "list_tasks")(batch))
        return success_result(self.tool_name, {"tasks": [task_content(task) for task in tasks]})


class TeamTaskGetTool(_TaskTool):
    tool_name = "team_task_get"; description = "读取一个团队任务。"; properties = {"task_id": field("任务标识。")} ; required = ("task_id",); kind = ToolKind.READ
    async def _execute(self, a):
        return success_result(self.tool_name, task_content(await maybe_await(_service_method(self._service, "get_task")(_required(a, "task_id", self.tool_name)))))


_PATCH_FIELDS = {
    "task_id": field("任务标识。"), "expected_revision": field("并发保护版本号。", "integer"),
    "title": field("新的任务标题。"), "description": field("新的任务说明。"),
    "dependency_ids": field("新的依赖列表。", "array", items={"type": "string"}), "kind": field("新的任务类型。", enum=[x.value for x in TaskKind]),
    "plan_revision": field("新的计划版本。", "integer"), "member_name": field("Lead 指定的成员名称。"),
}


class TeamTaskUpdateTool(_TaskTool):
    tool_name = "team_task_update"; description = "更新团队任务。"; properties = _PATCH_FIELDS; required = ("task_id", "expected_revision")
    async def _execute(self, a):
        if self._member_name:
            owner = await maybe_await(self._owner_check(a))
            if isinstance(owner, Exception): raise owner
        rev = required_int(a, "expected_revision", self.tool_name)
        if not isinstance(rev, int): return rev
        patch_kwargs = {}
        for name in ("title", "description"):
            if name in a:
                value = required_string(a, name, self.tool_name)
                if not isinstance(value, str): return value
                patch_kwargs[name] = value
        if "dependency_ids" in a:
            deps = a["dependency_ids"]
            if type(deps) is not list or any(type(x) is not str or not x for x in deps): return failure_result(self.tool_name, "invalid_argument", "参数“dependency_ids”必须是字符串列表", "dependency_ids")
            patch_kwargs["dependency_ids"] = tuple(deps)
        if "kind" in a:
            kind = enum_value(a, "kind", TaskKind, self.tool_name)
            if not isinstance(kind, TaskKind): return kind
            patch_kwargs["kind"] = kind
        if "plan_revision" in a:
            plan = required_int(a, "plan_revision", self.tool_name)
            if not isinstance(plan, int): return plan
            patch_kwargs["plan_revision"] = plan
        patch = TaskPatch(**patch_kwargs)
        updated = await maybe_await(_service_method(self._service, "update_task")(_required(a, "task_id", self.tool_name), rev, patch))
        return success_result(self.tool_name, task_content(updated))


class TeamTaskDeleteTool(_TaskTool):
    tool_name = "team_task_delete"; description = "删除尚未开始的团队任务。"; properties = {"task_id": field("任务标识。"), "expected_revision": field("并发保护版本号。", "integer")}; required = ("task_id", "expected_revision")
    async def _execute(self, a):
        if self._member_name:
            owner = await maybe_await(self._owner_check(a))
            if isinstance(owner, Exception): raise owner
        rev = required_int(a, "expected_revision", self.tool_name)
        if not isinstance(rev, int): return rev
        task_id = _required(a, "task_id", self.tool_name)
        await maybe_await(_service_method(self._service, "delete_task")(task_id, rev))
        return success_result(self.tool_name, {"task_id": task_id, "deleted": True})


class TeamTaskClaimTool(_TaskTool):
    tool_name = "team_task_claim"; description = "领取一个可执行的团队任务。"; properties = {"task_id": field("任务标识。"), "expected_revision": field("并发保护版本号。", "integer"), "member_name": field("Lead 指定的成员名称，可选。")}; required = ("task_id", "expected_revision")
    @property
    def definition(self):
        definition = super().definition
        required = ["task_id", "expected_revision"] if self._member_name else ["task_id", "expected_revision", "member_name"]
        return ToolDefinition(definition.name, definition.description, schema(self.properties, required), definition.kind, requires_approval=definition.requires_approval, runtime_scope=definition.runtime_scope, workspace_scope=definition.workspace_scope)

    async def _execute(self, a):
        member = self._bound_member(a, required=not bool(self._member_name))
        if isinstance(member, Exception) or not isinstance(member, str): return member
        rev = required_int(a, "expected_revision", self.tool_name)
        if not isinstance(rev, int): return rev
        task = await maybe_await(_service_method(self._service, "claim_task")(_required(a, "task_id", self.tool_name), member, rev))
        return success_result(self.tool_name, task_content(task))


class TeamTaskTransitionTool(_TaskTool):
    tool_name = "team_task_transition"; description = "转换团队任务状态并记录结果。";
    properties = {"task_id": field("任务标识。"), "expected_revision": field("并发保护版本号。", "integer"), "state": field("目标状态。", enum=[x.value for x in TeamTaskState]), "summary": field("完成结果摘要。"), "commit_id": field("结果提交标识。"), "verification_summary": field("验证摘要。"), "details": field("结果详情。"), "error": field("阻塞或失败原因。")}; required = ("task_id", "expected_revision", "state")
    async def _execute(self, a):
        if self._member_name:
            owner = await maybe_await(self._owner_check(a))
            if isinstance(owner, Exception): raise owner
            current = owner
            state = a.get("state")
            if state == TeamTaskState.RUNNING.value:
                if current.state is TeamTaskState.BLOCKED: raise TeamError(code="blocked_recovery_requires_lead", phase="team", message="只有 Lead 可以恢复 blocked 任务")
                checker = getattr(self._service, "member_requires_approval", None)
                if callable(checker) and await maybe_await(checker(self._member_name, current.task_id)) and current.approval_state is not ApprovalState.APPROVED:
                    raise TeamError(code="approval_required", phase="team", message="任务运行前需要审批")
        rev = required_int(a, "expected_revision", self.tool_name)
        if not isinstance(rev, int): return rev
        state = enum_value(a, "state", TeamTaskState, self.tool_name)
        if not isinstance(state, TeamTaskState): return state
        result = None
        if any(name in a for name in ("summary", "commit_id", "verification_summary", "details")):
            summary = required_string(a, "summary", self.tool_name)
            if not isinstance(summary, str): return summary
            result = TaskResult(summary=summary, commit_id=a.get("commit_id"), verification_summary=a.get("verification_summary"), details=a.get("details"))
        task = await maybe_await(_service_method(self._service, "transition_task")(_required(a, "task_id", self.tool_name), rev, state, result, a.get("error")))
        return success_result(self.tool_name, task_content(task))


__all__ = ["TeamTaskCreateTool", "TeamTaskListTool", "TeamTaskGetTool", "TeamTaskUpdateTool", "TeamTaskDeleteTool", "TeamTaskClaimTool", "TeamTaskTransitionTool"]
