from __future__ import annotations

from pathlib import Path

import pytest

from mycode.hook.config import load_hook_file
from mycode.hook.matcher import flatten_context, match_condition, parse_matcher
from mycode.hook.models import (
    HookCondition,
    HookConfigError,
    HookContext,
    HookEvent,
    HookPredicate,
    MatchKind,
)
from mycode.llm import ChatMessage
from mycode.tool import ToolCall, ToolResult


def predicate(field: str, matcher_value: object) -> HookPredicate:
    return HookPredicate(field=field, matcher=parse_matcher(matcher_value, location=field))


def context() -> HookContext:
    return HookContext(
        event=HookEvent.TOOL_AFTER,
        workspace_root=Path("D:/workspace"),
        turn_id=7,
        round_index=2,
        message=ChatMessage(role="assistant", content="TODO: run tests"),
        tool_call=ToolCall(
            id="call-1",
            name="run_command",
            arguments={"command": "  pytest   -q  ", "path": "src/main.py"},
        ),
        normalized_arguments={"command": "pytest -q", "path": "src/main.py"},
        raw_arguments={"command": "  pytest   -q  ", "path": "src/main.py"},
        tool_result=ToolResult(
            ok=False,
            tool_name="run_command",
            content={"tool_call_id": "call-1"},
            error="failed",
        ),
        error_code="tool_failed",
        error_message="failed safely",
        plan_only=True,
    )


def test_missing_condition_matches_unconditionally() -> None:
    assert match_condition(None, context()) is True


def test_all_requires_every_predicate_to_match() -> None:
    condition = HookCondition(
        mode="all",
        predicates=(
            predicate("tool", "run_command"),
            predicate("arguments.command", "pytest -q"),
        ),
    )
    failing = HookCondition(
        mode="all",
        predicates=(
            predicate("tool", "run_command"),
            predicate("result.ok", True),
        ),
    )

    assert match_condition(condition, context()) is True
    assert match_condition(failing, context()) is False


def test_any_requires_one_predicate_to_match() -> None:
    condition = HookCondition(
        mode="any",
        predicates=(
            predicate("result.ok", True),
            predicate("message.content", "glob:*TODO*"),
        ),
    )
    failing = HookCondition(
        mode="any",
        predicates=(
            predicate("result.ok", True),
            predicate("tool", "edit_file"),
        ),
    )

    assert match_condition(condition, context()) is True
    assert match_condition(failing, context()) is False


@pytest.mark.parametrize(
    ("value", "kind", "expected", "negate"),
    [
        ("src/main.py", MatchKind.EXACT, "src/main.py", False),
        (3, MatchKind.EXACT, 3, False),
        (True, MatchKind.EXACT, True, False),
        ("src/**/*.py", MatchKind.GLOB, "src/**/*.py", False),
        ("glob:src/**", MatchKind.GLOB, "src/**", False),
        ("re:\\bpytest\\b", MatchKind.REGEX, "\\bpytest\\b", False),
        ("!src/**", MatchKind.GLOB, "src/**", True),
        ("!glob:.env*", MatchKind.GLOB, ".env*", True),
        ("!re:\\brm\\b", MatchKind.REGEX, "\\brm\\b", True),
        ({"exact": "run_command"}, MatchKind.EXACT, "run_command", False),
        ({"glob": "src/**/*.py"}, MatchKind.GLOB, "src/**/*.py", False),
        ({"regex": "\\bpytest\\b", "not": True}, MatchKind.REGEX, "\\bpytest\\b", True),
    ],
)
def test_parse_matcher_supports_all_declared_syntaxes(
    value: object,
    kind: MatchKind,
    expected: object,
    negate: bool,
) -> None:
    matcher = parse_matcher(value, location="test")

    assert matcher.kind is kind
    assert matcher.expected == expected
    assert matcher.negate is negate


@pytest.mark.parametrize(
    ("field", "matcher_value"),
    [
        ("tool", "run_command"),
        ("round_index", 2),
        ("session.plan_only", True),
        ("arguments.path", "src/*.py"),
        ("arguments.path", {"glob": "src/**/*.py"}),
        ("arguments.command", "re:\\bpytest\\b"),
        ("message.content", {"regex": "TODO"}),
        ("result.ok", False),
        ("tool", "!edit_file"),
    ],
)
def test_match_condition_supports_exact_glob_regex_and_negate(
    field: str,
    matcher_value: object,
) -> None:
    condition = HookCondition(mode="all", predicates=(predicate(field, matcher_value),))

    assert match_condition(condition, context()) is True


def test_missing_context_field_does_not_match() -> None:
    condition = HookCondition(
        mode="all",
        predicates=(predicate("arguments.missing", "*"),),
    )

    assert match_condition(condition, context()) is False


def test_flatten_context_contains_stable_field_paths() -> None:
    flattened = flatten_context(context())

    assert flattened["event"] == "tool_after"
    assert flattened["turn_id"] == 7
    assert flattened["round_index"] == 2
    assert flattened["tool"] == "run_command"
    assert flattened["arguments.command"] == "pytest -q"
    assert flattened["arguments.path"] == "src/main.py"
    assert flattened["raw_arguments.command"] == "  pytest   -q  "
    assert flattened["result.ok"] is False
    assert flattened["result.error"] == "failed"
    assert flattened["message.role"] == "assistant"
    assert flattened["message.content"] == "TODO: run tests"
    assert flattened["error.code"] == "tool_failed"
    assert flattened["error.message"] == "failed safely"
    assert flattened["session.plan_only"] is True


@pytest.mark.parametrize(
    "condition_yaml",
    [
        "all: {}",
        "any: {}",
        "all: {tool: run_command}\n      any: {result.ok: false}",
        "all:\n        all: {tool: run_command}",
    ],
)
def test_load_rejects_invalid_logic_structures(tmp_path: Path, condition_yaml: str) -> None:
    path = tmp_path / "bad-logic.yaml"
    path.write_text(
        f"""
version: 1
hooks:
  - event: tool_after
    if:
      {condition_yaml}
    action:
      type: prompt
      content: hi
""",
        encoding="utf-8",
    )

    with pytest.raises(HookConfigError):
        load_hook_file(path)


def test_load_rejects_invalid_regex_before_runtime(tmp_path: Path) -> None:
    path = tmp_path / "bad-regex.yaml"
    path.write_text(
        """
version: 1
hooks:
  - event: user_message
    if:
      all:
        message.content:
          regex: "["
    action:
      type: prompt
      content: hi
""",
        encoding="utf-8",
    )

    with pytest.raises(HookConfigError, match="regex"):
        load_hook_file(path)
