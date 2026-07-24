from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path
from typing import Callable, Sequence

from mycode.memory.models import (
    MemoryDiagnostic,
    MemoryIndexBundle,
    MemoryKind,
    MemoryNote,
    MemoryScope,
    NoteUpdateAction,
    NoteUpdateDecision,
    NoteUpdateResult,
)
from mycode.memory.paths import MemoryPaths


_INDEX_NAME = "index.md"
_MAX_INDEX_LINES = 200
_MAX_INDEX_BYTES = 25 * 1024


class MemoryNoteStore:
    def __init__(self, *, paths: MemoryPaths, now: Callable[[], object]) -> None:
        self._paths = paths
        self._now = now
        self._paths.user_memory_dir.mkdir(parents=True, exist_ok=True)
        self._paths.project_memory_dir.mkdir(parents=True, exist_ok=True)

    def load_notes(self, scope: MemoryScope) -> tuple[MemoryNote, ...]:
        notes: list[MemoryNote] = []
        for path in sorted(self._scope_dir(scope).glob("*.md"), key=lambda item: item.name):
            if path.name == _INDEX_NAME:
                continue
            note = _parse_note(path, expected_scope=scope)
            if note is not None:
                notes.append(note)
        return tuple(notes)

    def load_note_summaries(self, scope: MemoryScope) -> tuple[str, ...]:
        summaries: list[str] = []
        for note in self.load_notes(scope):
            title = note.frontmatter.get("title", note.note_id)
            summaries.append(
                f"- {title} ({note.kind.value}, id: {note.note_id}, updated: {note.updated_at})"
            )
        return tuple(summaries)

    def load_index_bundle(self, scope: MemoryScope) -> MemoryIndexBundle:
        path = self._scope_dir(scope) / _INDEX_NAME
        diagnostics: list[MemoryDiagnostic] = []
        if not path.exists():
            return MemoryIndexBundle(
                scope=scope,
                entries=(),
                rendered_text="",
                line_count=0,
                byte_count=0,
                truncated=False,
            )

        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError) as exc:
            return MemoryIndexBundle(
                scope=scope,
                entries=(),
                rendered_text="",
                line_count=0,
                byte_count=0,
                truncated=False,
                diagnostics=(
                    MemoryDiagnostic(
                        code="memory_index_unreadable",
                        message=str(exc),
                        scope=scope,
                        path=str(path),
                    ),
                ),
            )

        entries: list[str] = []
        truncated = False
        for line in lines:
            candidate_entries = entries + [line]
            candidate_text = "\n".join(candidate_entries)
            if len(candidate_entries) > _MAX_INDEX_LINES or len(candidate_text.encode("utf-8")) > _MAX_INDEX_BYTES:
                truncated = True
                break
            entries.append(line)

        rendered_text = "\n".join(entries)
        if truncated:
            diagnostics.append(
                MemoryDiagnostic(
                    code="memory_index_truncated",
                    message="memory index exceeded prompt budget",
                    scope=scope,
                    path=str(path),
                )
            )
        return MemoryIndexBundle(
            scope=scope,
            entries=tuple(entries),
            rendered_text=rendered_text,
            line_count=len(entries),
            byte_count=len(rendered_text.encode("utf-8")),
            truncated=truncated,
            diagnostics=tuple(diagnostics),
        )

    def apply_decisions(
        self,
        decisions: Sequence[NoteUpdateDecision],
        *,
        source_session_id: str | None,
    ) -> NoteUpdateResult:
        created = 0
        merged = 0
        updated = 0
        ignored = 0
        diagnostics: list[MemoryDiagnostic] = []

        for decision in decisions:
            if decision.action is NoteUpdateAction.IGNORE:
                ignored += 1
                continue

            invalid = _invalid_decision_reason(decision)
            if invalid is not None:
                diagnostics.append(
                    MemoryDiagnostic(
                        code="memory_note_invalid_decision",
                        message=invalid,
                        scope=decision.scope,
                    )
                )
                continue

            scope = decision.scope
            assert scope is not None
            directory = self._scope_dir(scope)
            snapshot = _snapshot_directory(directory)
            try:
                if decision.action is NoteUpdateAction.CREATE:
                    self._create_note(decision, source_session_id=source_session_id)
                    self._rebuild_index(scope)
                    created += 1
                elif decision.action is NoteUpdateAction.UPDATE:
                    if not self._replace_note_body(decision, source_session_id=source_session_id, merge=False):
                        diagnostics.append(_not_found_diagnostic(decision))
                        continue
                    self._rebuild_index(scope)
                    updated += 1
                elif decision.action is NoteUpdateAction.MERGE:
                    if not self._replace_note_body(decision, source_session_id=source_session_id, merge=True):
                        diagnostics.append(_not_found_diagnostic(decision))
                        continue
                    self._rebuild_index(scope)
                    merged += 1
            except OSError as exc:
                _restore_snapshot(directory, snapshot)
                diagnostics.append(
                    MemoryDiagnostic(
                        code="memory_note_write_failed",
                        message=str(exc),
                        scope=scope,
                        path=str(directory),
                    )
                )

        return NoteUpdateResult(
            created=created,
            merged=merged,
            updated=updated,
            ignored=ignored,
            diagnostics=tuple(diagnostics),
        )

    def _scope_dir(self, scope: MemoryScope) -> Path:
        if scope is MemoryScope.USER:
            return self._paths.user_memory_dir
        if scope is MemoryScope.PROJECT:
            return self._paths.project_memory_dir
        raise ValueError(f"unknown memory scope: {scope}")

    def _create_note(self, decision: NoteUpdateDecision, *, source_session_id: str | None) -> MemoryNote:
        assert decision.scope is not None
        assert decision.kind is not None
        assert decision.title is not None
        assert decision.body is not None

        note_id = _note_id(decision.title, decision.body)
        path = self._scope_dir(decision.scope) / f"{note_id}.md"
        frontmatter = {
            "id": note_id,
            "scope": decision.scope.value,
            "kind": decision.kind.value,
            "updated_at": self._timestamp(),
            "source_session_id": source_session_id or "",
            "title": decision.title,
        }
        _atomic_write_text(path, _render_note(frontmatter, decision.body))
        note = _parse_note(path, expected_scope=decision.scope)
        assert note is not None
        return note

    def _replace_note_body(
        self,
        decision: NoteUpdateDecision,
        *,
        source_session_id: str | None,
        merge: bool,
    ) -> bool:
        assert decision.scope is not None
        assert decision.kind is not None
        assert decision.target_note_id is not None
        assert decision.body is not None

        note = self._find_note(decision.scope, decision.target_note_id)
        if note is None:
            return False

        body = decision.body
        if merge:
            body = f"{note.body.rstrip()}\n\n---\n\n{decision.body.strip()}"
        frontmatter = dict(note.frontmatter)
        frontmatter["kind"] = decision.kind.value
        frontmatter["updated_at"] = self._timestamp()
        frontmatter["source_session_id"] = source_session_id or ""
        if decision.title:
            frontmatter["title"] = decision.title
        _atomic_write_text(Path(note.path), _render_note(frontmatter, body))
        return True

    def _find_note(self, scope: MemoryScope, note_id: str) -> MemoryNote | None:
        for note in self.load_notes(scope):
            if note.note_id == note_id:
                return note
        return None

    def _rebuild_index(self, scope: MemoryScope) -> None:
        notes = self.load_notes(scope)
        lines = []
        for note in notes:
            title = note.frontmatter.get("title", note.note_id)
            lines.append(f"- {title} ({note.kind.value}, id: {note.note_id}, updated: {note.updated_at})")
        # 索引注入有固定预算，先在文件层重建出确定顺序，注入前再按预算截断。
        _atomic_write_text(self._scope_dir(scope) / _INDEX_NAME, "\n".join(lines) + ("\n" if lines else ""))

    def _timestamp(self) -> str:
        value = self._now()
        if hasattr(value, "isoformat"):
            return value.isoformat()
        return str(value)


def _parse_note(path: Path, *, expected_scope: MemoryScope) -> MemoryNote | None:
    try:
        raw = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None

    frontmatter, body = _split_frontmatter(raw)
    if frontmatter is None:
        return None
    try:
        scope = MemoryScope(frontmatter["scope"])
        kind = MemoryKind(frontmatter["kind"])
        note_id = frontmatter["id"]
        updated_at = frontmatter["updated_at"]
    except (KeyError, ValueError):
        return None
    if scope is not expected_scope:
        return None

    source_session_id = frontmatter.get("source_session_id") or None
    return MemoryNote(
        note_id=note_id,
        scope=scope,
        kind=kind,
        path=str(path),
        frontmatter=frontmatter,
        body=body,
        updated_at=updated_at,
        source_session_id=source_session_id,
        sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
    )


def _split_frontmatter(raw: str) -> tuple[dict[str, str] | None, str]:
    lines = raw.splitlines()
    if not lines or lines[0].strip() != "---":
        return None, ""
    try:
        end_index = next(index for index in range(1, len(lines)) if lines[index].strip() == "---")
    except StopIteration:
        return None, ""

    frontmatter: dict[str, str] = {}
    for line in lines[1:end_index]:
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        frontmatter[key.strip()] = value.strip()
    body = "\n".join(lines[end_index + 1 :]).strip("\n")
    return frontmatter, body


def _render_note(frontmatter: dict[str, str], body: str) -> str:
    order = ("id", "scope", "kind", "updated_at", "source_session_id", "title")
    lines = ["---"]
    for key in order:
        if key in frontmatter:
            lines.append(f"{key}: {frontmatter[key]}")
    for key in sorted(set(frontmatter) - set(order)):
        lines.append(f"{key}: {frontmatter[key]}")
    lines.extend(["---", body.strip("\n")])
    return "\n".join(lines) + "\n"


def _note_id(title: str, body: str) -> str:
    slug = _slugify(title) or "note"
    digest = hashlib.sha256(f"{title}\0{body}".encode("utf-8")).hexdigest()[:8]
    return f"{slug}-{digest}"


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug[:48].strip("-")


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f".{path.name}.tmp")
    tmp_path.write_text(text, encoding="utf-8")
    os.replace(tmp_path, path)


def _invalid_decision_reason(decision: NoteUpdateDecision) -> str | None:
    if decision.action is NoteUpdateAction.CREATE:
        if decision.scope is None or decision.kind is None or not decision.title or not decision.body:
            return "create decision requires scope, kind, title, and body"
        return None
    if decision.action in (NoteUpdateAction.UPDATE, NoteUpdateAction.MERGE):
        if decision.scope is None or decision.kind is None or not decision.target_note_id or not decision.body:
            return "update and merge decisions require scope, kind, target_note_id, and body"
        return None
    return "unsupported note update action"


def _not_found_diagnostic(decision: NoteUpdateDecision) -> MemoryDiagnostic:
    return MemoryDiagnostic(
        code="memory_note_not_found",
        message="target note was not found",
        scope=decision.scope,
    )


def _snapshot_directory(directory: Path) -> dict[Path, bytes]:
    directory.mkdir(parents=True, exist_ok=True)
    return {path: path.read_bytes() for path in directory.glob("*.md")}


def _restore_snapshot(directory: Path, snapshot: dict[Path, bytes]) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    for path in directory.glob("*.md"):
        if path not in snapshot:
            path.unlink(missing_ok=True)
    for path in directory.glob("*.tmp"):
        path.unlink(missing_ok=True)
    for path, content in snapshot.items():
        path.write_bytes(content)
