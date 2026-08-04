from __future__ import annotations

import subprocess
import os
from pathlib import Path

from mycode.tool.base import (
    ToolArguments,
    ToolDefinition,
    ToolInvocationContext,
    ToolKind,
    ToolResult,
    ToolWorkspaceScope,
)


class RunCommandTool:
    def __init__(self, workspace_root: str | Path, default_timeout_seconds: float = 10.0) -> None:
        self._workspace_root = Path(workspace_root).resolve()
        self._default_timeout_seconds = default_timeout_seconds

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="run_command",
            description="在当前工作区内执行 shell 命令，并返回退出码、标准输出、标准错误和超时状态。",
            parameters={
                "type": "object",
                "description": "执行命令所需参数。",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "要执行的 shell 命令。",
                    },
                    "timeout_seconds": {
                        "type": "number",
                        "description": "命令超时时间（秒）。",
                    },
                },
                "required": ["command"],
            },
            kind=ToolKind.WRITE,
            grant_arguments=("command",),
            workspace_scope=ToolWorkspaceScope.WORKSPACE_AWARE,
        )

    def execute(
        self,
        arguments: ToolArguments,
        context: ToolInvocationContext | None = None,
    ) -> ToolResult:
        try:
            _ensure_invocation_workspace(self._workspace_root, context)
            command = arguments.get("command")
            if not isinstance(command, str):
                raise ValueError("command must be a string")
            timeout_seconds = _timeout_seconds(arguments.get("timeout_seconds"), self._default_timeout_seconds)
            completed = subprocess.run(
                command,
                cwd=self._workspace_root,
                shell=True,
                text=True,
                capture_output=True,
                timeout=timeout_seconds,
                env=_command_environment(context),
            )
            content = {
                "exit_code": completed.returncode,
                "stdout": completed.stdout,
                "stderr": completed.stderr,
                "timed_out": False,
            }
            return ToolResult(
                ok=completed.returncode == 0,
                tool_name=self.definition.name,
                content=content,
                error=None if completed.returncode == 0 else f"command exited with code {completed.returncode}",
            )
        except subprocess.TimeoutExpired as exc:
            return ToolResult(
                ok=False,
                tool_name=self.definition.name,
                content={
                    "exit_code": None,
                    "stdout": exc.stdout or "",
                    "stderr": exc.stderr or "",
                    "timed_out": True,
                },
                error=f"command timeout after {exc.timeout} seconds",
            )
        except Exception as exc:
            return ToolResult(
                ok=False,
                tool_name=self.definition.name,
                content={"exit_code": None, "stdout": "", "stderr": "", "timed_out": False},
                error=str(exc),
            )


def _timeout_seconds(value: object, default: float) -> float:
    if value is None:
        return default
    if isinstance(value, bool):
        raise ValueError("timeout_seconds must be a number")
    return float(value)


def _ensure_invocation_workspace(
    bound_root: Path,
    context: ToolInvocationContext | None,
) -> None:
    if context is None:
        return
    if bound_root.resolve() != context.workspace.root.resolve():
        raise ValueError("工具绑定工作区与调用工作区不一致。")


def _command_environment(context: ToolInvocationContext | None) -> dict[str, str] | None:
    if context is None or context.workspace.hooks_path is None:
        return None
    env = os.environ.copy()
    env["GIT_CONFIG_COUNT"] = "1"
    env["GIT_CONFIG_KEY_0"] = "core.hooksPath"
    env["GIT_CONFIG_VALUE_0"] = str(context.workspace.hooks_path)
    return env
