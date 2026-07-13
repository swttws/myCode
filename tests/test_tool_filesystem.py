from pathlib import Path

import pytest

from mycode.tool.cache import FileTextCache
from mycode.tool.filesystem import (
    EditFileTool,
    FindFilesTool,
    ReadFileTool,
    SearchCodeTool,
    WriteFileTool,
)
from mycode.tool.pathing import PathGuard, ToolPathError


def test_path_guard_resolves_relative_path_inside_workspace(tmp_path):
    guard = PathGuard(tmp_path)

    assert guard.resolve("a/b.txt") == (tmp_path / "a" / "b.txt").resolve()


def test_path_guard_rejects_parent_traversal_outside_workspace(tmp_path):
    guard = PathGuard(tmp_path)

    with pytest.raises(ToolPathError, match="outside workspace"):
        guard.resolve("../outside.txt")


def test_path_guard_rejects_absolute_path_outside_workspace(tmp_path):
    guard = PathGuard(tmp_path)
    outside = tmp_path.parent / "outside.txt"

    with pytest.raises(ToolPathError, match="outside workspace"):
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
    assert "outside workspace" in result.error


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
    assert "outside workspace" in result.error


def test_read_and_write_file_tools_define_required_schema_fields(tmp_path):
    read_tool = ReadFileTool(PathGuard(tmp_path), FileTextCache())
    write_tool = WriteFileTool(PathGuard(tmp_path), FileTextCache())

    assert read_tool.definition.name == "read_file"
    assert read_tool.definition.parameters["required"] == ["path"]
    assert write_tool.definition.name == "write_file"
    assert write_tool.definition.parameters["required"] == ["path", "text"]


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
    assert "outside workspace" in result.error


def test_edit_file_tool_defines_required_schema_fields(tmp_path):
    tool = EditFileTool(PathGuard(tmp_path), FileTextCache())

    assert tool.definition.name == "edit_file"
    assert tool.definition.parameters["required"] == ["path", "old_text", "new_text"]


def test_find_files_tool_returns_matching_relative_paths(tmp_path):
    (tmp_path / "a.py").write_text("print('a')", encoding="utf-8")
    (tmp_path / "nested").mkdir()
    (tmp_path / "nested" / "b.py").write_text("print('b')", encoding="utf-8")
    (tmp_path / "notes.txt").write_text("notes", encoding="utf-8")
    tool = FindFilesTool(PathGuard(tmp_path))

    result = tool.execute({"pattern": "*.py"})

    assert result.ok is True
    assert result.content == {"matches": ["a.py", "nested/b.py"]}


def test_find_files_tool_rejects_root_outside_workspace(tmp_path):
    tool = FindFilesTool(PathGuard(tmp_path))

    result = tool.execute({"pattern": "*.py", "root": "../outside"})

    assert result.ok is False
    assert result.tool_name == "find_files"
    assert "outside workspace" in result.error


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


def test_find_and_search_tools_define_required_schema_fields(tmp_path):
    find_tool = FindFilesTool(PathGuard(tmp_path))
    search_tool = SearchCodeTool(PathGuard(tmp_path))

    assert find_tool.definition.name == "find_files"
    assert find_tool.definition.parameters["required"] == ["pattern"]
    assert search_tool.definition.name == "search_code"
    assert search_tool.definition.parameters["required"] == ["query"]
