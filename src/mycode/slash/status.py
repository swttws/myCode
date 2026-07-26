from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any, TYPE_CHECKING

from mycode.mcp.models import MCPServerState
from mycode.permission.models import RuleSource
from mycode.slash.models import (
    GitStatusSnapshot,
    MCPServerStatus,
    MCPStatusSnapshot,
    PermissionStatusSnapshot,
    SlashMode,
    StatusSection,
)

if TYPE_CHECKING:
    from collections.abc import Iterable


def collect_git_status(
    workspace_root: str | Path,
    *,
    timeout_seconds: float = 2.0,
) -> StatusSection[GitStatusSnapshot]:
    workspace_path = Path(workspace_root)
    if not workspace_path.exists() or not workspace_path.is_dir():
        return StatusSection(
            value=GitStatusSnapshot(
                is_repository=False,
                repository_root=None,
                branch=None,
                upstream=None,
                ahead=0,
                behind=0,
                staged=0,
                unstaged=0,
                untracked=0,
            )
        )

    root_result = _run_git_command(
        workspace_path,
        ["git", "rev-parse", "--show-toplevel"],
        timeout_seconds=timeout_seconds,
    )
    if root_result is None:
        return StatusSection(
            value=GitStatusSnapshot(
                is_repository=False,
                repository_root=None,
                branch=None,
                upstream=None,
                ahead=0,
                behind=0,
                staged=0,
                unstaged=0,
                untracked=0,
            )
        )

    status_result = _run_git_command(
        workspace_path,
        ["git", "status", "--porcelain=v2", "--branch", "--untracked-files=all"],
        timeout_seconds=timeout_seconds,
    )
    if status_result is None:
        return StatusSection(value=None, error="git_status_unavailable")

    branch = None
    upstream = None
    ahead = 0
    behind = 0
    staged = 0
    unstaged = 0
    untracked = 0

    for line in status_result.splitlines():
        if not line.startswith("# "):
            code = line[:1]
            if code in {"1", "2", "u"}:
                parts = line.split(" ", 2)
                if len(parts) > 1:
                    xy = parts[1]
                    if xy and xy[0] != ".":
                        staged += 1
                    if len(xy) > 1 and xy[1] != ".":
                        unstaged += 1
            elif code == "?":
                untracked += 1
            continue

        header = line[2:]
        if header.startswith("branch.head "):
            branch = header[len("branch.head ") :]
            continue
        if header.startswith("branch.upstream "):
            upstream = header[len("branch.upstream ") :]
            continue
        if header.startswith("branch.ab "):
            match = re.fullmatch(r"\+(\d+)\s+-(\d+)", header[len("branch.ab ") :])
            if match is not None:
                ahead = int(match.group(1))
                behind = int(match.group(2))

    return StatusSection(
        value=GitStatusSnapshot(
            is_repository=True,
            repository_root=root_result.strip() or str(workspace_path.resolve()),
            branch=branch,
            upstream=upstream,
            ahead=ahead,
            behind=behind,
            staged=staged,
            unstaged=unstaged,
            untracked=untracked,
        )
    )


def collect_mcp_status(pool: Any) -> StatusSection[MCPStatusSnapshot]:
    try:
        server_names = tuple(pool.server_names)
        diagnostics = tuple(getattr(pool, "diagnostics", ()))
        tools = tuple(getattr(pool, "tools", ()))
    except Exception:
        return StatusSection(value=None, error="mcp_unavailable")

    servers: list[MCPServerStatus] = []
    for server_name in server_names:
        try:
            state = pool.server_state(server_name)
            available = bool(pool.is_available(server_name))
            tool_count = sum(1 for tool in tools if getattr(tool, "server_name", None) == server_name)
            categories = tuple(
                _dedupe(
                    diagnostic.category
                    for diagnostic in diagnostics
                    if getattr(diagnostic, "server_name", None) == server_name
                )
            )
        except Exception as exc:
            state = MCPServerState.FAILED
            available = False
            tool_count = 0
            categories = (type(exc).__name__,)
        servers.append(
            MCPServerStatus(
                name=server_name,
                state=state,
                available=available,
                tool_count=tool_count,
                diagnostic_categories=categories,
            )
        )

    return StatusSection(value=MCPStatusSnapshot(servers=tuple(servers)))


def format_application_status(snapshot: Any) -> str:
    lines = [
        f"工作区：{_as_text(getattr(snapshot, 'workspace_root', None), default='未知')}",
        f"模式：{_format_mode(getattr(snapshot, 'mode', None))}",
        _prefix_lines("权限", _format_permission_section(getattr(snapshot, 'permission', None))),
        _prefix_lines("Token", _format_token_section(getattr(snapshot, 'token', None))),
        _prefix_lines("会话", _format_session_section(getattr(snapshot, 'session', None))),
        _prefix_lines("记忆", _format_memory_section(getattr(snapshot, 'memory', None))),
        _prefix_lines("Git", _format_git_section(getattr(snapshot, 'git', None))),
        _prefix_lines("MCP", _format_mcp_section(getattr(snapshot, 'mcp', None))),
    ]
    return "\n".join(line for line in lines if line)


def _format_permission_section(section: Any) -> str:
    value, error = _unwrap_section(section)
    if error is not None:
        return f"未知（{error}）"
    if value is None:
        return "未知"
    mode = _enum_value(getattr(value, "mode", None), default="unknown")
    source = _enum_value(getattr(value, "source", None), default="unknown")
    return f"{mode} ({source})"


def _format_token_section(section: Any) -> str:
    value, error = _unwrap_section(section)
    if error is not None:
        return f"未知（{error}）"
    if value is None:
        return "未知"
    tokens = _int_attr(value, "estimated_tokens", "tokens", default=0)
    window = _int_attr(value, "context_window_tokens", "window_tokens", default=0)
    ratio = _float_attr(value, "usage_ratio", "ratio", default=None)
    if ratio is None and window > 0:
        ratio = tokens / window
    source = _enum_value(getattr(value, "source", None), default="unknown")
    ratio_text = f"{ratio:.1%}" if ratio is not None else "未知"
    window_text = str(window) if window else "未知"
    return f"{tokens} / {window_text} ({ratio_text}) [{source}]"


def _format_session_section(section: Any) -> str:
    value, error = _unwrap_section(section)
    if error is not None:
        return f"未知（{error}）"
    if value is None:
        return "未知"
    session_id = _as_text(getattr(value, "session_id", None), default="未知")
    message_count = _int_attr(value, "message_count", default=0)
    source = _as_text(getattr(value, "source", None), default="unknown")
    restored_from = _as_text(getattr(value, "restored_from_session_id", None), default="")
    updated_at = _as_text(getattr(value, "updated_at", None), default="未知")
    source_text = source if not restored_from else f"{source} ({restored_from})"
    return (
        f"{session_id}\n"
        f"消息数：{message_count}\n"
        f"来源：{source_text}\n"
        f"最近更新时间：{updated_at}"
    )


def _format_memory_section(section: Any) -> str:
    value, error = _unwrap_section(section)
    if error is not None:
        return f"未知（{error}）"
    if value is None:
        return "未知"
    user = getattr(value, "user", None)
    project = getattr(value, "project", None)
    return "\n".join(
        [
            _format_memory_scope("用户", user),
            _format_memory_scope("项目", project),
        ]
    )


def _format_memory_scope(label: str, scope: Any) -> str:
    if scope is None:
        return f"{label}：未知"
    path = _as_text(getattr(scope, "path", None), default="未知")
    note_count = _int_attr(scope, "note_count", default=0)
    line_count = _int_attr(scope, "index_line_count", "line_count", default=0)
    byte_count = _int_attr(scope, "index_byte_count", "byte_count", default=0)
    diagnostic_source = getattr(scope, "diagnostic_codes", getattr(scope, "diagnostics", ()))
    diagnostics = tuple(_textify(item) for item in diagnostic_source if _textify(item))
    diagnostics_text = ", ".join(diagnostics) if diagnostics else "无"
    return (
        f"{label}：{path}\n"
        f"  笔记数：{note_count}\n"
        f"  索引：{line_count} 行 / {byte_count} 字节\n"
        f"  最近诊断：{diagnostics_text}"
    )


def _format_git_section(section: Any) -> str:
    value, error = _unwrap_section(section)
    if error is not None:
        return f"未知（{error}）"
    if value is None:
        return "未知"
    if not getattr(value, "is_repository", False):
        return "非 Git 目录"
    repository_root = _as_text(getattr(value, "repository_root", None), default="未知")
    branch = _as_text(getattr(value, "branch", None), default="detached")
    upstream = _as_text(getattr(value, "upstream", None), default="无")
    ahead = _int_attr(value, "ahead", default=0)
    behind = _int_attr(value, "behind", default=0)
    staged = _int_attr(value, "staged", default=0)
    unstaged = _int_attr(value, "unstaged", default=0)
    untracked = _int_attr(value, "untracked", default=0)
    return (
        f"{repository_root}\n"
        f"  分支：{branch}\n"
        f"  上游：{upstream}\n"
        f"  ahead/behind：{ahead}/{behind}\n"
        f"  staged/unstaged/untracked：{staged}/{unstaged}/{untracked}"
    )


def _format_mcp_section(section: Any) -> str:
    value, error = _unwrap_section(section)
    if error is not None:
        return f"未知（{error}）"
    if value is None:
        return "未知"
    servers = tuple(getattr(value, "servers", ()))
    if not servers:
        return "无可用 MCP 服务"
    lines = []
    for server in servers:
        name = _as_text(getattr(server, "name", None), default="unknown")
        state = _enum_value(getattr(server, "state", None), default="unknown").upper()
        available = "yes" if getattr(server, "available", False) else "no"
        tool_count = _int_attr(server, "tool_count", default=0)
        diagnostics = tuple(
            _textify(item) for item in getattr(server, "diagnostic_categories", ()) if _textify(item)
        )
        diagnostics_text = ", ".join(diagnostics) if diagnostics else "无"
        lines.append(
            f"{name} | {state} | available={available} | tools={tool_count} | diagnostics={diagnostics_text}"
        )
    return "\n".join(lines)


def _format_mode(mode: Any) -> str:
    enum_value = _enum_value(mode, default="default")
    marker = "[PLAN]" if enum_value == SlashMode.PLAN.value else "[DEFAULT]"
    return marker


def _prefix_lines(label: str, text: str) -> str:
    lines = text.splitlines()
    if not lines:
        return f"{label}："
    return "\n".join([f"{label}：{lines[0]}", *[f"  {line}" for line in lines[1:]]])


def _unwrap_section(section: Any) -> tuple[Any | None, str | None]:
    if section is None:
        return None, None
    if hasattr(section, "value") and hasattr(section, "error"):
        error = getattr(section, "error")
        if error:
            return None, _textify(error)
        return getattr(section, "value"), None
    return section, None


def _run_git_command(workspace_path: Path, command: list[str], *, timeout_seconds: float) -> str | None:
    try:
        completed = subprocess.run(
            command,
            cwd=workspace_path,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None

    if completed.returncode != 0:
        return None
    return completed.stdout


def _dedupe(values: Iterable[str]) -> Iterable[str]:
    seen: set[str] = set()
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        yield value


def _enum_value(value: Any, *, default: str) -> str:
    if value is None:
        return default
    enum_value = getattr(value, "value", None)
    if isinstance(enum_value, str):
        return enum_value
    if isinstance(value, str):
        return value
    return str(value)


def _as_text(value: Any, *, default: str) -> str:
    if value is None:
        return default
    enum_value = getattr(value, "value", None)
    if isinstance(enum_value, str):
        return enum_value
    if isinstance(value, str):
        return value
    return str(value)


def _textify(value: Any) -> str:
    if value is None:
        return ""
    enum_value = getattr(value, "value", None)
    if isinstance(enum_value, str):
        return enum_value
    if isinstance(value, str):
        return value
    return str(value)


def _int_attr(value: Any, *names: str, default: int) -> int:
    for name in names:
        candidate = getattr(value, name, None)
        if isinstance(candidate, int) and not isinstance(candidate, bool):
            return candidate
    return default


def _float_attr(value: Any, *names: str, default: float | None) -> float | None:
    for name in names:
        candidate = getattr(value, name, None)
        if isinstance(candidate, (int, float)) and not isinstance(candidate, bool):
            return float(candidate)
    return default


__all__ = [
    "collect_git_status",
    "collect_mcp_status",
    "format_application_status",
]
