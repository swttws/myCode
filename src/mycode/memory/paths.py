from __future__ import annotations

import hashlib
import os
from pathlib import Path


class MemoryPathError(ValueError):
    """长期记忆路径无法确认位于允许范围内。"""


class MemoryPaths:
    def __init__(self, *, workspace_root: Path | str, home: Path | str) -> None:
        try:
            self._workspace_root = Path(workspace_root).resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise MemoryPathError("无法确认工作区根目录的真实路径") from exc
        if not self._workspace_root.is_dir():
            raise MemoryPathError("工作区根目录不存在或不是目录")

        try:
            self._home = Path(home).resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise MemoryPathError("无法确认用户主目录的真实路径") from exc
        if not self._home.is_dir():
            raise MemoryPathError("用户主目录不存在或不是目录")

        self._project_digest = hashlib.sha256(str(self._workspace_root).encode("utf-8")).hexdigest()
        self._mycode_root = self._home / ".mycode"
        self._project_store_root = self._mycode_root / "projects" / self._project_digest
        self._sessions_dir = self._project_store_root / "sessions"
        self._project_memory_dir = self._project_store_root / "memory"
        self._user_memory_dir = self._mycode_root / "memory"

    @property
    def workspace_root(self) -> Path:
        return self._workspace_root

    @property
    def home(self) -> Path:
        return self._home

    @property
    def project_digest(self) -> str:
        return self._project_digest

    @property
    def project_store_root(self) -> Path:
        return self._project_store_root

    @property
    def sessions_dir(self) -> Path:
        return self._sessions_dir

    @property
    def user_memory_dir(self) -> Path:
        return self._user_memory_dir

    @property
    def project_memory_dir(self) -> Path:
        return self._project_memory_dir

    def ensure_directories(self) -> None:
        # 只显式创建长期记忆目录，避免把 `.mewcode` 或其他旧命名意外带进来。
        self._mycode_root.mkdir(parents=True, exist_ok=True)
        self._project_store_root.mkdir(parents=True, exist_ok=True)
        self._sessions_dir.mkdir(parents=True, exist_ok=True)
        self._project_memory_dir.mkdir(parents=True, exist_ok=True)
        self._user_memory_dir.mkdir(parents=True, exist_ok=True)

    def validate_project_path(self, path: Path | str) -> Path:
        return self._validate_within_root(path, self._workspace_root, label="工作区")

    def validate_user_mycode_path(self, path: Path | str) -> Path:
        return self._validate_within_root(path, self._mycode_root, label="用户 myCode")

    def _validate_within_root(self, path: Path | str, root: Path, *, label: str) -> Path:
        if not isinstance(path, (str, Path)):
            raise MemoryPathError("路径必须是字符串或 Path 对象")

        raw_path = Path(path)
        candidate = raw_path if raw_path.is_absolute() else root / raw_path
        try:
            resolved = _resolve_through_existing_parent(candidate)
        except (OSError, RuntimeError) as exc:
            # 真实路径或符号链接无法安全确认时宁可拒绝，也不要把边界检查建立在猜测上。
            raise MemoryPathError(f"无法安全解析{label}路径") from exc

        if not _is_within(root, resolved):
            raise MemoryPathError(f"路径超出{label}范围: {path}")

        try:
            resolved.relative_to(root)
        except ValueError as exc:
            # commonpath 与相对路径必须同时确认边界，平台差异出现歧义时按拒绝处理。
            raise MemoryPathError(f"路径超出{label}范围: {path}") from exc
        return resolved


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

