from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path

import pytest

from mycode.compact import archive as archive_module
from mycode.compact.archive import ArchiveSession
from mycode.compact.estimator import TokenEstimator


def test_initialize_uses_resolved_workspace_hash_and_session_cache_directory(tmp_path):
    workspace = tmp_path / "workspace" / "nested" / ".."
    workspace.parent.mkdir(parents=True)
    home = tmp_path / "home"

    session = ArchiveSession(
        workspace,
        home=home,
        session_id="0b462542-2b6d-4cc4-9f6c-2a8ef43f7df8",
        clock=lambda: 100.0,
    )

    identity = str(workspace.resolve())
    workspace_hash = sha256(identity.encode("utf-8")).hexdigest()
    assert session.workspace_hash == workspace_hash
    assert session.session_dir == home / ".mycode" / "projects" / workspace_hash / "context" / session.session_id
    assert session.session_dir.is_dir()
    assert (session.session_dir / "session.lock").is_file()

    session.close()


def test_sessions_and_workspaces_are_isolated(tmp_path):
    home = tmp_path / "home"
    first_workspace = tmp_path / "first"
    second_workspace = tmp_path / "second"
    first_workspace.mkdir()
    second_workspace.mkdir()

    first = ArchiveSession(first_workspace, home=home, session_id="a" * 36, clock=lambda: 100.0)
    second = ArchiveSession(first_workspace, home=home, session_id="b" * 36, clock=lambda: 100.0)
    other_workspace = ArchiveSession(second_workspace, home=home, session_id="a" * 36, clock=lambda: 100.0)

    assert first.session_dir != second.session_dir
    assert first.session_dir != other_workspace.session_dir
    assert first.workspace_hash != other_workspace.workspace_hash

    first.close()
    second.close()
    other_workspace.close()


def test_initialize_removes_expired_inactive_session_directory(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    home = tmp_path / "home"
    first = ArchiveSession(workspace, home=home, session_id="old-session", clock=lambda: 0.0)
    old_directory = first.session_dir
    first.close()

    ArchiveSession(
        workspace,
        home=home,
        session_id="current-session",
        clock=lambda: 86_401.0,
    ).close()

    assert not old_directory.exists()


def test_initialize_retains_expired_active_session_directory(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    home = tmp_path / "home"
    active = ArchiveSession(workspace, home=home, session_id="active-session", clock=lambda: 0.0)
    active_directory = active.session_dir

    current = ArchiveSession(
        workspace,
        home=home,
        session_id="current-session",
        clock=lambda: 86_401.0,
    )

    assert active_directory.is_dir()

    current.close()
    active.close()


def test_transaction_commits_utf8_json_envelope_and_registers_path(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    session = ArchiveSession(workspace, home=tmp_path / "home", session_id="session")
    text = "tool output\n保留中文原文"

    transaction = session.begin()
    artifact = transaction.archive_text(kind="tool_result", text=text)

    artifact_path = Path(artifact.path)
    assert artifact == artifact.__class__(
        path=str(artifact_path),
        kind="tool_result",
        original_chars=len(text),
        estimated_tokens=TokenEstimator().estimate_text(text),
        sha256=sha256(text.encode("utf-8")).hexdigest(),
    )
    assert not artifact_path.exists()
    assert artifact_path not in session.allowed_paths

    transaction.commit()

    raw = artifact_path.read_bytes()
    assert "保留中文原文".encode("utf-8") in raw
    assert json.loads(raw.decode("utf-8")) == {
        "estimated_tokens": TokenEstimator().estimate_text(text),
        "kind": "tool_result",
        "original_chars": len(text),
        "sha256": sha256(text.encode("utf-8")).hexdigest(),
        "text": text,
    }
    assert artifact_path in session.allowed_paths

    session.close()


def test_transaction_rollback_removes_temporary_files_and_keeps_paths_unregistered(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    session = ArchiveSession(workspace, home=tmp_path / "home", session_id="session")

    transaction = session.begin()
    artifact = transaction.archive_text(kind="history", text="draft")

    transaction.rollback()

    assert not Path(artifact.path).exists()
    assert not list((session.session_dir / "tmp").glob("*"))
    assert session.allowed_paths == frozenset()

    session.close()


def test_transaction_write_failure_removes_partial_file_and_keeps_paths_unregistered(
    tmp_path,
    monkeypatch,
):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    session = ArchiveSession(workspace, home=tmp_path / "home", session_id="session")

    def fail_after_partial_write(self, temp_path, envelope):
        temp_path.parent.mkdir(parents=True, exist_ok=True)
        temp_path.write_text("partial", encoding="utf-8")
        raise OSError("disk full")

    monkeypatch.setattr(
        archive_module.ArchiveTransaction,
        "_write_envelope",
        fail_after_partial_write,
    )

    transaction = session.begin()
    with pytest.raises(OSError, match="disk full"):
        transaction.archive_text(kind="user_message", text="content")

    assert not list((session.session_dir / "tmp").glob("*"))
    assert session.allowed_paths == frozenset()

    session.close()
