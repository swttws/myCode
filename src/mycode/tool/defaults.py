from __future__ import annotations

from pathlib import Path

from mycode.permission.pathing import PathGuard
from mycode.tool.cache import FileTextCache
from mycode.tool.command import RunCommandTool
from mycode.tool.filesystem import (
    EditFileTool,
    FindFilesTool,
    ReadFileTool,
    SearchCodeTool,
    WriteFileTool,
)
from mycode.tool.registry import ToolRegistry


def create_default_tool_registry(
    workspace_root: str | Path,
    *,
    path_guard: PathGuard | None = None,
) -> ToolRegistry:
    path_guard = path_guard or PathGuard(workspace_root)
    # 默认注册中心复用同一个路径守卫和文本缓存，给读写改三类文件工具共享。
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
