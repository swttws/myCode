from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Any, Iterable
import inspect

from mycode.tool import ToolResult


def failure_result(tool_name: str, reason_code: str, message_zh: str, field: str | None = None) -> ToolResult:
    content: dict[str, Any] = {"reason_code": reason_code, "message": message_zh}
    if field is not None:
        content["field"] = field
    return ToolResult(ok=False, tool_name=tool_name, content=content, error=message_zh)


def success_result(tool_name: str, content: dict[str, Any]) -> ToolResult:
    return ToolResult(ok=True, tool_name=tool_name, content=content)


def validate_object_arguments(arguments: object, allowed_fields: Iterable[str], tool_name: str = "team") -> ToolResult | None:
    if not isinstance(arguments, dict):
        return failure_result(tool_name, "invalid_arguments", "参数必须是对象")
    unknown = sorted(set(arguments) - set(allowed_fields))
    if unknown:
        return failure_result(tool_name, "unknown_argument", f"不支持参数：{unknown[0]}", unknown[0])
    return None


def required_string(arguments: dict[str, Any], name: str, tool_name: str = "team") -> str | ToolResult:
    value = arguments.get(name)
    if type(value) is not str or not value.strip():
        return failure_result(tool_name, "missing_argument", f"参数“{name}”必须是非空字符串", name)
    return value


def required_int(arguments: dict[str, Any], name: str, tool_name: str = "team", *, non_negative: bool = True) -> int | ToolResult:
    value = arguments.get(name)
    if type(value) is not int or isinstance(value, bool) or (non_negative and value < 0):
        return failure_result(tool_name, "invalid_argument", f"参数“{name}”必须是非负整数", name)
    return value


def required_bool(arguments: dict[str, Any], name: str, tool_name: str = "team") -> bool | ToolResult:
    value = arguments.get(name)
    if type(value) is not bool:
        return failure_result(tool_name, "invalid_argument", f"参数“{name}”必须是布尔值", name)
    return value


def optional_string(arguments: dict[str, Any], name: str, tool_name: str = "team") -> str | None | ToolResult:
    if name not in arguments or arguments[name] is None:
        return None
    return required_string(arguments, name, tool_name)


def optional_bool(arguments: dict[str, Any], name: str, default: bool, tool_name: str = "team") -> bool | ToolResult:
    if name not in arguments:
        return default
    return required_bool(arguments, name, tool_name)


def enum_value(arguments: dict[str, Any], name: str, enum_type: type[Enum], tool_name: str = "team") -> Enum | ToolResult:
    value = arguments.get(name)
    if type(value) is not str:
        return failure_result(tool_name, "invalid_argument", f"参数“{name}”必须是有效枚举值", name)
    try:
        return enum_type(value)
    except ValueError:
        return failure_result(tool_name, "invalid_argument", f"参数“{name}”不是有效枚举值", name)


def task_content(task: Any) -> dict[str, Any]:
    result = getattr(task, "result", None)
    return {
        "task_id": task.task_id, "batch_id": task.batch_id, "title": task.title,
        "description": task.description, "state": task.state.value, "kind": task.kind.value,
        "owner": task.owner, "revision": task.revision, "plan_revision": task.plan_revision,
        "approval_state": task.approval_state.value, "dependency_ids": list(task.dependency_ids),
        "result": None if result is None else {
            "summary": result.summary, "commit_id": result.commit_id,
            "verification_summary": result.verification_summary, "details": result.details,
        },
        "error": task.error,
    }


def batch_content(batch: Any) -> dict[str, Any]:
    return {"batch_id": batch.batch_id, "goal": batch.goal, "state": batch.state.value, "revision": getattr(batch, "revision", None)}


def message_content(receipt: Any) -> dict[str, Any]:
    return {"message_id": receipt.message_id, "recipient_names": list(receipt.recipient_names), "fanout_count": receipt.fanout_count}


def error_result(tool_name: str, exc: Exception) -> ToolResult:
    detail = str(exc) or "团队操作失败"
    reason = getattr(exc, "code", None) or ("invalid_argument" if isinstance(exc, ValueError) else "team_action_failed")
    return failure_result(tool_name, reason, f"团队操作失败：{detail}")


async def maybe_await(value: Any) -> Any:
    return await value if inspect.isawaitable(value) else value


def schema(properties: dict[str, Any], required: list[str] | tuple[str, ...] = ()) -> dict[str, Any]:
    return {"type": "object", "properties": properties, "required": list(required), "additionalProperties": False}


def field(description: str, type_name: str = "string", **extra: Any) -> dict[str, Any]:
    return {"type": type_name, "description": description, **extra}


def as_path_list(values: Any) -> tuple[Path, ...]:
    return tuple(Path(value) for value in values or ())
