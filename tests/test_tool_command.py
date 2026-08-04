import sys

from mycode.tool import ToolInvocationContext
from mycode.tool.command import RunCommandTool
from tests.helpers import shared_workspace, worktree_workspace


def _python_command(code: str) -> str:
    return f'"{sys.executable}" -c "{code}"'


def test_run_command_tool_returns_stdout_and_exit_code(tmp_path):
    tool = RunCommandTool(tmp_path)

    result = tool.execute({"command": _python_command("print('ok')")})

    assert result.ok is True
    assert result.content["exit_code"] == 0
    assert result.content["stdout"].strip() == "ok"
    assert result.content["stderr"] == ""
    assert result.content["timed_out"] is False


def test_run_command_tool_returns_stderr_and_nonzero_exit_code(tmp_path):
    tool = RunCommandTool(tmp_path)

    result = tool.execute(
        {"command": _python_command("import sys; print('bad', file=sys.stderr); sys.exit(3)")}
    )

    assert result.ok is False
    assert result.content["exit_code"] == 3
    assert "bad" in result.content["stderr"]
    assert result.content["timed_out"] is False


def test_run_command_tool_returns_timeout_result(tmp_path):
    tool = RunCommandTool(tmp_path, default_timeout_seconds=0.1)

    result = tool.execute({"command": _python_command("import time; time.sleep(2)")})

    assert result.ok is False
    assert result.content["exit_code"] is None
    assert result.content["timed_out"] is True
    assert "timeout" in result.error


def test_run_command_tool_runs_in_workspace_root(tmp_path):
    tool = RunCommandTool(tmp_path)

    result = tool.execute({"command": _python_command("from pathlib import Path; print(Path.cwd())")})

    assert result.ok is True
    assert result.content["stdout"].strip() == str(tmp_path)


def test_run_command_tool_rejects_mismatched_invocation_workspace(tmp_path):
    workspace_a = tmp_path / "a"
    workspace_b = tmp_path / "b"
    workspace_a.mkdir()
    workspace_b.mkdir()
    tool = RunCommandTool(workspace_a)
    context = ToolInvocationContext(workspace=shared_workspace(workspace_b))

    result = tool.execute({"command": _python_command("print('nope')")}, context)

    assert result.ok is False
    assert "调用工作区" in result.error


def test_run_command_tool_applies_worktree_hooks_path_env(tmp_path):
    hooks_path = tmp_path / ".git-hooks"
    hooks_path.mkdir()
    tool = RunCommandTool(tmp_path)
    context = ToolInvocationContext(
        workspace=worktree_workspace(tmp_path, hooks_path=hooks_path)
    )

    result = tool.execute(
        {
            "command": _python_command(
                "import os; "
                "print(os.environ.get('GIT_CONFIG_COUNT')); "
                "print(os.environ.get('GIT_CONFIG_KEY_0')); "
                "print(os.environ.get('GIT_CONFIG_VALUE_0'))"
            )
        },
        context,
    )

    assert result.ok is True
    assert result.content["stdout"].splitlines() == [
        "1",
        "core.hooksPath",
        str(hooks_path),
    ]


def test_run_command_tool_defines_required_schema_fields(tmp_path):
    tool = RunCommandTool(tmp_path)

    assert tool.definition.name == "run_command"
    assert tool.definition.parameters["required"] == ["command"]
    assert "timeout_seconds" in tool.definition.parameters["properties"]
    assert tool.definition.grant_arguments == ("command",)
