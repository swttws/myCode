from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from mycode.team.models import MemberBackend, MessageProtocol, TeamMessage
from mycode.tool import ToolDefinition, ToolKind, ToolResult, ToolRuntimeScope


class TeamTool:
    def __init__(self, *, service) -> None:
        self._service = service

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="team",
            description="Create, inspect, and coordinate a persistent local team.",
            parameters={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": [
                            "create",
                            "status",
                            "start_batch",
                            "spawn_member",
                            "send_message",
                            "archive",
                        ],
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
                    "message_id": {"type": "string"},
                    "target_name": {"type": "string"},
                    "body": {"type": "string"},
                    "summary": {"type": "string"},
                    "broadcast": {"type": "boolean"},
                    "sender": {"type": "string"},
                },
                "required": ["action"],
            },
            kind=ToolKind.WRITE,
            requires_approval=False,
            runtime_scope=ToolRuntimeScope.PARENT_ONLY,
        )

    async def execute_async(self, arguments, context=None) -> ToolResult:
        if not isinstance(arguments, dict):
            return _failure("invalid_team_arguments", "team arguments must be an object")
        action = arguments.get("action")
        if type(action) is not str or not action:
            return _failure("missing_team_action", "team action is required")
        if action not in _ACTION_ARGUMENTS:
            return _failure("unknown_team_action", f"unknown team action: {action}")
        unknown = set(arguments) - _ACTION_ARGUMENTS[action]
        if unknown:
            return _failure(
                "unknown_team_argument",
                f"unknown team argument: {sorted(unknown)[0]}",
            )
        try:
            if action == "create":
                snapshot = await self._service.create_or_attach(
                    _required_string(arguments, "team_name"),
                    goal=_optional_string(arguments.get("goal")),
                )
                return _success(
                    "team",
                    {
                        "team_name": snapshot.team.team_name,
                        "state": snapshot.team.state.value,
                        "activated": True,
                    },
                )
            if action == "status":
                snapshot = await self._service.status()
                return _success(
                    "team",
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
                    "team",
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
                    "team",
                    {
                        "member_name": member.member_name,
                        "state": state,
                    },
                )
            if action == "send_message":
                broadcast = bool(arguments.get("broadcast", False))
                target_name = None if broadcast else _required_string(arguments, "target_name")
                message = TeamMessage(
                    message_id=_required_string(arguments, "message_id"),
                    protocol=MessageProtocol.BROADCAST if broadcast else MessageProtocol.MESSAGE,
                    sender=_optional_string(arguments.get("sender")) or "lead",
                    target_name=target_name,
                    broadcast=broadcast,
                    body=_required_string(arguments, "body"),
                    summary=_optional_string(arguments.get("summary")) or _required_string(arguments, "body"),
                    timestamp=datetime.now(timezone.utc),
                )
                receipt = await self._service.send_message(message)
                return _success(
                    "team",
                    {
                        "message_id": receipt.message_id,
                        "recipient_names": list(receipt.recipient_names),
                        "fanout_count": receipt.fanout_count,
                    },
                )
            if action == "archive":
                team = await self._service.archive()
                return _success(
                    "team",
                    {
                        "team_name": team.team_name,
                        "state": team.state.value,
                    },
                )
        except Exception as exc:
            return _failure(getattr(exc, "code", "team_action_failed"), str(exc))
        return _failure("unknown_team_action", f"unknown team action: {action}")


_ACTION_ARGUMENTS = {
    "create": frozenset({"action", "team_name", "goal"}),
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
    "send_message": frozenset(
        {
            "action",
            "message_id",
            "target_name",
            "body",
            "summary",
            "broadcast",
            "sender",
        }
    ),
    "archive": frozenset({"action"}),
}


def _success(tool_name: str, content: dict[str, Any]) -> ToolResult:
    return ToolResult(ok=True, tool_name=tool_name, content=content)


def _failure(reason_code: str, message: str) -> ToolResult:
    return ToolResult(
        ok=False,
        tool_name="team",
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


__all__ = ["TeamTool"]
