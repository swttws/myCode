from __future__ import annotations

from mycode.team.models import MemberBackend
from mycode.team.tool_helpers import batch_content, error_result, field, enum_value, maybe_await, required_bool, required_int, required_string, schema, success_result, validate_object_arguments, optional_bool
from mycode.tool import ToolDefinition, ToolKind, ToolRuntimeScope


class _OrchestrationTool:
    tool_name = ""
    description = ""
    properties: dict = {}
    required: tuple[str, ...] = ()

    def __init__(self, service) -> None:
        self._service = service

    @property
    def definition(self):
        return ToolDefinition(self.tool_name, self.description, schema(self.properties, self.required), ToolKind.WRITE, requires_approval=False, runtime_scope=ToolRuntimeScope.PARENT_ONLY)

    async def execute_async(self, arguments, context=None):
        invalid = validate_object_arguments(arguments, self.properties, self.tool_name)
        if invalid: return invalid
        arguments = arguments or {}
        missing = next((name for name in self.required if name not in arguments), None)
        if missing: return error_result(self.tool_name, ValueError(f"缺少必填参数：{missing}"))
        try: return await self._execute(arguments)
        except Exception as exc: return error_result(self.tool_name, exc)


class TeamBatchStartTool(_OrchestrationTool):
    tool_name = "team_batch_start"; description = "启动一个团队批次。"
    properties = {"goal": field("批次目标，必须是非空字符串。")}; required = ("goal",)
    async def _execute(self, a):
        goal = required_string(a, "goal", self.tool_name)
        if not isinstance(goal, str): return goal
        return success_result(self.tool_name, batch_content(await maybe_await(self._service.start_batch(goal))))


class TeamBatchIntegrateTool(_OrchestrationTool):
    tool_name = "team_batch_integrate"; description = "整合一个已完成的团队批次。"
    properties = {"batch_id": field("待整合的批次标识。")}; required = ("batch_id",)
    async def _execute(self, a):
        batch_id = required_string(a, "batch_id", self.tool_name)
        if not isinstance(batch_id, str): return batch_id
        report = await maybe_await(self._service.integrate_batch(batch_id))
        return success_result(self.tool_name, {"batch_id": report.batch_id, "state": report.state.value, "result_commit_id": report.result_commit_id, "conflict_task_id": report.conflict_task_id, "integrated_member_names": list(report.integrated_member_names)})


class TeamMemberSpawnTool(_OrchestrationTool):
    tool_name = "team_member_spawn"; description = "启动一个绑定任务和批次的团队成员。"
    properties = {
        "member_name": field("成员名称。"), "role_name": field("成员角色名称。"), "role_revision": field("角色版本号。", "integer"),
        "requested_backend": field("请求的成员后端。", "string", enum=[x.value for x in MemberBackend]), "task_id": field("成员负责的任务标识。"),
        "batch_id": field("所属批次标识。"), "goal": field("成员工作目标。"), "read_only": field("是否只读。", "boolean"), "approval_required": field("写入前是否需要审批。", "boolean"),
    }
    required = tuple(properties)
    async def _execute(self, a):
        vals = {}
        for name in ("member_name", "role_name", "task_id", "batch_id", "goal"):
            value = required_string(a, name, self.tool_name)
            if not isinstance(value, str): return value
            vals[name] = value
        for name in ("role_revision",):
            value = required_int(a, name, self.tool_name)
            if not isinstance(value, int): return value
            vals[name] = value
        backend = enum_value(a, "requested_backend", MemberBackend, self.tool_name)
        if isinstance(backend, Exception) or not isinstance(backend, MemberBackend): return backend
        vals["requested_backend"] = backend
        for name in ("read_only", "approval_required"):
            value = required_bool(a, name, self.tool_name)
            if not isinstance(value, bool): return value
            vals[name] = value
        member = await maybe_await(self._service.spawn_member(**vals))
        return success_result(self.tool_name, {"member_name": member.member_name, "state": getattr(member.state, "value", member.state)})


class TeamMemberTerminateTool(_OrchestrationTool):
    tool_name = "team_member_terminate"; description = "终止一个团队成员。"
    properties = {"member_name": field("要终止的成员名称。"), "force": field("是否强制终止，默认 false。", "boolean")}; required = ("member_name",)
    async def _execute(self, a):
        name = required_string(a, "member_name", self.tool_name)
        if not isinstance(name, str): return name
        force = optional_bool(a, "force", False, self.tool_name)
        if not isinstance(force, bool): return force
        member = await maybe_await(self._service.terminate_member(name, force=force))
        return success_result(self.tool_name, {"member_name": member.member_name, "state": getattr(member.state, "value", member.state)})


__all__ = ["TeamBatchStartTool", "TeamBatchIntegrateTool", "TeamMemberSpawnTool", "TeamMemberTerminateTool"]
