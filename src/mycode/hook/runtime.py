from __future__ import annotations

import logging
from pathlib import Path

from mycode.hook.actions import HookActionRunner
from mycode.hook.context import build_tool_hook_context
from mycode.hook.matcher import match_condition
from mycode.hook.models import (
    HookActionResult,
    HookActionType,
    HookConfig,
    HookContext,
    HookEvent,
    HookPromptInjection,
    HookTriggerResult,
)
from mycode.permission.pathing import PathGuard
from mycode.prompt.models import PromptContextBlock
from mycode.tool.base import ToolCall, ToolDefinition, ToolResult


logger = logging.getLogger(__name__)

_HOOK_PROMPT_PRIORITY = -150


class HookRuntime:
    def __init__(
        self,
        *,
        config: HookConfig,
        workspace_root: Path,
        path_guard: PathGuard,
        runner: HookActionRunner | None = None,
    ) -> None:
        self._config = config
        self._workspace_root = workspace_root
        self._path_guard = path_guard
        self._runner = runner or HookActionRunner(workspace_root=workspace_root)
        self._once_executed: set[str] = set()
        self._prompt_injections: list[HookPromptInjection] = []

    async def trigger(self, context: HookContext) -> HookTriggerResult:
        actions = await self._run_matching_rules(context)
        return HookTriggerResult(actions=tuple(actions))

    async def before_tool(
        self,
        *,
        call: ToolCall,
        definition: ToolDefinition,
        round_index: int,
        turn_id: int,
        plan_only: bool,
        workspace_root: Path | None = None,
    ) -> HookTriggerResult:
        active_root = Path(workspace_root).resolve() if workspace_root is not None else self._workspace_root
        context = build_tool_hook_context(
            event=HookEvent.TOOL_BEFORE,
            workspace_root=active_root,
            path_guard=self._path_guard_for(active_root),
            call=call,
            definition=definition,
            round_index=round_index,
            turn_id=turn_id,
            plan_only=plan_only,
        )
        actions: list[HookActionResult] = []
        for rule in self._config.rules:
            if rule.event is not context.event:
                continue
            if not match_condition(rule.condition, context):
                continue
            if rule.once and rule.id in self._once_executed:
                continue
            if rule.once:
                self._once_executed.add(rule.id)
            try:
                result = await self._runner.run(rule, context)
            except Exception as exc:
                # Hook 是辅助自动化，运行期异常只能记录，不能打断 Agent 主流程。
                logger.warning(
                    "Hook 运行期异常：rule=%s，event=%s，reason=%s",
                    rule.id,
                    rule.event.value,
                    _safe_error(exc),
                )
                result = HookActionResult(ok=False, error=_safe_error(exc))
            self._handle_action_result(rule, result, context)
            actions.append(result)
            if result.blocked or rule.action.block:
                # 拦截必须回填为工具结果，让模型在下一轮能基于拒绝原因调整。
                return HookTriggerResult(
                    actions=tuple(actions),
                    blocked_tool_result=_blocked_tool_result(call, rule.id, _block_reason(rule, result)),
                )
        return HookTriggerResult(actions=tuple(actions))

    async def after_tool(
        self,
        *,
        call: ToolCall,
        definition: ToolDefinition,
        result: ToolResult,
        round_index: int,
        turn_id: int,
        plan_only: bool,
        workspace_root: Path | None = None,
    ) -> HookTriggerResult:
        active_root = Path(workspace_root).resolve() if workspace_root is not None else self._workspace_root
        context = build_tool_hook_context(
            event=HookEvent.TOOL_AFTER,
            workspace_root=active_root,
            path_guard=self._path_guard_for(active_root),
            call=call,
            definition=definition,
            result=result,
            round_index=round_index,
            turn_id=turn_id,
            plan_only=plan_only,
        )
        actions = await self._run_matching_rules(context)
        return HookTriggerResult(actions=tuple(actions))

    def prompt_blocks(self) -> tuple[PromptContextBlock, ...]:
        return tuple(
            PromptContextBlock(
                id=f"hook:{injection.id}",
                kind="hook",
                priority=_HOOK_PROMPT_PRIORITY,
                content=injection.content,
            )
            for injection in self._prompt_injections
        )

    def clear_request_state(self) -> None:
        self._prompt_injections.clear()

    def _add_prompt_injection(
        self,
        rule_id: str,
        content: str,
        context: HookContext,
    ) -> None:
        injection_id = f"{rule_id}-{len(self._prompt_injections) + 1}"
        self._prompt_injections.append(
            HookPromptInjection(
                id=injection_id,
                rule_id=rule_id,
                content=content,
                created_event=context.event,
            )
        )

    async def _run_matching_rules(self, context: HookContext) -> list[HookActionResult]:
        actions: list[HookActionResult] = []
        for rule in self._config.rules:
            if rule.event is not context.event:
                continue
            if not match_condition(rule.condition, context):
                continue
            if rule.once and rule.id in self._once_executed:
                continue
            if rule.once:
                self._once_executed.add(rule.id)
            result = await self._run_rule(rule, context)
            self._handle_action_result(rule, result, context)
            actions.append(result)
        return actions

    async def _run_rule(self, rule, context: HookContext) -> HookActionResult:
        try:
            return await self._runner.run(rule, context)
        except Exception as exc:
            # Hook 是辅助自动化，运行期异常只能记录，不能打断 Agent 主流程。
            logger.warning(
                "Hook 运行期异常：rule=%s，event=%s，reason=%s",
                rule.id,
                rule.event.value,
                _safe_error(exc),
            )
            return HookActionResult(ok=False, error=_safe_error(exc))

    def _handle_action_result(
        self,
        rule,
        result: HookActionResult,
        context: HookContext,
    ) -> None:
        if (
            rule.action.type is HookActionType.PROMPT
            and result.ok
            and result.output
        ):
            self._add_prompt_injection(rule.id, result.output, context)

    def _path_guard_for(self, workspace_root: Path) -> PathGuard:
        if workspace_root.resolve() == self._workspace_root.resolve():
            return self._path_guard
        return PathGuard(workspace_root)


class NullHookRuntime:
    async def trigger(self, context: HookContext) -> HookTriggerResult:
        return HookTriggerResult(actions=())

    async def before_tool(self, *args, **kwargs) -> HookTriggerResult:
        return HookTriggerResult(actions=())

    async def after_tool(self, *args, **kwargs) -> HookTriggerResult:
        return HookTriggerResult(actions=())

    def prompt_blocks(self) -> tuple[PromptContextBlock, ...]:
        return ()

    def clear_request_state(self) -> None:
        return None


def _safe_error(exc: BaseException) -> str:
    message = str(exc)
    return message or exc.__class__.__name__


def _blocked_tool_result(call: ToolCall, rule_id: str, reason: str) -> ToolResult:
    return ToolResult(
        ok=False,
        tool_name=call.name,
        content={
            "tool_call_id": call.id,
            "reason_code": "hook_blocked",
            "hook_rule_id": rule_id,
        },
        error=reason,
    )


def _block_reason(rule, result: HookActionResult) -> str:
    return (
        result.block_reason
        or rule.action.reason
        or result.output
        or rule.action.content
        or "Hook 安全策略拒绝执行该工具调用。"
    )
