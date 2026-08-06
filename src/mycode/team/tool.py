from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from mycode.team.models import (
    ApprovalState,
    MemberBackend,
    MessageProtocol,
    TaskKind,
    TaskPatch,
    TaskResult,
    TeamMessage,
    TeamTask,
    TeamTaskState,
)
from mycode.tool import ToolDefinition, ToolKind, ToolResult, ToolRuntimeScope


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
            description=_TOOL_DESCRIPTIONS[self._name],
            parameters={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": sorted(_TOOL_ACTIONS[self._name]),
                    },
                    "team_name": {"type": "string"},
                    "goal": {"type": "string"},
                    "member_name": {"type": "string"},
                    "role_name": {"type": "string"},
                    "role_revision": {"type": "integer"},
                    "requested_backend": {"type": "string"},
                    "task_id": {"type": "string"},
                    "batch_id": {"type": "string"},
                    "read_only": {"type": "boolean"},
                    "approval_required": {"type": "boolean"},
                    "force": {"type": "boolean"},
                    "message_id": {"type": "string"},
                    "target_name": {"type": "string"},
                    "body": {"type": "string"},
                    "summary": {"type": "string"},
                    "broadcast": {"type": "boolean"},
                    "sender": {"type": "string"},
                    "title": {"type": "string"},
                    "description": {"type": "string"},
                    "dependency_ids": {"type": "array", "items": {"type": "string"}},
                    "kind": {"type": "string", "enum": ["code", "read_only"]},
                    "expected_revision": {"type": "integer"},
                    "state": {
                        "type": "string",
                        "enum": [
                            "pending",
                            "claimed",
                            "awaiting_approval",
                            "running",
                            "blocked",
                            "completed",
                            "failed",
                            "cancelled",
                        ],
                    },
                    "approved": {"type": "boolean"},
                    "reason": {"type": "string"},
                    "error": {"type": "string"},
                    "plan_revision": {"type": "integer"},
                    "commit_id": {"type": "string"},
                    "verification_summary": {"type": "string"},
                    "details": {"type": "string"},
                },
                "required": ["action"],
            },
            kind=ToolKind.WRITE,
            requires_approval=False,
            runtime_scope=ToolRuntimeScope.PARENT_ONLY,
        )

    async def execute_async(self, arguments, context=None) -> ToolResult:
        if not isinstance(arguments, dict):
            return _failure(self._name, "invalid_team_arguments", "team arguments must be an object")
        action = arguments.get("action")
        if type(action) is not str or not action:
            return _failure(self._name, "missing_team_action", "team action is required")
        if action not in _TOOL_ACTIONS[self._name]:
            return _failure(self._name, "unknown_team_action", f"unknown team action: {action}")
        unknown = set(arguments) - _ACTION_ARGUMENTS[action]
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
                state = getattr(member.state, "value", member.state)
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
                state = getattr(member.state, "value", member.state)
                return _success(self._name, {"member_name": member.member_name, "state": state})
            if action == "send_message":
                return await self._send_protocol_message(arguments, protocol=None)
            if action == "create_task":
                task = _make_task(arguments)
                created = self._call_task_method("create_task", task)
                return _success(self._name, _task_content(created))
            if action == "list_tasks":
                tasks = self._call_task_method("list_tasks", _optional_string(arguments.get("batch_id")))
                return _success(self._name, {"tasks": [_task_content(task) for task in tasks]})
            if action == "get_task":
                task = self._call_task_method("get_task", _required_string(arguments, "task_id"))
                return _success(self._name, _task_content(task))
            if action == "update_task":
                if self._name == "team_member":
                    self._resolve_member_name_argument(arguments)
                task = self._call_task_method(
                    "update_task",
                    _required_string(arguments, "task_id"),
                    _required_int(arguments, "expected_revision"),
                    _make_task_patch(arguments),
                )
                return _success(self._name, _task_content(task))
            if action == "delete_task":
                self._call_task_method(
                    "delete_task",
                    _required_string(arguments, "task_id"),
                    _required_int(arguments, "expected_revision"),
                )
                return _success(self._name, {"task_id": _required_string(arguments, "task_id"), "deleted": True})
            if action == "claim_task":
                member_name = self._resolve_member_name_argument(arguments)
                task = self._call_task_method(
                    "claim_task",
                    _required_string(arguments, "task_id"),
                    member_name,
                    _required_int(arguments, "expected_revision"),
                )
                return _success(self._name, _task_content(task))
            if action == "transition_task":
                task = self._call_task_method(
                    "transition_task",
                    _required_string(arguments, "task_id"),
                    _required_int(arguments, "expected_revision"),
                    TeamTaskState(_required_string(arguments, "state")),
                    _make_task_result(arguments),
                    _optional_string(arguments.get("error")) or _optional_string(arguments.get("reason")),
                )
                return _success(self._name, _task_content(task))
            if action == "plan_submit":
                task = self._call_task_method(
                    "update_task",
                    _required_string(arguments, "task_id"),
                    _required_int(arguments, "expected_revision"),
                    TaskPatch(
                        plan_revision=_required_int(arguments, "plan_revision"),
                        approval_state=ApprovalState.PENDING,
                    ),
                )
                receipt = await self._send_protocol_message(arguments, protocol=MessageProtocol.PLAN_SUBMIT)
                content = _task_content(task)
                content["message_id"] = receipt.content["message_id"]
                return _success(self._name, content)
            if action == "plan_decision":
                approved = _required_bool(arguments, "approved")
                if not approved and _optional_string(arguments.get("reason")) is None:
                    raise ValueError("reason must be present when rejecting a plan")
                task = self._call_task_method(
                    "update_task",
                    _required_string(arguments, "task_id"),
                    _required_int(arguments, "expected_revision"),
                    TaskPatch(
                        approval_state=ApprovalState.APPROVED if approved else ApprovalState.REJECTED,
                    ),
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
            return _failure(self._name, getattr(exc, "code", "team_action_failed"), str(exc))
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

    def _call_task_method(self, method_name: str, *args):
        method = getattr(self._service, method_name, None)
        if callable(method):
            return method(*args)
        board = getattr(self._service, "task_board", None)
        board_method_name = {
            "create_task": "create",
            "list_tasks": "list",
            "get_task": "get",
            "update_task": "update",
            "delete_task": "delete",
            "claim_task": "claim",
            "transition_task": "transition",
        }[method_name]
        board_method = getattr(board, board_method_name)
        return board_method(*args)

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
    "team": "Create, inspect, and coordinate a persistent local team.",
    "team_lead": "Coordinate team batches, members, tasks, messages, approvals, and local integration.",
    "team_member": "Claim team tasks, submit plans, report status, and exchange team messages.",
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


_ACTION_ARGUMENTS = {
    "create": frozenset({"action", "team_name", "goal"}),
    "attach": frozenset({"action", "team_name", "goal"}),
    "status": frozenset({"action"}),
    "start_batch": frozenset({"action", "goal"}),
    "spawn_member": frozenset(
        {
            "action",
            "member_name",
            "role_name",
            "role_revision",
            "requested_backend",
            "task_id",
            "batch_id",
            "goal",
            "read_only",
            "approval_required",
        }
    ),
    "terminate_member": frozenset({"action", "member_name", "force"}),
    "send_message": frozenset(
        {
            "action",
            "message_id",
            "target_name",
            "task_id",
            "batch_id",
            "body",
            "summary",
            "broadcast",
            "sender",
        }
    ),
    "create_task": frozenset(
        {
            "action",
            "task_id",
            "batch_id",
            "title",
            "description",
            "dependency_ids",
            "kind",
        }
    ),
    "list_tasks": frozenset({"action", "batch_id"}),
    "get_task": frozenset({"action", "task_id"}),
    "update_task": frozenset(
        {
            "action",
            "task_id",
            "expected_revision",
            "title",
            "description",
            "dependency_ids",
            "kind",
            "member_name",
            "plan_revision",
        }
    ),
    "delete_task": frozenset({"action", "task_id", "expected_revision"}),
    "claim_task": frozenset({"action", "task_id", "member_name", "expected_revision"}),
    "transition_task": frozenset(
        {
            "action",
            "task_id",
            "expected_revision",
            "state",
            "summary",
            "commit_id",
            "verification_summary",
            "details",
            "reason",
            "error",
        }
    ),
    "plan_submit": frozenset(
        {
            "action",
            "message_id",
            "task_id",
            "batch_id",
            "target_name",
            "expected_revision",
            "plan_revision",
            "body",
            "summary",
            "sender",
        }
    ),
    "plan_decision": frozenset(
        {
            "action",
            "message_id",
            "target_name",
            "task_id",
            "batch_id",
            "expected_revision",
            "approved",
            "reason",
            "body",
            "summary",
            "sender",
        }
    ),
    "status_update": frozenset(
        {
            "action",
            "message_id",
            "target_name",
            "task_id",
            "batch_id",
            "body",
            "summary",
            "sender",
        }
    ),
    "shutdown_request": frozenset(
        {
            "action",
            "message_id",
            "target_name",
            "body",
            "summary",
            "sender",
        }
    ),
    "shutdown_response": frozenset(
        {
            "action",
            "message_id",
            "target_name",
            "body",
            "summary",
            "sender",
        }
    ),
    "integrate": frozenset({"action", "batch_id"}),
    "archive": frozenset({"action"}),
}


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
