from __future__ import annotations

import fnmatch
import re
import unicodedata
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from mycode.permission.pathing import PathGuard, ToolPathError
from mycode.tool.base import (
    ToolArguments,
    ToolDefinition,
    ToolInvocationContext,
    ToolKind,
    ToolResult,
    ToolRuntimeScope,
    ToolWorkspaceScope,
)
from mycode.tool.cache import FileTextCache


_SYNONYM_GROUPS = (
    ("service", "svc", "服务"),
    ("config", "configuration", "settings", "setting", "配置", "设置"),
    ("user", "account", "member", "用户", "账号", "账户"),
    ("auth", "authentication", "authorization", "login", "认证", "授权", "登录"),
    ("repository", "repo", "storage", "仓库", "存储"),
    ("controller", "handler", "endpoint", "控制器", "处理器"),
    ("test", "tests", "spec", "测试"),
    ("document", "docs", "documentation", "文档"),
    ("error", "exception", "failure", "错误", "异常"),
)
_SYNONYM_CANONICAL = {
    alias: group[0]
    for group in _SYNONYM_GROUPS
    for alias in group
}
_SEARCH_FUZZY_THRESHOLD = 0.72
_SEARCH_TOKEN_PATTERN = re.compile(r"[a-z0-9]+|[\u4e00-\u9fff]+")


class ReadFileTool:
    def __init__(self, path_guard: PathGuard, cache: FileTextCache) -> None:
        self._path_guard = path_guard
        self._cache = cache

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="read_file",
            description="读取工作区内的 UTF-8 文本文件。",
            parameters={
                "type": "object",
                "description": "读取文件所需参数。",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "要读取的工作区内相对路径。",
                    }
                },
                "required": ["path"],
            },
            kind=ToolKind.READ,
            grant_arguments=("path",),
            runtime_scope=ToolRuntimeScope.TASK_LOCAL,
            workspace_scope=ToolWorkspaceScope.WORKSPACE_AWARE,
        )

    def execute(
        self,
        arguments: ToolArguments,
        context: ToolInvocationContext | None = None,
    ) -> ToolResult:
        try:
            _ensure_invocation_workspace(self._path_guard.workspace_root, context)
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
            description="向工作区内写入 UTF-8 文本文件，并自动创建父目录。",
            parameters={
                "type": "object",
                "description": "写入文件所需参数。",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "要写入的工作区内相对路径。",
                    },
                    "text": {
                        "type": "string",
                        "description": "要写入文件的文本内容。",
                    },
                },
                "required": ["path", "text"],
            },
            kind=ToolKind.WRITE,
            grant_arguments=("path",),
            runtime_scope=ToolRuntimeScope.TASK_LOCAL,
            workspace_scope=ToolWorkspaceScope.WORKSPACE_AWARE,
        )

    def execute(
        self,
        arguments: ToolArguments,
        context: ToolInvocationContext | None = None,
    ) -> ToolResult:
        try:
            _ensure_invocation_workspace(self._path_guard.workspace_root, context)
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
            description="仅当原文在文件中唯一出现时，替换对应文本。",
            parameters={
                "type": "object",
                "description": "改写文件所需参数。",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "要修改的工作区内相对路径。",
                    },
                    "old_text": {
                        "type": "string",
                        "description": "要替换的原始文本。",
                    },
                    "new_text": {
                        "type": "string",
                        "description": "替换后的新文本。",
                    },
                },
                "required": ["path", "old_text", "new_text"],
            },
            kind=ToolKind.WRITE,
            grant_arguments=("path",),
            runtime_scope=ToolRuntimeScope.TASK_LOCAL,
            workspace_scope=ToolWorkspaceScope.WORKSPACE_AWARE,
        )

    def execute(
        self,
        arguments: ToolArguments,
        context: ToolInvocationContext | None = None,
    ) -> ToolResult:
        try:
            _ensure_invocation_workspace(self._path_guard.workspace_root, context)
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
            description="按 glob、模糊匹配和常见同义词在工作区内查找文件。",
            parameters={
                "type": "object",
                "description": "查找文件所需参数。",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": "文件名、相对路径或 glob 查询，也支持错别字和常见同义词。",
                    },
                    "root": {
                        "type": "string",
                        "description": "可选搜索起始目录；省略或留空时递归搜索整个工作区。",
                    },
                },
                "required": ["pattern"],
            },
            kind=ToolKind.READ,
            grant_arguments=("root",),
            runtime_scope=ToolRuntimeScope.TASK_LOCAL,
            workspace_scope=ToolWorkspaceScope.WORKSPACE_AWARE,
        )

    def execute(
        self,
        arguments: ToolArguments,
        context: ToolInvocationContext | None = None,
    ) -> ToolResult:
        try:
            _ensure_invocation_workspace(self._path_guard.workspace_root, context)
            pattern = _required_str(arguments, "pattern")
            root = _resolve_search_root(self._path_guard, arguments)
            exact_matches = []
            fuzzy_candidates = []
            for candidate in sorted(root.rglob("*")):
                # 遍历结果可能在检查后被替换成链接，每个候选都要重新确认真实边界。
                path = self._path_guard.inspect(str(candidate)).resolved
                if not path.is_file():
                    continue
                relative = _relative_path(self._path_guard.workspace_root, path)
                if _matches_file_pattern(self._path_guard.workspace_root, path, pattern):
                    exact_matches.append(relative)
                else:
                    score = _search_match_score(relative, pattern)
                    if score >= _SEARCH_FUZZY_THRESHOLD:
                        fuzzy_candidates.append((score, relative))
            matches = (
                exact_matches
                if exact_matches
                else [
                    relative
                    for _score, relative in sorted(
                        fuzzy_candidates,
                        key=lambda item: (-item[0], item[1]),
                    )
                ]
            )
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
            description="在工作区内 UTF-8 文本文件中搜索字面量、模糊内容和常见同义词。",
            parameters={
                "type": "object",
                "description": "搜索代码所需参数。",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "要搜索的内容，也支持错别字、大小写差异和常见同义词。",
                    },
                    "root": {
                        "type": "string",
                        "description": "可选搜索起始目录；省略或留空时递归搜索整个工作区。",
                    },
                },
                "required": ["query"],
            },
            kind=ToolKind.READ,
            grant_arguments=("root",),
            runtime_scope=ToolRuntimeScope.TASK_LOCAL,
            workspace_scope=ToolWorkspaceScope.WORKSPACE_AWARE,
        )

    def execute(
        self,
        arguments: ToolArguments,
        context: ToolInvocationContext | None = None,
    ) -> ToolResult:
        try:
            _ensure_invocation_workspace(self._path_guard.workspace_root, context)
            query = _required_str(arguments, "query")
            root = _resolve_search_root(self._path_guard, arguments)
            exact_matches: list[dict[str, object]] = []
            fuzzy_matches: list[dict[str, object]] = []
            for candidate in sorted(root.rglob("*")):
                # 搜索在读取正文前复检候选，边界不确定时整次调用失败而不是静默跳过。
                path = self._path_guard.inspect(str(candidate)).resolved
                if not path.is_file():
                    continue
                exact, fuzzy = _search_file(self._path_guard.workspace_root, path, query)
                exact_matches.extend(exact)
                fuzzy_matches.extend(fuzzy)
            matches = exact_matches if exact_matches else fuzzy_matches
            return ToolResult(ok=True, tool_name=self.definition.name, content={"matches": matches})
        except Exception as exc:
            return _failure(self.definition.name, exc, {"query": arguments.get("query")})


def _required_str(arguments: ToolArguments, name: str) -> str:
    value = arguments.get(name)
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a string")
    return value


def _ensure_invocation_workspace(
    bound_root: Path,
    context: ToolInvocationContext | None,
) -> None:
    if context is None:
        return
    if bound_root.resolve() != context.workspace.root.resolve():
        raise ToolPathError("工具绑定工作区与调用工作区不一致。")


def _relative_path(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()


def _resolve_search_root(path_guard: PathGuard, arguments: ToolArguments) -> Path:
    value = arguments.get("root", ".")
    if value is None or (isinstance(value, str) and not value.strip()):
        value = "."
    if not isinstance(value, str):
        raise ValueError("root must be a string")
    return path_guard.resolve(value)


def _matches_file_pattern(workspace_root: Path, path: Path, pattern: str) -> bool:
    relative_path = _relative_path(workspace_root, path)
    if fnmatch.fnmatch(path.name, pattern) or fnmatch.fnmatch(relative_path, pattern):
        return True

    if Path(pattern).suffix:
        return relative_path.endswith(f"/{pattern}")

    # 兼容模型省略顶层目录或文件扩展名的文件查询。
    relative_stem = Path(relative_path).with_suffix("").as_posix()
    return relative_stem == pattern or relative_stem.endswith(f"/{pattern}")


def _failure(tool_name: str, exc: Exception, content: dict[str, Any]) -> ToolResult:
    return ToolResult(ok=False, tool_name=tool_name, content=content, error=str(exc))


def _normalize_search_text(value: str) -> str:
    text = unicodedata.normalize("NFKC", value)
    text = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", text)
    text = text.casefold().replace("\\", "/")
    for alias in sorted(_SYNONYM_CANONICAL, key=len, reverse=True):
        canonical = _SYNONYM_CANONICAL[alias]
        if alias == canonical:
            continue
        if re.fullmatch(r"[a-z0-9]+", alias):
            text = re.sub(
                rf"(?<![a-z0-9]){re.escape(alias)}(?![a-z0-9])",
                f" {canonical} ",
                text,
            )
        else:
            text = text.replace(alias, f" {canonical} ")
    text = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", " ", text)
    return " ".join(text.split())


def _search_tokens(value: str) -> list[str]:
    return _SEARCH_TOKEN_PATTERN.findall(_normalize_search_text(value))


def _token_similarity(query_token: str, candidate_token: str) -> float:
    if query_token == candidate_token:
        return 1.0
    if len(query_token) <= 2 or len(candidate_token) <= 2:
        return 0.0
    if query_token in candidate_token or candidate_token in query_token:
        return min(len(query_token), len(candidate_token)) / max(
            len(query_token), len(candidate_token)
        )
    return SequenceMatcher(None, query_token, candidate_token).ratio()


def _search_match_score(candidate: str, query: str) -> float:
    normalized_query = _normalize_search_text(query)
    normalized_candidate = _normalize_search_text(candidate)
    if not normalized_query or not normalized_candidate:
        return 0.0
    if normalized_query in normalized_candidate:
        return 1.0

    query_tokens = _search_tokens(query)
    candidate_tokens = _search_tokens(candidate)
    if not query_tokens or not candidate_tokens:
        return 0.0
    scores = [
        max(_token_similarity(query_token, candidate_token) for candidate_token in candidate_tokens)
        for query_token in query_tokens
    ]
    if any(score < _SEARCH_FUZZY_THRESHOLD for score in scores):
        return 0.0
    return sum(scores) / len(scores)


def _search_file(
    root: Path,
    path: Path,
    query: str,
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except UnicodeDecodeError:
        return [], []

    exact_matches = [
        {
            "path": _relative_path(root, path),
            "line_number": index,
            "line": line,
        }
        for index, line in enumerate(lines, start=1)
        if query in line
    ]
    fuzzy_matches = [
        {
            "path": _relative_path(root, path),
            "line_number": index,
            "line": line,
        }
        for index, line in enumerate(lines, start=1)
        if _search_match_score(line, query) >= _SEARCH_FUZZY_THRESHOLD
    ]
    return exact_matches, fuzzy_matches
