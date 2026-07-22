from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path

import pytest

from mycode.compact import archive as archive_module
from mycode.compact.archive import ArchiveSession
from mycode.compact.estimator import TokenEstimator
from mycode.tool import ToolKind


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


def test_read_committed_artifact_returns_token_limited_slices(tmp_path):
    session, artifact, text = _committed_artifact(
        tmp_path,
        text=("abcd" * 200) + ("你好" * 50),
    )
    estimator = TokenEstimator()
    parts = []
    offset = 0

    while True:
        artifact_slice = session.read(artifact.path, offset=offset, max_tokens=17)
        parts.append(artifact_slice.text)
        assert estimator.estimate_text(artifact_slice.text) <= 17
        assert artifact_slice.next_offset > offset or artifact_slice.eof
        offset = artifact_slice.next_offset
        if artifact_slice.eof:
            break

    assert "".join(parts) == text
    assert offset == len(text)

    session.close()


def test_read_rejects_uncommitted_artifact_path(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    session = ArchiveSession(workspace, home=tmp_path / "home", session_id="session")
    artifact = session.begin().archive_text(kind="tool_result", text="secret")

    with pytest.raises(ValueError, match="未登记"):
        session.read(artifact.path)

    session.close()


def test_read_rejects_artifact_from_other_session(tmp_path):
    first, artifact, _ = _committed_artifact(tmp_path, text="secret")
    second = ArchiveSession(first.workspace, home=tmp_path / "home", session_id="other")

    with pytest.raises(ValueError, match="未登记"):
        second.read(artifact.path)

    second.close()
    first.close()


def test_read_rejects_traversal_syntax_even_when_it_resolves_to_registered_path(tmp_path):
    session, artifact, _ = _committed_artifact(tmp_path, text="secret")
    artifact_path = Path(artifact.path)
    traversed = artifact_path.parent / ".." / artifact_path.parent.name / artifact_path.name

    with pytest.raises(ValueError, match="路径穿越"):
        session.read(str(traversed))

    session.close()


def test_read_rejects_registered_path_replaced_by_symlink(tmp_path):
    session, artifact, _ = _committed_artifact(tmp_path, text="secret")
    artifact_path = Path(artifact.path)
    outside = tmp_path / "outside.json"
    outside.write_text('{"text":"leak"}', encoding="utf-8")
    artifact_path.unlink()
    try:
        artifact_path.symlink_to(outside)
    except OSError:
        pytest.skip("symlink creation is not available on this Windows setup")

    with pytest.raises(ValueError, match="符号链接|未登记"):
        session.read(artifact.path)

    session.close()


def test_read_rejects_registered_artifact_with_changed_body(tmp_path):
    session, artifact, _ = _committed_artifact(tmp_path, text="original")
    changed = "changed"
    Path(artifact.path).write_text(
        json.dumps(
            {
                "estimated_tokens": TokenEstimator().estimate_text(changed),
                "kind": "tool_result",
                "original_chars": len(changed),
                "sha256": sha256(changed.encode("utf-8")).hexdigest(),
                "text": changed,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="完整性"):
        session.read(artifact.path)

    session.close()


@pytest.mark.parametrize("offset", [-1, True, "0"])
def test_read_rejects_invalid_offset(tmp_path, offset):
    session, artifact, _ = _committed_artifact(tmp_path, text="secret")

    with pytest.raises(ValueError, match="offset"):
        session.read(artifact.path, offset=offset)

    session.close()


@pytest.mark.parametrize("max_tokens", [0, -1, True, "2", 2_001])
def test_read_rejects_invalid_max_tokens(tmp_path, max_tokens):
    session, artifact, _ = _committed_artifact(tmp_path, text="secret")

    with pytest.raises(ValueError, match="max_tokens"):
        session.read(artifact.path, max_tokens=max_tokens)

    session.close()


def test_read_compact_artifact_tool_is_read_only_and_returns_slice_content(tmp_path):
    session, artifact, text = _committed_artifact(tmp_path, text="abcdef")
    tool = archive_module.ReadCompactArtifactTool(session)

    assert tool.definition.name == "read_compact_artifact"
    assert tool.definition.kind is ToolKind.READ
    assert tool.definition.grant_arguments == ()

    result = tool.execute({"path": artifact.path, "offset": 0, "max_tokens": 1})

    assert result.ok is True
    assert result.tool_name == "read_compact_artifact"
    assert result.content["path"] == artifact.path
    assert text.startswith(result.content["text"])
    assert result.content["next_offset"] > 0
    assert result.content["eof"] is False

    session.close()


def _committed_artifact(tmp_path, *, text):
    workspace = tmp_path / "workspace"
    workspace.mkdir(exist_ok=True)
    session = ArchiveSession(
        workspace,
        home=tmp_path / "home",
        session_id=sha256(text.encode()).hexdigest()[:8],
    )
    transaction = session.begin()
    artifact = transaction.archive_text(kind="tool_result", text=text)
    transaction.commit()
    return session, artifact, text
