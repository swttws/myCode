from __future__ import annotations

from pathlib import Path

from mycode.tool.cache import FileTextCache
from mycode.tool.command import RunCommandTool
from mycode.tool.filesystem import (
    EditFileTool,
    FindFilesTool,
    ReadFileTool,
    SearchCodeTool,
    WriteFileTool,
)
from mycode.tool.pathing import PathGuard
from mycode.tool.registry import ToolRegistry


def create_default_tool_registry(workspace_root: str | Path) -> ToolRegistry:
    path_guard = PathGuard(workspace_root)
    cache = FileTextCache()
    return ToolRegistry(
        [
            ReadFileTool(path_guard, cache),
            WriteFileTool(path_guard, cache),
            EditFileTool(path_guard, cache),
            RunCommandTool(path_guard.workspace_root),
            FindFilesTool(path_guard),
            SearchCodeTool(path_guard),
        ]
    )
