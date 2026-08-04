from __future__ import annotations

import asyncio
from pathlib import Path

from mycode.hook.models import (
    HookAction,
    HookActionResult,
    HookActionType,
    HookCondition,
    HookConfig,
    HookContext,
    HookEvent,
    HookPredicate,
    HookRule,
)
from mycode.hook.runtime import HookRuntime, NullHookRuntime
from mycode.hook.matcher import parse_matcher
from mycode.permission.pathing import PathGuard
from mycode.tool import ToolCall, ToolDefinition, ToolKind, ToolResult


class RecordingRunner:
    def __init__(self, results: dict[str, HookActionResult | Exception] | None = None) -> None:
        self.results = results or {}
        self.calls: list[tuple[str, HookEvent]] = []
        self.contexts: list[HookContext] = []

    async def run(self, rule: HookRule, context: HookContext) -> HookActionResult:
        self.calls.append((rule.id, context.event))
        self.contexts.append(context)
        result = self.results.get(rule.id)
        if isinstance(result, Exception):
            raise result
        if result is not None:
            return result
        if rule.action.type is HookActionType.PROMPT:
            return HookActionResult(ok=True, output=rule.action.content or "")
        return HookActionResult(ok=True, output=rule.id)


def prompt_rule(
    rule_id: str,
    event: HookEvent = HookEvent.MODEL_ROUND_START,
    *,
    content: str | None = None,
    condition: HookCondition | None = None,
    once: bool = False,
    index: int = 0,
) -> HookRule:
    return HookRule(
        id=rule_id,
        event=event,
        condition=condition,
        action=HookAction(
            type=HookActionType.PROMPT,
            content=content or f"content:{rule_id}",
        ),
        once=once,
        background=False,
        timeout_seconds=None,
        index=index,
    )


def tool_before_rule(
    rule_id: str,
    *,
    block: bool,
    content: str | None = "blocked by content",
    reason: str | None = None,
    index: int = 0,
) -> HookRule:
    return HookRule(
        id=rule_id,
        event=HookEvent.TOOL_BEFORE,
        condition=None,
        action=HookAction(
            type=HookActionType.PROMPT,
            content=content,
            block=block,
            reason=reason,
        ),
        once=False,
        background=False,
        timeout_seconds=None,
        index=index,
    )


def command_definition() -> ToolDefinition:
    return ToolDefinition(
        name="run_command",
        description="test",
        parameters={
            "type": "object",
            "properties": {"command": {"type": "string"}},
            "required": ["command"],
        },
        kind=ToolKind.WRITE,
        grant_arguments=("command",),
    )


def command_call(call_id: str = "call-1") -> ToolCall:
    return ToolCall(call_id, "run_command", {"command": "echo ok"})


def runtime(
    tmp_path: Path,
    rules: tuple[HookRule, ...],
    runner: RecordingRunner,
) -> HookRuntime:
    return HookRuntime(
        config=HookConfig(version=1, rules=rules),
        workspace_root=tmp_path,
        path_guard=PathGuard(tmp_path),
        runner=runner,
    )


def context(tmp_path: Path, event: HookEvent = HookEvent.MODEL_ROUND_START) -> HookContext:
    return HookContext(
        event=event,
        workspace_root=tmp_path,
        round_index=1,
        normalized_arguments={"command": "pytest -q"},
    )


def test_trigger_runs_matching_rules_in_yaml_order(tmp_path: Path) -> None:
    runner = RecordingRunner()
    hook_runtime = runtime(
        tmp_path,
        (
            prompt_rule("first", index=0),
            prompt_rule("other-event", HookEvent.MODEL_ROUND_END, index=1),
            prompt_rule("second", index=2),
        ),
        runner,
    )

    result = asyncio.run(hook_runtime.trigger(context(tmp_path)))

    assert [call[0] for call in runner.calls] == ["first", "second"]
    assert [action.output for action in result.actions] == ["content:first", "content:second"]


def test_trigger_skips_rules_when_condition_does_not_match(tmp_path: Path) -> None:
    condition = HookCondition(
        mode="all",
        predicates=(
            HookPredicate(
                field="arguments.command",
                matcher=parse_matcher("re:\\bpytest\\b", location="test"),
            ),
        ),
    )
    failing_condition = HookCondition(
        mode="all",
        predicates=(
            HookPredicate(
                field="arguments.command",
                matcher=parse_matcher("re:\\bruff\\b", location="test"),
            ),
        ),
    )
    runner = RecordingRunner()
    hook_runtime = runtime(
        tmp_path,
        (
            prompt_rule("matched", condition=condition, index=0),
            prompt_rule("skipped", condition=failing_condition, index=1),
        ),
        runner,
    )

    asyncio.run(hook_runtime.trigger(context(tmp_path)))

    assert [call[0] for call in runner.calls] == ["matched"]


def test_once_rule_runs_once_per_runtime(tmp_path: Path) -> None:
    runner = RecordingRunner()
    rule = prompt_rule("once", once=True)
    hook_runtime = runtime(tmp_path, (rule,), runner)

    asyncio.run(hook_runtime.trigger(context(tmp_path)))
    asyncio.run(hook_runtime.trigger(context(tmp_path)))
    fresh_runner = RecordingRunner()
    fresh_runtime = runtime(tmp_path, (rule,), fresh_runner)
    asyncio.run(fresh_runtime.trigger(context(tmp_path)))

    assert [call[0] for call in runner.calls] == ["once"]
    assert [call[0] for call in fresh_runner.calls] == ["once"]


def test_prompt_action_becomes_stable_framework_block(tmp_path: Path) -> None:
    runner = RecordingRunner()
    hook_runtime = runtime(tmp_path, (prompt_rule("prompt-rule", content="remember tests"),), runner)

    asyncio.run(hook_runtime.trigger(context(tmp_path)))

    blocks = hook_runtime.prompt_blocks()
    assert len(blocks) == 1
    assert blocks[0].id == "hook:prompt-rule-1"
    assert blocks[0].kind == "hook"
    assert blocks[0].priority == -150
    assert blocks[0].content == "remember tests"


def test_clear_request_state_clears_prompt_blocks_not_once_state(tmp_path: Path) -> None:
    runner = RecordingRunner()
    hook_runtime = runtime(tmp_path, (prompt_rule("once-prompt", once=True),), runner)

    asyncio.run(hook_runtime.trigger(context(tmp_path)))
    hook_runtime.clear_request_state()
    asyncio.run(hook_runtime.trigger(context(tmp_path)))

    assert hook_runtime.prompt_blocks() == ()
    assert [call[0] for call in runner.calls] == ["once-prompt"]


def test_action_failure_does_not_stop_later_rules(tmp_path: Path) -> None:
    runner = RecordingRunner(
        {
            "broken": RuntimeError("private stack"),
            "failed-result": HookActionResult(ok=False, error="failed"),
        }
    )
    hook_runtime = runtime(
        tmp_path,
        (
            prompt_rule("broken", index=0),
            prompt_rule("failed-result", index=1),
            prompt_rule("after", index=2),
        ),
        runner,
    )

    result = asyncio.run(hook_runtime.trigger(context(tmp_path)))

    assert [call[0] for call in runner.calls] == ["broken", "failed-result", "after"]
    assert [action.ok for action in result.actions] == [False, False, True]


def test_null_runtime_returns_empty_results(tmp_path: Path) -> None:
    null_runtime = NullHookRuntime()

    result = asyncio.run(null_runtime.trigger(context(tmp_path)))

    assert result.actions == ()
    assert result.blocked_tool_result is None
    assert null_runtime.prompt_blocks() == ()
    null_runtime.clear_request_state()


def test_before_tool_first_blocking_rule_wins(tmp_path: Path) -> None:
    runner = RecordingRunner()
    hook_runtime = runtime(
        tmp_path,
        (
            tool_before_rule("first-block", block=True, reason="拒绝执行。", index=0),
            tool_before_rule("second-block", block=True, reason="不应执行。", index=1),
        ),
        runner,
    )

    result = asyncio.run(
        hook_runtime.before_tool(
            call=command_call(),
            definition=command_definition(),
            round_index=1,
            turn_id=2,
            plan_only=False,
        )
    )

    assert [call[0] for call in runner.calls] == ["first-block"]
    assert result.blocked_tool_result is not None
    assert result.blocked_tool_result.ok is False
    assert result.blocked_tool_result.tool_name == "run_command"
    assert result.blocked_tool_result.content == {
        "tool_call_id": "call-1",
        "reason_code": "hook_blocked",
        "hook_rule_id": "first-block",
    }
    assert result.blocked_tool_result.error == "拒绝执行。"


def test_before_tool_block_reason_falls_back_to_prompt_content_then_default(
    tmp_path: Path,
) -> None:
    content_runtime = runtime(
        tmp_path,
        (tool_before_rule("content-block", block=True, content="content reason"),),
        RecordingRunner(),
    )
    default_runtime = runtime(
        tmp_path,
        (
            tool_before_rule(
                "default-block",
                block=True,
                content=None,
            ),
        ),
        RecordingRunner({"default-block": HookActionResult(ok=True, output="")}),
    )

    content_result = asyncio.run(
        content_runtime.before_tool(
            call=command_call("call-content"),
            definition=command_definition(),
            round_index=1,
            turn_id=2,
            plan_only=False,
        )
    )
    default_result = asyncio.run(
        default_runtime.before_tool(
            call=command_call("call-default"),
            definition=command_definition(),
            round_index=1,
            turn_id=2,
            plan_only=False,
        )
    )

    assert content_result.blocked_tool_result is not None
    assert content_result.blocked_tool_result.error == "content reason"
    assert default_result.blocked_tool_result is not None
    assert default_result.blocked_tool_result.error == "Hook 安全策略拒绝执行该工具调用。"


def test_before_tool_non_blocking_rule_does_not_block_tool(tmp_path: Path) -> None:
    runner = RecordingRunner()
    hook_runtime = runtime(
        tmp_path,
        (tool_before_rule("observe", block=False),),
        runner,
    )

    result = asyncio.run(
        hook_runtime.before_tool(
            call=command_call(),
            definition=command_definition(),
            round_index=1,
            turn_id=2,
            plan_only=False,
        )
    )

    assert [call[0] for call in runner.calls] == ["observe"]
    assert result.blocked_tool_result is None


def test_before_tool_uses_call_workspace_root(tmp_path: Path) -> None:
    configured_root = tmp_path / "configured"
    call_root = tmp_path / "call"
    configured_root.mkdir()
    call_root.mkdir()
    runner = RecordingRunner()
    hook_runtime = runtime(
        configured_root,
        (tool_before_rule("observe", block=False),),
        runner,
    )

    result = asyncio.run(
        hook_runtime.before_tool(
            call=command_call(),
            definition=command_definition(),
            round_index=1,
            turn_id=2,
            plan_only=False,
            workspace_root=call_root,
        )
    )

    assert result.blocked_tool_result is None
    assert runner.contexts[0].workspace_root == call_root


def test_after_tool_never_returns_blocked_tool_result(tmp_path: Path) -> None:
    runner = RecordingRunner({"after": HookActionResult(ok=True, output="blocked", blocked=True)})
    after_rule = HookRule(
        id="after",
        event=HookEvent.TOOL_AFTER,
        condition=None,
        action=HookAction(
            type=HookActionType.PROMPT,
            content="after",
            block=True,
            reason="ignored",
        ),
        once=False,
        background=False,
        timeout_seconds=None,
        index=0,
    )
    hook_runtime = runtime(tmp_path, (after_rule,), runner)

    result = asyncio.run(
        hook_runtime.after_tool(
            call=command_call(),
            definition=command_definition(),
            result=ToolResult(True, "run_command", {"tool_call_id": "call-1"}),
            round_index=1,
            turn_id=2,
            plan_only=False,
        )
    )

    assert [call[0] for call in runner.calls] == ["after"]
    assert result.actions[0].blocked is True
    assert result.blocked_tool_result is None
