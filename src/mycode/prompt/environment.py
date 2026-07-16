from __future__ import annotations

import platform
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Protocol

from mycode.prompt.models import EnvironmentSnapshot, PromptConfig, PromptDiagnostic


class EnvironmentCollector(Protocol):
    def collect(self) -> EnvironmentSnapshot:
        raise NotImplementedError


class DefaultEnvironmentCollector:
    def __init__(self, workspace_root: str | Path, config: PromptConfig) -> None:
        self._workspace_root = Path(workspace_root)
        self._config = config

    def collect(self) -> EnvironmentSnapshot:
        diagnostics: list[PromptDiagnostic] = []
        now = datetime.now().astimezone()
        git_branch = self._git_output(("branch", "--show-current"), "git_branch_unavailable", diagnostics)
        git_status = self._git_output(("status", "--short"), "git_status_unavailable", diagnostics)
        return EnvironmentSnapshot(
            workspace=str(self._workspace_root),
            operating_system=platform.system(),
            current_time=now.isoformat(),
            timezone=now.tzname() or "unknown",
            git_branch=git_branch,
            git_status=git_status,
            diagnostics=tuple(diagnostics),
        )

    def _git_output(
        self,
        arguments: tuple[str, ...],
        diagnostic_code: str,
        diagnostics: list[PromptDiagnostic],
    ) -> str | None:
        try:
            result = subprocess.run(
                ("git", *arguments),
                cwd=self._workspace_root,
                capture_output=True,
                text=True,
                timeout=self._config.git_timeout_seconds,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            diagnostics.append(PromptDiagnostic(diagnostic_code, "environment", "Git metadata is unavailable"))
            return None
        if result.returncode != 0:
            diagnostics.append(PromptDiagnostic(diagnostic_code, "environment", "Git metadata is unavailable"))
            return None
        return result.stdout.strip() or None


def format_environment_context(snapshot: EnvironmentSnapshot, config: PromptConfig) -> str:
    fields = (
        ("workspace", snapshot.workspace),
        ("operating_system", snapshot.operating_system),
        ("current_time", snapshot.current_time),
        ("timezone", snapshot.timezone),
        ("git_branch", snapshot.git_branch),
        ("git_status", snapshot.git_status),
    )
    lines = ["<environment-context>"]
    for name, value in fields:
        rendered, _ = _escape_and_truncate(value or "unknown", config.environment_value_limit)
        lines.append(f"{name}: {rendered}")
    lines.append("</environment-context>")
    return "\n".join(lines)


def _escape_and_truncate(value: str, limit: int) -> tuple[str, bool]:
    escaped_parts: list[str] = []
    emitted_length = 0
    for character in value:
        escaped = _escape_xml_character(character)
        if emitted_length + len(escaped) > limit:
            # 逐字符转义后截断，避免把 &amp; 或 &lt; 截成无效实体。
            return "".join(escaped_parts) + "...", True
        escaped_parts.append(escaped)
        emitted_length += len(escaped)
    return "".join(escaped_parts), False


def _escape_xml_character(value: str) -> str:
    return {
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        '"': "&quot;",
        "'": "&#x27;",
    }.get(value, value)
