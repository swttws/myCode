from __future__ import annotations

import copy
import logging
from collections.abc import Mapping
from pathlib import Path

from mycode.hook.models import HookContext, HookEvent
from mycode.llm import ChatMessage
from mycode.permission.pathing import PathGuard
from mycode.permission.policy import build_subject
from mycode.tool.base import ToolCall, ToolDefinition, ToolResult


logger = logging.getLogger(__name__)


def build_event_hook_context(
    *,
    event: HookEvent,
    workspace_root: Path,
    turn_id: int | None = None,
    round_index: int | None = None,
    user_text: str | None = None,
    plan_only: bool = False,
) -> HookContext:
    return HookContext(
        event=event,
        workspace_root=workspace_root,
        turn_id=turn_id,
        round_index=round_index,
        user_text=user_text,
        plan_only=plan_only,
    )


def build_message_hook_context(
    *,
    event: HookEvent,
    workspace_root: Path,
    message: ChatMessage,
    turn_id: int | None = None,
    round_index: int | None = None,
    plan_only: bool = False,
) -> HookContext:
    return HookContext(
        event=event,
        workspace_root=workspace_root,
        turn_id=turn_id,
        round_index=round_index,
        message=message,
        plan_only=plan_only,
    )


def build_error_hook_context(
    *,
    workspace_root: Path,
    error_code: str,
    error_message: str,
    turn_id: int | None = None,
    round_index: int | None = None,
    plan_only: bool = False,
) -> HookContext:
    return HookContext(
        event=HookEvent.RUNTIME_ERROR,
        workspace_root=workspace_root,
        turn_id=turn_id,
        round_index=round_index,
        error_code=error_code,
        error_message=error_message,
        plan_only=plan_only,
    )


def build_tool_hook_context(
    *,
    event: HookEvent,
    workspace_root: Path,
    path_guard: PathGuard,
    call: ToolCall,
    definition: ToolDefinition,
    round_index: int,
    turn_id: int,
    plan_only: bool,
    result: ToolResult | None = None,
) -> HookContext:
    raw_arguments = _raw_arguments(call.arguments)
    try:
        # 权限已经完成是否允许的判断；Hook 这里只复用同一份规范化结果做条件匹配。
        subject = build_subject(call, definition, path_guard)
        normalized = dict(subject.normalized_arguments)
        return HookContext(
            event=event,
            workspace_root=workspace_root,
            turn_id=turn_id,
            round_index=round_index,
            tool_call=call,
            tool_definition=definition,
            normalized_arguments=normalized,
            raw_arguments=raw_arguments,
            tool_result=result,
            plan_only=plan_only,
        )
    except Exception:
        logger.warning(
            "Hook 工具参数规范化失败：event=%s，tool=%s，call_id=%s",
            event.value,
            call.name,
            call.id,
        )
        return HookContext(
            event=event,
            workspace_root=workspace_root,
            turn_id=turn_id,
            round_index=round_index,
            tool_call=call,
            tool_definition=definition,
            normalized_arguments={},
            raw_arguments=raw_arguments,
            tool_result=result,
            error_code="hook_argument_normalization_failed",
            error_message="工具参数规范化失败，Hook 条件将仅使用安全上下文。",
            plan_only=plan_only,
        )


def _raw_arguments(arguments: Mapping[str, object] | None) -> dict[str, object]:
    if not isinstance(arguments, Mapping):
        return {}
    return copy.deepcopy(dict(arguments))
