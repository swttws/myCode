from pathlib import Path

import pytest

from mycode.permission.pathing import PathGuard, ToolPathError
from mycode.tool.cache import FileTextCache
from mycode.tool.filesystem import (
    EditFileTool,
    FindFilesTool,
    ReadFileTool,
    SearchCodeTool,
    WriteFileTool,
)


def test_path_guard_resolves_relative_path_inside_workspace(tmp_path):
    guard = PathGuard(tmp_path)

    assert guard.resolve("a/b.txt") == (tmp_path / "a" / "b.txt").resolve()


def test_path_guard_rejects_parent_traversal_outside_workspace(tmp_path):
    guard = PathGuard(tmp_path)

    with pytest.raises(ToolPathError, match="工作区"):
        guard.resolve("../outside.txt")


def test_path_guard_rejects_absolute_path_outside_workspace(tmp_path):
    guard = PathGuard(tmp_path)
    outside = tmp_path.parent / "outside.txt"

    with pytest.raises(ToolPathError, match="工作区"):
        guard.resolve(str(outside))


def test_read_file_tool_reads_workspace_text(tmp_path):
    (tmp_path / "notes.txt").write_text("hello", encoding="utf-8")
    tool = ReadFileTool(PathGuard(tmp_path), FileTextCache())

    result = tool.execute({"path": "notes.txt"})

    assert result.ok is True
    assert result.tool_name == "read_file"
    assert result.content == {"path": "notes.txt", "text": "hello"}
    assert result.error is None


def test_read_file_tool_rejects_path_outside_workspace(tmp_path):
    tool = ReadFileTool(PathGuard(tmp_path), FileTextCache())

    result = tool.execute({"path": "../outside.txt"})

    assert result.ok is False
    assert result.tool_name == "read_file"
    assert "工作区" in result.error


def test_write_file_tool_writes_text_and_creates_parent(tmp_path):
    tool = WriteFileTool(PathGuard(tmp_path), FileTextCache())

    result = tool.execute({"path": "nested/notes.txt", "text": "fresh"})

    assert result.ok is True
    assert result.content == {"path": "nested/notes.txt", "bytes": len("fresh".encode("utf-8"))}
    assert (tmp_path / "nested" / "notes.txt").read_text(encoding="utf-8") == "fresh"


def test_write_file_tool_rejects_path_outside_workspace(tmp_path):
    tool = WriteFileTool(PathGuard(tmp_path), FileTextCache())

    result = tool.execute({"path": "../outside.txt", "text": "fresh"})

    assert result.ok is False
    assert result.tool_name == "write_file"
    assert "工作区" in result.error


def test_read_and_write_file_tools_define_required_schema_fields(tmp_path):
    read_tool = ReadFileTool(PathGuard(tmp_path), FileTextCache())
    write_tool = WriteFileTool(PathGuard(tmp_path), FileTextCache())

    assert read_tool.definition.name == "read_file"
    assert read_tool.definition.parameters["required"] == ["path"]
    assert write_tool.definition.name == "write_file"
    assert write_tool.definition.parameters["required"] == ["path", "text"]
    assert read_tool.definition.grant_arguments == ("path",)
    assert write_tool.definition.grant_arguments == ("path",)


def test_edit_file_tool_replaces_unique_text(tmp_path):
    (tmp_path / "notes.txt").write_text("hello world", encoding="utf-8")
    tool = EditFileTool(PathGuard(tmp_path), FileTextCache())

    result = tool.execute({"path": "notes.txt", "old_text": "world", "new_text": "tool"})

    assert result.ok is True
    assert result.content == {"path": "notes.txt", "match_count": 1}
    assert (tmp_path / "notes.txt").read_text(encoding="utf-8") == "hello tool"


def test_edit_file_tool_reports_zero_matches(tmp_path):
    (tmp_path / "notes.txt").write_text("hello world", encoding="utf-8")
    tool = EditFileTool(PathGuard(tmp_path), FileTextCache())

    result = tool.execute({"path": "notes.txt", "old_text": "missing", "new_text": "tool"})

    assert result.ok is False
    assert result.content == {"path": "notes.txt", "match_count": 0}
    assert "expected exactly one match" in result.error
    assert (tmp_path / "notes.txt").read_text(encoding="utf-8") == "hello world"


def test_edit_file_tool_reports_multiple_matches_without_writing(tmp_path):
    (tmp_path / "notes.txt").write_text("one two one", encoding="utf-8")
    tool = EditFileTool(PathGuard(tmp_path), FileTextCache())

    result = tool.execute({"path": "notes.txt", "old_text": "one", "new_text": "tool"})

    assert result.ok is False
    assert result.content == {"path": "notes.txt", "match_count": 2}
    assert "expected exactly one match" in result.error
    assert (tmp_path / "notes.txt").read_text(encoding="utf-8") == "one two one"


def test_edit_file_tool_rejects_path_outside_workspace(tmp_path):
    tool = EditFileTool(PathGuard(tmp_path), FileTextCache())

    result = tool.execute({"path": "../outside.txt", "old_text": "a", "new_text": "b"})

    assert result.ok is False
    assert result.tool_name == "edit_file"
    assert "工作区" in result.error


def test_edit_file_tool_defines_required_schema_fields(tmp_path):
    tool = EditFileTool(PathGuard(tmp_path), FileTextCache())

    assert tool.definition.name == "edit_file"
    assert tool.definition.parameters["required"] == ["path", "old_text", "new_text"]
    assert tool.definition.grant_arguments == ("path",)


def test_find_files_tool_returns_matching_relative_paths(tmp_path):
    (tmp_path / "a.py").write_text("print('a')", encoding="utf-8")
    (tmp_path / "nested").mkdir()
    (tmp_path / "nested" / "b.py").write_text("print('b')", encoding="utf-8")
    (tmp_path / "notes.txt").write_text("notes", encoding="utf-8")
    tool = FindFilesTool(PathGuard(tmp_path))

    result = tool.execute({"pattern": "*.py"})

    assert result.ok is True
    assert result.content == {"matches": ["a.py", "nested/b.py"]}


def test_find_files_tool_matches_file_stem_when_extension_is_omitted(tmp_path):
    (tmp_path / "nested").mkdir()
    (tmp_path / "nested" / "spec.md").write_text("specification", encoding="utf-8")
    tool = FindFilesTool(PathGuard(tmp_path))

    # 用户和模型常会省略文件扩展名，仍应能找到对应文件。
    result = tool.execute({"pattern": "spec"})

    assert result.ok is True
    assert result.content == {"matches": ["nested/spec.md"]}


def test_find_files_tool_matches_relative_path_without_leading_directory(tmp_path):
    (tmp_path / "doc" / "stage").mkdir(parents=True)
    (tmp_path / "doc" / "stage" / "spec.md").write_text("specification", encoding="utf-8")
    tool = FindFilesTool(PathGuard(tmp_path))

    # 顶层文档目录被省略时，仍应在工作区内递归匹配。
    result = tool.execute({"pattern": "stage/spec"})

    assert result.ok is True
    assert result.content == {"matches": ["doc/stage/spec.md"]}


def test_find_files_tool_rejects_root_outside_workspace(tmp_path):
    tool = FindFilesTool(PathGuard(tmp_path))

    result = tool.execute({"pattern": "*.py", "root": "../outside"})

    assert result.ok is False
    assert result.tool_name == "find_files"
    assert "工作区" in result.error


def test_search_code_tool_returns_matching_lines(tmp_path):
    (tmp_path / "a.py").write_text("alpha\nneedle here\n", encoding="utf-8")
    (tmp_path / "nested").mkdir()
    (tmp_path / "nested" / "b.txt").write_text("needle too\nother\n", encoding="utf-8")
    tool = SearchCodeTool(PathGuard(tmp_path))

    result = tool.execute({"query": "needle"})

    assert result.ok is True
    assert result.content == {
        "matches": [
            {"path": "a.py", "line_number": 2, "line": "needle here"},
            {"path": "nested/b.txt", "line_number": 1, "line": "needle too"},
        ]
    }


def test_search_code_tool_skips_non_utf8_files(tmp_path):
    (tmp_path / "good.txt").write_text("needle\n", encoding="utf-8")
    (tmp_path / "bad.bin").write_bytes(b"\xff\xfe")
    tool = SearchCodeTool(PathGuard(tmp_path))

    result = tool.execute({"query": "needle"})

    assert result.ok is True
    assert result.content == {
        "matches": [{"path": "good.txt", "line_number": 1, "line": "needle"}]
    }


class RejectCandidateGuard(PathGuard):
    def __init__(self, workspace_root, rejected_name):
        super().__init__(workspace_root)
        self.rejected_name = rejected_name
        self.inspected = []

    def inspect(self, path):
        guarded = super().inspect(path)
        self.inspected.append(guarded.relative)
        if guarded.resolved.name == self.rejected_name:
            raise ToolPathError("候选文件已越过工作区边界")
        return guarded


def test_find_files_rechecks_every_candidate_and_fails_closed(tmp_path):
    (tmp_path / "allowed.txt").write_text("ok", encoding="utf-8")
    (tmp_path / "blocked.txt").write_text("secret", encoding="utf-8")
    guard = RejectCandidateGuard(tmp_path, "blocked.txt")

    result = FindFilesTool(guard).execute({"pattern": "*.txt"})

    assert result.ok is False
    assert "候选文件" in result.error
    assert "blocked.txt" in guard.inspected


def test_search_code_rechecks_candidate_before_reading(tmp_path, monkeypatch):
    blocked = tmp_path / "blocked.txt"
    blocked.write_text("needle secret", encoding="utf-8")
    guard = RejectCandidateGuard(tmp_path, "blocked.txt")
    original_read_text = Path.read_text

    def fail_if_blocked(self, *args, **kwargs):
        if self.name == "blocked.txt":
            raise AssertionError("越界候选不应被读取")
        return original_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", fail_if_blocked)

    result = SearchCodeTool(guard).execute({"query": "needle"})

    assert result.ok is False
    assert "候选文件" in result.error
    assert "blocked.txt" in guard.inspected


def test_find_and_search_tools_define_required_schema_fields(tmp_path):
    find_tool = FindFilesTool(PathGuard(tmp_path))
    search_tool = SearchCodeTool(PathGuard(tmp_path))

    assert find_tool.definition.name == "find_files"
    assert find_tool.definition.parameters["required"] == ["pattern"]
    assert search_tool.definition.name == "search_code"
    assert search_tool.definition.parameters["required"] == ["query"]
    assert find_tool.definition.grant_arguments == ("root",)
    assert search_tool.definition.grant_arguments == ("root",)
