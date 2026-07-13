from __future__ import annotations

import subprocess
from pathlib import Path

from mycode.tool.base import ToolArguments, ToolDefinition, ToolResult


class RunCommandTool:
    def __init__(self, workspace_root: str | Path, default_timeout_seconds: float = 10.0) -> None:
        self._workspace_root = Path(workspace_root).resolve()
        self._default_timeout_seconds = default_timeout_seconds

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="run_command",
            description="Run a shell command in the current workspace with a timeout.",
            parameters={
                "type": "object",
                "properties": {
                    "command": {"type": "string"},
                    "timeout_seconds": {"type": "number"},
                },
                "required": ["command"],
            },
        )

    def execute(self, arguments: ToolArguments) -> ToolResult:
        try:
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
