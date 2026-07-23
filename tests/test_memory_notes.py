from __future__ import annotations

import hashlib
import os
from datetime import datetime, timezone
from pathlib import Path

import mycode.memory.notes as notes_module
from mycode.memory.models import MemoryKind, MemoryScope, NoteUpdateAction, NoteUpdateDecision
from mycode.memory.notes import MemoryNoteStore
from mycode.memory.paths import MemoryPaths


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


def test_memory_note_store_loads_frontmatter_and_keeps_scopes_isolated(tmp_path):
    paths, _workspace, _home = _make_paths(tmp_path)
    user_note = paths.user_memory_dir / "use-short-replies-abcd1234.md"
    project_note = paths.project_memory_dir / "api-contract-bbbb2222.md"
    _write_note(
        user_note,
        note_id="user-pref-1",
        scope=MemoryScope.USER,
        kind=MemoryKind.USER_PREFERENCE,
        title="Use short replies",
        body="Prefer concise implementation updates.",
    )
    _write_note(
        project_note,
        note_id="project-knowledge-1",
        scope=MemoryScope.PROJECT,
        kind=MemoryKind.PROJECT_KNOWLEDGE,
        title="API contract",
        body="Project memory stores Markdown notes.",
    )

    store = MemoryNoteStore(paths=paths, now=lambda: _dt(2026, 7, 23, 11, 0, 0))

    user_notes = store.load_notes(MemoryScope.USER)
    project_notes = store.load_notes(MemoryScope.PROJECT)
    user_summaries = store.load_note_summaries(MemoryScope.USER)

    assert len(user_notes) == 1
    assert user_notes[0].note_id == "user-pref-1"
    assert user_notes[0].scope is MemoryScope.USER
    assert user_notes[0].kind is MemoryKind.USER_PREFERENCE
    assert user_notes[0].body == "Prefer concise implementation updates."
    assert user_notes[0].sha256 == hashlib.sha256(user_note.read_bytes()).hexdigest()
    assert len(project_notes) == 1
    assert project_notes[0].note_id == "project-knowledge-1"
    assert "user-pref-1" in user_summaries[0]
    assert "project-knowledge-1" not in "\n".join(user_summaries)


def test_memory_note_store_loads_index_bundle_with_line_and_byte_limits(tmp_path):
    paths, _workspace, _home = _make_paths(tmp_path)
    lines = [f"- entry {index:03d} " + ("x" * 350) for index in range(120)]
    (paths.user_memory_dir / "index.md").write_text("\n".join(lines), encoding="utf-8")

    store = MemoryNoteStore(paths=paths, now=lambda: _dt(2026, 7, 23, 11, 0, 0))
    bundle = store.load_index_bundle(MemoryScope.USER)

    assert bundle.scope is MemoryScope.USER
    assert bundle.entries[0].startswith("- entry 000")
    assert bundle.line_count == len(bundle.entries)
    assert bundle.line_count <= 200
    assert bundle.byte_count <= 25 * 1024
    assert bundle.truncated is True
    assert bundle.diagnostics[0].code == "memory_index_truncated"
    assert bundle.rendered_text == "\n".join(bundle.entries)


def test_memory_note_store_applies_create_update_merge_ignore_and_rebuilds_index(tmp_path):
    paths, _workspace, _home = _make_paths(tmp_path)
    now_values = iter(
        [
            _dt(2026, 7, 23, 10, 0, 0),
            _dt(2026, 7, 23, 10, 1, 0),
            _dt(2026, 7, 23, 10, 2, 0),
        ]
    )
    store = MemoryNoteStore(paths=paths, now=lambda: next(now_values))

    create_result = store.apply_decisions(
        [
            NoteUpdateDecision(
                action=NoteUpdateAction.CREATE,
                scope=MemoryScope.USER,
                kind=MemoryKind.CORRECTION,
                title="Always use pathlib",
                body="Use pathlib for filesystem code.",
                reason="The user corrected this preference.",
            )
        ],
        source_session_id="session-1",
    )
    created_note = store.load_notes(MemoryScope.USER)[0]
    created_path = Path(created_note.path)

    update_result = store.apply_decisions(
        [
            NoteUpdateDecision(
                action=NoteUpdateAction.UPDATE,
                scope=MemoryScope.USER,
                kind=MemoryKind.CORRECTION,
                target_note_id=created_note.note_id,
                body="Use pathlib unless an existing local helper is clearer.",
                reason="Refined by follow-up.",
            )
        ],
        source_session_id="session-2",
    )
    merge_result = store.apply_decisions(
        [
            NoteUpdateDecision(
                action=NoteUpdateAction.MERGE,
                scope=MemoryScope.USER,
                kind=MemoryKind.CORRECTION,
                target_note_id=created_note.note_id,
                body="Do not introduce ad hoc string path parsing.",
                reason="Related correction.",
            ),
            NoteUpdateDecision(action=NoteUpdateAction.IGNORE, reason="No durable note needed."),
        ],
        source_session_id="session-3",
    )
    updated_note = store.load_notes(MemoryScope.USER)[0]
    index_text = (paths.user_memory_dir / "index.md").read_text(encoding="utf-8")

    assert create_result.created == 1
    assert update_result.updated == 1
    assert merge_result.merged == 1
    assert merge_result.ignored == 1
    assert created_note.note_id.startswith("always-use-pathlib-")
    assert Path(updated_note.path) == created_path
    assert updated_note.body == (
        "Use pathlib unless an existing local helper is clearer.\n\n---\n\n"
        "Do not introduce ad hoc string path parsing."
    )
    assert updated_note.frontmatter["source_session_id"] == "session-3"
    assert "Always use pathlib" in index_text
    assert created_note.note_id in index_text


def test_memory_note_store_rejects_invalid_decisions_without_writing(tmp_path):
    paths, _workspace, _home = _make_paths(tmp_path)
    store = MemoryNoteStore(paths=paths, now=lambda: _dt(2026, 7, 23, 10, 0, 0))

    result = store.apply_decisions(
        [
            NoteUpdateDecision(
                action=NoteUpdateAction.CREATE,
                scope=MemoryScope.USER,
                kind=MemoryKind.USER_PREFERENCE,
                title="Missing body",
            ),
            NoteUpdateDecision(
                action=NoteUpdateAction.UPDATE,
                scope=MemoryScope.USER,
                kind=MemoryKind.USER_PREFERENCE,
                body="No target note id.",
            ),
        ],
        source_session_id="session-1",
    )

    assert result.created == 0
    assert result.updated == 0
    assert len(result.diagnostics) == 2
    assert {diagnostic.code for diagnostic in result.diagnostics} == {"memory_note_invalid_decision"}
    assert store.load_notes(MemoryScope.USER) == ()


def test_memory_note_store_rolls_back_note_write_when_index_replace_fails(tmp_path, monkeypatch):
    paths, _workspace, _home = _make_paths(tmp_path)
    original_index = "- Existing note\n"
    (paths.project_memory_dir / "index.md").write_text(original_index, encoding="utf-8")
    store = MemoryNoteStore(paths=paths, now=lambda: _dt(2026, 7, 23, 10, 0, 0))
    original_replace = notes_module.os.replace

    def fail_index_replace(src: str | os.PathLike[str], dst: str | os.PathLike[str]) -> None:
        if Path(dst).name == "index.md":
            raise OSError("simulated index failure")
        original_replace(src, dst)

    monkeypatch.setattr(notes_module.os, "replace", fail_index_replace)

    result = store.apply_decisions(
        [
            NoteUpdateDecision(
                action=NoteUpdateAction.CREATE,
                scope=MemoryScope.PROJECT,
                kind=MemoryKind.PROJECT_KNOWLEDGE,
                title="Rollback candidate",
                body="This note should not survive the failed index write.",
            )
        ],
        source_session_id="session-1",
    )

    assert result.created == 0
    assert result.diagnostics[0].code == "memory_note_write_failed"
    assert (paths.project_memory_dir / "index.md").read_text(encoding="utf-8") == original_index
    assert store.load_notes(MemoryScope.PROJECT) == ()
