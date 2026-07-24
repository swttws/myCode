from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from mycode.memory.models import MemoryKind, MemoryScope
from mycode.memory.notes import MemoryNoteStore
from mycode.memory.paths import MemoryPaths
from mycode.memory.tools import ReadMemoryNoteTool
from mycode.tool import ToolKind


def _dt(year: int, month: int, day: int, hour: int = 0, minute: int = 0, second: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, second, tzinfo=timezone.utc)


def _make_paths(tmp_path: Path) -> tuple[MemoryPaths, Path, Path]:
    workspace = tmp_path / "workspace"
    home = tmp_path / "home"
    workspace.mkdir()
    home.mkdir()
    paths = MemoryPaths(workspace_root=workspace, home=home)
    paths.ensure_directories()
    return paths, workspace, home


def _write_note(path: Path, *, note_id: str, scope: MemoryScope, kind: MemoryKind, title: str, body: str) -> None:
    path.write_text(
        "\n".join(
            [
                "---",
                f"id: {note_id}",
                f"scope: {scope.value}",
                f"kind: {kind.value}",
                "updated_at: 2026-07-23T10:00:00+00:00",
                "source_session_id: session-1",
                f"title: {title}",
                "---",
                body,
            ]
        ),
        encoding="utf-8",
    )


def test_read_memory_note_tool_reads_note_by_scope_and_id(tmp_path):
    paths, _workspace, _home = _make_paths(tmp_path)
    _write_note(
        paths.project_memory_dir / "api-contract-abcd1234.md",
        note_id="api-contract-abcd1234",
        scope=MemoryScope.PROJECT,
        kind=MemoryKind.PROJECT_KNOWLEDGE,
        title="API contract",
        body="Project memory stores Markdown notes outside the workspace.",
    )
    store = MemoryNoteStore(paths=paths, now=lambda: _dt(2026, 7, 23, 11, 0, 0))
    tool = ReadMemoryNoteTool(store)

    result = tool.execute({"scope": "project", "note_id": "api-contract-abcd1234"})

    assert tool.definition.name == "read_memory_note"
    assert tool.definition.kind is ToolKind.READ
    assert tool.definition.grant_arguments == ()
    assert result.ok is True
    assert result.content == {
        "scope": "project",
        "note_id": "api-contract-abcd1234",
        "kind": "project_knowledge",
        "title": "API contract",
        "updated_at": "2026-07-23T10:00:00+00:00",
        "body": "Project memory stores Markdown notes outside the workspace.",
    }


def test_read_memory_note_tool_rejects_unknown_scope_or_note_id(tmp_path):
    paths, _workspace, _home = _make_paths(tmp_path)
    store = MemoryNoteStore(paths=paths, now=lambda: _dt(2026, 7, 23, 11, 0, 0))
    tool = ReadMemoryNoteTool(store)

    invalid_scope = tool.execute({"scope": "global", "note_id": "missing"})
    missing = tool.execute({"scope": "user", "note_id": "missing"})

    assert invalid_scope.ok is False
    assert invalid_scope.error == "scope must be 'user' or 'project'"
    assert missing.ok is False
    assert missing.error == "memory note not found"
