from __future__ import annotations

from pathlib import Path


class ToolPathError(ValueError):
    """Raised when a tool path escapes the configured workspace."""


class PathGuard:
    def __init__(self, workspace_root: str | Path) -> None:
        self._workspace_root = Path(workspace_root).resolve()

    @property
    def workspace_root(self) -> Path:
        return self._workspace_root

    def resolve(self, path: str) -> Path:
        raw_path = Path(path)
        candidate = raw_path if raw_path.is_absolute() else self._workspace_root / raw_path
        resolved = candidate.resolve()
        try:
            resolved.relative_to(self._workspace_root)
        except ValueError as exc:
            raise ToolPathError(f"path is outside workspace: {path}") from exc
        return resolved
