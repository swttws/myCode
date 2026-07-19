from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


class ToolPathError(ValueError):
    """工具路径无法被确认位于工作区内。"""


@dataclass(frozen=True)
class GuardedPath:
    resolved: Path
    relative: str
    match_value: str


class PathGuard:
    def __init__(self, workspace_root: str | Path) -> None:
        try:
            self._workspace_root = Path(workspace_root).resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise ToolPathError("无法确认工作区根目录的真实路径") from exc
        if not self._workspace_root.is_dir():
            raise ToolPathError("工作区根目录不存在或不是目录")

    @property
    def workspace_root(self) -> Path:
        return self._workspace_root

    def inspect(self, path: str) -> GuardedPath:
        if not isinstance(path, str):
            raise ToolPathError("工具路径必须是字符串")

        raw_path = Path(path)
        candidate = raw_path if raw_path.is_absolute() else self._workspace_root / raw_path
        try:
            resolved = _resolve_through_existing_parent(candidate)
        except (OSError, RuntimeError) as exc:
            # 链接状态或真实路径无法确认时必须拒绝，避免检查失败反而扩大访问范围。
            raise ToolPathError("无法安全解析工具路径") from exc

        if not _is_within(self._workspace_root, resolved):
            raise ToolPathError(f"路径超出工作区范围: {path}")

        try:
            relative = resolved.relative_to(self._workspace_root).as_posix()
        except ValueError as exc:
            # commonpath 与相对路径必须同时确认边界，平台路径差异出现歧义时按拒绝处理。
            raise ToolPathError(f"路径超出工作区范围: {path}") from exc
        match_value = os.path.normcase(relative).replace("\\", "/")
        return GuardedPath(resolved=resolved, relative=relative, match_value=match_value)

    def resolve(self, path: str) -> Path:
        return self.inspect(path).resolved


def _resolve_through_existing_parent(candidate: Path) -> Path:
    if candidate.exists() or candidate.is_symlink():
        return candidate.resolve(strict=True)

    missing_parts: list[str] = []
    current = candidate
    while not current.exists() and not current.is_symlink():
        if current.parent == current:
            raise OSError("没有可验证的已存在父目录")
        missing_parts.append(current.name)
        current = current.parent

    resolved = current.resolve(strict=True)
    for part in reversed(missing_parts):
        resolved = resolved / part
    return resolved


def _is_within(root: Path, candidate: Path) -> bool:
    root_value = os.path.normcase(str(root))
    candidate_value = os.path.normcase(str(candidate))
    try:
        return os.path.commonpath((root_value, candidate_value)) == root_value
    except ValueError:
        return False
