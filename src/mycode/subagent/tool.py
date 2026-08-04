from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from mycode.subagent.models import (
    ParentAgentSnapshot,
    SubAgentConfig,
    SubAgentKind,
    SubAgentLaunchRequest,
    SubAgentTaskSnapshot,
    SubAgentTaskState,
    SubAgentTaskSummary,
    SubAgentUsage,
)
from mycode.tool import ToolDefinition, ToolKind, ToolResult, ToolRuntimeScope


AGENT_TOOL_NAME = "Agent"


@dataclass(frozen=True)
class _ParsedAction:
    action: str
    kind: SubAgentKind | None = None
    task: str | None = None
    role_name: str | None = None
    background: bool = False
    task_id: str | None = None


class AgentTool:
    def __init__(
        self,
        *,
        service,
        snapshot_store,
        config: SubAgentConfig,
    ) -> None:
        self._service = service
        self._snapshot_store = snapshot_store
        self._config = config

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name=AGENT_TOOL_NAME,
            description="启动、查询和管理当前会话内的子 Agent 任务。",
            parameters=_agent_parameters(),
            kind=ToolKind.WRITE,
            runtime_scope=ToolRuntimeScope.PARENT_ONLY,
            execution_timeout_seconds=self._config.foreground_timeout_seconds + 5,
        )

    async def execute_async(self, arguments: dict[str, Any]) -> ToolResult:
        try:
            parsed = _parse_arguments(arguments)
        except ValueError as exc:
            return _error_result("invalid_agent_arguments", str(exc))

        if parsed.action == "list":
            return ToolResult(
                ok=True,
                tool_name=AGENT_TOOL_NAME,
                content={
                    "action": "list",
                    "tasks": [_summary_to_dict(item) for item in self._service.list_tasks()],
                },
            )
        if parsed.action == "get":
            try:
                snapshot = self._service.get_task(parsed.task_id or "")
            except KeyError:
                return _error_result("task_not_found", f"未找到子 Agent 任务：{parsed.task_id}")
            return ToolResult(
                ok=True,
                tool_name=AGENT_TOOL_NAME,
                content={
                    "action": "get",
                    "task": _snapshot_to_dict(snapshot),
                },
            )

        try:
            parent = self._snapshot_store.current()
        except RuntimeError:
            return _error_result(
                "parent_snapshot_unavailable",
                "当前轮次还没有可用的父 Agent 请求快照，无法启动子 Agent。",
            )
        launch_request = SubAgentLaunchRequest(
            kind=parsed.kind or SubAgentKind.DEFINED,
            task=parsed.task or "",
            role_name=parsed.role_name,
            requested_background=(
                True if parsed.kind is SubAgentKind.FORK else parsed.background
            ),
            parent=parent,
        )
        try:
            response = await self._service.run(launch_request)
        except Exception as exc:
            return _error_result("subagent_run_failed", _safe_error(exc))
        return ToolResult(
            ok=True,
            tool_name=AGENT_TOOL_NAME,
            content={
                "action": "run",
                "inline": response.inline,
                "task": _snapshot_to_dict(response.task),
                "message": _run_message(response.inline, response.task),
            },
        )


def _parse_arguments(arguments: dict[str, Any]) -> _ParsedAction:
    if not isinstance(arguments, dict):
        raise ValueError("Agent 工具参数必须是对象。")
    action = arguments.get("action")
    if action not in {"run", "list", "get"}:
        raise ValueError("action 必须是 run、list 或 get。")
    if action == "list":
        _reject_extra(arguments, {"action"})
        return _ParsedAction(action="list")
    if action == "get":
        _reject_extra(arguments, {"action", "task_id"})
        task_id = arguments.get("task_id")
        if type(task_id) is not str or not task_id:
            raise ValueError("get 必须提供非空 task_id。")
        return _ParsedAction(action="get", task_id=task_id)

    _reject_extra(arguments, {"action", "type", "task", "role", "background"})
    raw_type = arguments.get("type")
    if raw_type not in {"defined", "fork"}:
        raise ValueError("run.type 必须是 defined 或 fork。")
    task = arguments.get("task")
    if type(task) is not str or not task:
        raise ValueError("run 必须提供非空 task。")
    if raw_type == "fork":
        if "role" in arguments:
            raise ValueError("run/fork 不允许提供 role。")
        if "background" in arguments:
            raise ValueError("run/fork 固定为后台任务，不允许提供 background。")
        return _ParsedAction(action="run", kind=SubAgentKind.FORK, task=task, background=True)

    role_name = arguments.get("role")
    if type(role_name) is not str or not role_name:
        raise ValueError("run/defined 必须提供非空 role。")
    background = arguments.get("background", False)
    if type(background) is not bool:
        raise ValueError("background 必须是布尔值。")
    return _ParsedAction(
        action="run",
        kind=SubAgentKind.DEFINED,
        task=task,
        role_name=role_name,
        background=background,
    )


def _reject_extra(arguments: dict[str, Any], allowed: set[str]) -> None:
    extra = sorted(set(arguments) - allowed)
    if extra:
        raise ValueError("Agent 工具包含未知或不允许的参数：" + ", ".join(extra))


def _agent_parameters() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "action": {
                "type": "string",
                "enum": ["run", "list", "get"],
                "description": "run 启动子 Agent，list 列出任务，get 查询任务详情。",
            },
            "type": {
                "type": "string",
                "enum": ["defined", "fork"],
                "description": "run 时的子 Agent 类型。",
            },
            "task": {"type": "string", "description": "交给子 Agent 的任务。"},
            "role": {"type": "string", "description": "defined 类型使用的角色名。"},
            "background": {
                "type": "boolean",
                "description": "defined 类型是否立即转入后台。",
            },
            "task_id": {"type": "string", "description": "get 查询的任务 ID。"},
        },
        "required": ["action"],
    }


def _snapshot_to_dict(snapshot: SubAgentTaskSnapshot) -> dict[str, Any]:
    payload = {
        "id": snapshot.id,
        "sequence": snapshot.sequence,
        "kind": snapshot.kind.value,
        "role": snapshot.role_name,
        "state": snapshot.state.value,
        "detached": snapshot.detached,
        "rounds": snapshot.rounds,
        "result": (
            {
                "detail": snapshot.result.detail,
                "summary": snapshot.result.summary,
                "detail_truncated": snapshot.result.detail_truncated,
                "summary_truncated": snapshot.result.summary_truncated,
            }
            if snapshot.result is not None
            else None
        ),
        "error_code": snapshot.error_code,
        "error_message": snapshot.error_message,
        "usage": _usage_to_dict(snapshot.usage),
    }
    payload.update(_workspace_to_dict(snapshot))
    return payload


def _summary_to_dict(summary: SubAgentTaskSummary) -> dict[str, Any]:
    payload = {
        "id": summary.id,
        "sequence": summary.sequence,
        "kind": summary.kind.value,
        "role": summary.role_name,
        "state": summary.state.value,
        "detached": summary.detached,
        "rounds": summary.rounds,
        "error_code": summary.error_code,
        "usage": _usage_to_dict(summary.usage),
    }
    payload.update(_workspace_to_dict(summary))
    return payload


def _workspace_to_dict(value: SubAgentTaskSnapshot | SubAgentTaskSummary) -> dict[str, Any]:
    workspace_root = value.workspace_root
    return {
        "isolation": value.isolation.value,
        "workspace_root": str(workspace_root) if workspace_root is not None else None,
        "branch_name": value.branch_name,
        "workspace_preparation": (
            value.workspace_preparation.value
            if value.workspace_preparation is not None
            else None
        ),
        "initialized_rules": tuple(value.initialized_rules),
        "disposition": _disposition_to_dict(value.disposition),
    }


def _disposition_to_dict(disposition) -> dict[str, Any] | None:
    if disposition is None:
        return None
    return {
        "disposition": disposition.disposition.value,
        "workspace_root": str(disposition.workspace_root),
        "branch_name": disposition.branch_name,
        "reasons": tuple(disposition.reasons),
    }


def _usage_to_dict(usage: SubAgentUsage) -> dict[str, int | str]:
    return {
        "input_tokens": _known_or_unknown(usage.input_tokens),
        "output_tokens": _known_or_unknown(usage.output_tokens),
        "total_tokens": _known_or_unknown(usage.total_tokens),
        "cache_read_tokens": _known_or_unknown(usage.cache_read_tokens),
        "cache_write_tokens": _known_or_unknown(usage.cache_write_tokens),
    }


def _known_or_unknown(value: int | None) -> int | str:
    return value if value is not None else "未知"


def _run_message(inline: bool, snapshot: SubAgentTaskSnapshot) -> str:
    if snapshot.state is SubAgentTaskState.COMPLETED:
        if inline:
            return "子 Agent 任务已完成。"
        return f"子 Agent 任务已在后台完成：{snapshot.id}"
    if snapshot.state is SubAgentTaskState.FAILED:
        return f"子 Agent 任务失败：{snapshot.error_message or snapshot.error_code}"
    if snapshot.state is SubAgentTaskState.CANCELLED:
        return f"子 Agent 任务已取消：{snapshot.id}"
    return f"子 Agent 任务已转入后台：{snapshot.id}"


def _error_result(reason_code: str, message: str) -> ToolResult:
    return ToolResult(
        ok=False,
        tool_name=AGENT_TOOL_NAME,
        content={
            "reason_code": reason_code,
            "message": message,
        },
        error=message,
    )


def _safe_error(exc: BaseException) -> str:
    message = str(exc)
    return message or exc.__class__.__name__
