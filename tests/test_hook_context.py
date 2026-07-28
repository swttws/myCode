from __future__ import annotations

from pathlib import Path

from mycode.hook.context import (
    build_error_hook_context,
    build_event_hook_context,
    build_message_hook_context,
    build_tool_hook_context,
)
from mycode.hook.matcher import flatten_context
from mycode.hook.models import HookEvent
from mycode.llm import ChatMessage
from mycode.permission.pathing import PathGuard
from mycode.tool import ToolCall, ToolDefinition, ToolKind, ToolResult


def definition(
    name: str,
    *,
    kind: ToolKind,
    properties: dict[str, object],
    required: tuple[str, ...],
    grant_arguments: tuple[str, ...] = (),
) -> ToolDefinition:
    return ToolDefinition(
        name=name,
        description="test",
        parameters={
            "type": "object",
            "properties": properties,
            "required": list(required),
        },
        kind=kind,
        grant_arguments=grant_arguments,
    )


def file_definition() -> ToolDefinition:
    return definition(
        "read_file",
        kind=ToolKind.READ,
        properties={"path": {"type": "string"}},
        required=("path",),
        grant_arguments=("path",),
    )


def find_definition() -> ToolDefinition:
    return definition(
        "find_files",
        kind=ToolKind.READ,
        properties={"pattern": {"type": "string"}, "root": {"type": "string"}},
        required=("pattern",),
        grant_arguments=("root",),
    )


def command_definition() -> ToolDefinition:
    return definition(
        "run_command",
        kind=ToolKind.WRITE,
        properties={"command": {"type": "string"}},
        required=("command",),
        grant_arguments=("command",),
    )


def test_tool_context_uses_permission_normalized_path(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text("", encoding="utf-8")
    call = ToolCall("call-1", "read_file", {"path": "src\\main.py"}, raw_arguments='{"path":"src\\\\main.py"}')

    context = build_tool_hook_context(
        event=HookEvent.TOOL_BEFORE,
        workspace_root=tmp_path,
        path_guard=PathGuard(tmp_path),
        call=call,
        definition=file_definition(),
        round_index=3,
        turn_id=9,
        plan_only=False,
    )

    assert context.normalized_arguments["path"].endswith("src/main.py")
    assert context.raw_arguments == {"path": "src\\main.py"}
    assert context.tool_call == call
    assert flatten_context(context)["arguments.path"].endswith("src/main.py")


def test_tool_context_defaults_root_like_permission_rules(tmp_path: Path) -> None:
    call = ToolCall("call-1", "find_files", {"pattern": "*.py"})

    context = build_tool_hook_context(
        event=HookEvent.TOOL_BEFORE,
        workspace_root=tmp_path,
        path_guard=PathGuard(tmp_path),
        call=call,
        definition=find_definition(),
        round_index=1,
        turn_id=2,
        plan_only=False,
    )

    assert context.normalized_arguments["root"] == "."
    assert context.raw_arguments == {"pattern": "*.py"}


def test_tool_context_uses_permission_normalized_command(tmp_path: Path) -> None:
    call = ToolCall(
        "call-1",
        "run_command",
        {"command": "  echo   'a  b'   &&   echo done  "},
    )

    context = build_tool_hook_context(
        event=HookEvent.TOOL_BEFORE,
        workspace_root=tmp_path,
        path_guard=PathGuard(tmp_path),
        call=call,
        definition=command_definition(),
        round_index=1,
        turn_id=2,
        plan_only=True,
    )

    assert context.normalized_arguments["command"] == "echo 'a  b' && echo done"
    assert context.raw_arguments["command"] == "  echo   'a  b'   &&   echo done  "
    assert flatten_context(context)["session.plan_only"] is True


def test_message_and_tool_result_contexts_expose_stable_fields(tmp_path: Path) -> None:
    message_context = build_message_hook_context(
        event=HookEvent.USER_MESSAGE,
        workspace_root=tmp_path,
        message=ChatMessage(role="user", content="hello"),
        turn_id=4,
        round_index=None,
        plan_only=False,
    )
    result_context = build_tool_hook_context(
        event=HookEvent.TOOL_AFTER,
        workspace_root=tmp_path,
        path_guard=PathGuard(tmp_path),
        call=ToolCall("call-1", "run_command", {"command": "echo ok"}),
        definition=command_definition(),
        result=ToolResult(False, "run_command", {"tool_call_id": "call-1"}, error="bad"),
        round_index=5,
        turn_id=4,
        plan_only=False,
    )

    assert flatten_context(message_context)["message.content"] == "hello"
    assert flatten_context(result_context)["result.ok"] is False
    assert flatten_context(result_context)["result.error"] == "bad"


def test_general_and_error_contexts_expose_safe_fields(tmp_path: Path) -> None:
    general = build_event_hook_context(
        event=HookEvent.USER_REQUEST_START,
        workspace_root=tmp_path,
        turn_id=1,
        user_text="run tests",
        plan_only=True,
    )
    error = build_error_hook_context(
        workspace_root=tmp_path,
        error_code="llm_error",
        error_message="safe summary",
        turn_id=1,
        round_index=2,
        plan_only=True,
    )

    assert flatten_context(general)["user_text"] == "run tests"
    assert flatten_context(general)["session.plan_only"] is True
    assert error.event is HookEvent.RUNTIME_ERROR
    assert flatten_context(error)["error.code"] == "llm_error"
    assert flatten_context(error)["error.message"] == "safe summary"


def test_tool_context_normalization_failure_is_fail_soft(tmp_path: Path) -> None:
    call = ToolCall("call-1", "read_file", {"path": "../secret.txt"})

    context = build_tool_hook_context(
        event=HookEvent.TOOL_BEFORE,
        workspace_root=tmp_path,
        path_guard=PathGuard(tmp_path),
        call=call,
        definition=file_definition(),
        round_index=1,
        turn_id=2,
        plan_only=False,
    )

    assert context.normalized_arguments == {}
    assert context.raw_arguments == {"path": "../secret.txt"}
    assert context.error_code == "hook_argument_normalization_failed"
    assert "secret" not in (context.error_message or "")
