from __future__ import annotations

import fnmatch
from pathlib import Path
from typing import Any

from mycode.tool.base import ToolArguments, ToolDefinition, ToolResult
from mycode.tool.cache import FileTextCache
from mycode.tool.pathing import PathGuard, ToolPathError


class ReadFileTool:
    def __init__(self, path_guard: PathGuard, cache: FileTextCache) -> None:
        self._path_guard = path_guard
        self._cache = cache

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="read_file",
            description="Read a UTF-8 text file from the current workspace.",
            parameters={
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
        )

    def execute(self, arguments: ToolArguments) -> ToolResult:
        try:
            path_value = _required_str(arguments, "path")
            path = self._path_guard.resolve(path_value)
            text = self._cache.read_text(path)
            return ToolResult(
                ok=True,
                tool_name=self.definition.name,
                content={"path": _relative_path(self._path_guard.workspace_root, path), "text": text},
            )
        except Exception as exc:
            return _failure(self.definition.name, exc, {"path": arguments.get("path")})


class WriteFileTool:
    def __init__(self, path_guard: PathGuard, cache: FileTextCache) -> None:
        self._path_guard = path_guard
        self._cache = cache

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="write_file",
            description="Write UTF-8 text to a file in the current workspace.",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "text": {"type": "string"},
                },
                "required": ["path", "text"],
            },
        )

    def execute(self, arguments: ToolArguments) -> ToolResult:
        try:
            path_value = _required_str(arguments, "path")
            text = _required_str(arguments, "text")
            path = self._path_guard.resolve(path_value)
            self._cache.write_text(path, text)
            return ToolResult(
                ok=True,
                tool_name=self.definition.name,
                content={
                    "path": _relative_path(self._path_guard.workspace_root, path),
                    "bytes": len(text.encode("utf-8")),
                },
            )
        except Exception as exc:
            return _failure(self.definition.name, exc, {"path": arguments.get("path")})


class EditFileTool:
    def __init__(self, path_guard: PathGuard, cache: FileTextCache) -> None:
        self._path_guard = path_guard
        self._cache = cache

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="edit_file",
            description="Replace text in a UTF-8 file when the original text appears exactly once.",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "old_text": {"type": "string"},
                    "new_text": {"type": "string"},
                },
                "required": ["path", "old_text", "new_text"],
            },
        )

    def execute(self, arguments: ToolArguments) -> ToolResult:
        try:
            path_value = _required_str(arguments, "path")
            old_text = _required_str(arguments, "old_text")
            new_text = _required_str(arguments, "new_text")
            path = self._path_guard.resolve(path_value)
            match_count, _ = self._cache.edit_text(path, old_text, new_text)
            content = {
                "path": _relative_path(self._path_guard.workspace_root, path),
                "match_count": match_count,
            }
            if match_count != 1:
                return ToolResult(
                    ok=False,
                    tool_name=self.definition.name,
                    content=content,
                    error=f"expected exactly one match, found {match_count}",
                )
            return ToolResult(ok=True, tool_name=self.definition.name, content=content)
        except Exception as exc:
            return _failure(self.definition.name, exc, {"path": arguments.get("path")})


class FindFilesTool:
    def __init__(self, path_guard: PathGuard) -> None:
        self._path_guard = path_guard

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="find_files",
            description="Find files in the current workspace using a glob-style pattern.",
            parameters={
                "type": "object",
                "properties": {
                    "pattern": {"type": "string"},
                    "root": {"type": "string"},
                },
                "required": ["pattern"],
            },
        )

    def execute(self, arguments: ToolArguments) -> ToolResult:
        try:
            pattern = _required_str(arguments, "pattern")
            root = self._path_guard.resolve(str(arguments.get("root", ".")))
            matches = [
                _relative_path(self._path_guard.workspace_root, path)
                for path in sorted(root.rglob("*"))
                if path.is_file() and fnmatch.fnmatch(path.name, pattern)
            ]
            return ToolResult(ok=True, tool_name=self.definition.name, content={"matches": matches})
        except Exception as exc:
            return _failure(self.definition.name, exc, {"pattern": arguments.get("pattern")})


class SearchCodeTool:
    def __init__(self, path_guard: PathGuard) -> None:
        self._path_guard = path_guard

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="search_code",
            description="Search UTF-8 text files in the current workspace for a literal query.",
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "root": {"type": "string"},
                },
                "required": ["query"],
            },
        )

    def execute(self, arguments: ToolArguments) -> ToolResult:
        try:
            query = _required_str(arguments, "query")
            root = self._path_guard.resolve(str(arguments.get("root", ".")))
            matches: list[dict[str, object]] = []
            for path in sorted(root.rglob("*")):
                if not path.is_file():
                    continue
                matches.extend(_search_file(self._path_guard.workspace_root, path, query))
            return ToolResult(ok=True, tool_name=self.definition.name, content={"matches": matches})
        except Exception as exc:
            return _failure(self.definition.name, exc, {"query": arguments.get("query")})


def _required_str(arguments: ToolArguments, name: str) -> str:
    value = arguments.get(name)
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a string")
    return value


def _relative_path(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()


def _failure(tool_name: str, exc: Exception, content: dict[str, Any]) -> ToolResult:
    return ToolResult(ok=False, tool_name=tool_name, content=content, error=str(exc))


def _search_file(root: Path, path: Path, query: str) -> list[dict[str, object]]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except UnicodeDecodeError:
        return []

    return [
        {
            "path": _relative_path(root, path),
            "line_number": index,
            "line": line,
        }
        for index, line in enumerate(lines, start=1)
        if query in line
    ]
