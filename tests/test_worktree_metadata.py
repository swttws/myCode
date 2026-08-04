from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from mycode.workspace import WorkspaceTaskIdentity
from mycode.worktree.metadata import WorktreeMetadataStore
from mycode.worktree.models import WorktreeError, WorktreeMetadata, WorktreePhase
from mycode.worktree.pathing import WorktreePathPolicy


def test_metadata_encode_writes_stable_schema_for_all_phases(tmp_path: Path):
    repo_root = _repo_root(tmp_path)
    store = _store(repo_root)

    for phase in WorktreePhase:
        metadata = _metadata(repo_root, phase=phase)
        path = store.write(metadata)
        payload = _payload(metadata)

        assert path == repo_root / ".worktrees" / ".metadata" / "general" / "task-000001.json"
        assert path.read_text(encoding="utf-8") == _canonical_json(payload)
        assert json.loads(path.read_text(encoding="utf-8"))["created_at"].endswith("Z")
        assert store.read_candidate(path) == metadata


@pytest.mark.parametrize(
    "mutate",
    [
        lambda payload: payload.update({"extra": True}),
        lambda payload: payload["identity"].update({"unknown": True}),
        lambda payload: payload.update({"schema_version": 2}),
        lambda payload: payload.update({"phase": "done"}),
        lambda payload: payload["identity"].update({"task_id": 123}),
        lambda payload: payload.update({"initialized_rules": ["ok", 3]}),
    ],
)
def test_metadata_decode_rejects_unknown_bad_types_and_invalid_schema(
    tmp_path: Path,
    mutate,
):
    repo_root = _repo_root(tmp_path)
    metadata = _metadata(repo_root)
    path = _metadata_path(repo_root)
    payload = _payload(metadata)
    mutate(payload)
    path.write_text(_canonical_json(payload), encoding="utf-8")

    with pytest.raises(WorktreeError, match="元数据"):
        _store(repo_root).read_candidate(path)


def test_metadata_decode_rejects_duplicate_fields(tmp_path: Path):
    repo_root = _repo_root(tmp_path)
    path = _metadata_path(repo_root)
    path.write_text('{"schema_version":1,"schema_version":1}\n', encoding="utf-8")

    with pytest.raises(WorktreeError, match="重复"):
        _store(repo_root).read_candidate(path)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda payload: payload.update({"created_at": "2026-01-01T00:00:00"}),
        lambda payload: payload.update({"workspace_root": "relative/worktree"}),
    ],
)
def test_metadata_decode_rejects_naive_datetime_and_relative_path(
    tmp_path: Path,
    mutate,
):
    repo_root = _repo_root(tmp_path)
    path = _metadata_path(repo_root)
    payload = _payload(_metadata(repo_root))
    mutate(payload)
    path.write_text(_canonical_json(payload), encoding="utf-8")

    with pytest.raises(WorktreeError, match="元数据"):
        _store(repo_root).read_candidate(path)


def test_metadata_size_limit_rejects_files_over_sixty_four_kib(tmp_path: Path):
    repo_root = _repo_root(tmp_path)
    path = _metadata_path(repo_root)
    path.write_text("x" * (64 * 1024 + 1), encoding="utf-8")

    with pytest.raises(WorktreeError, match="64 KiB"):
        _store(repo_root).read_candidate(path)


def test_metadata_decode_error_is_bounded_and_does_not_echo_json_or_reason(
    tmp_path: Path,
):
    repo_root = _repo_root(tmp_path)
    path = _metadata_path(repo_root)
    secret_reason = "sensitive retained reason " * 200
    payload = _payload(
        _metadata(
            repo_root,
            phase=WorktreePhase.RETAINED,
            retained_reasons=(secret_reason,),
        )
    )
    payload["phase"] = "invalid"
    path.write_text(_canonical_json(payload), encoding="utf-8")

    with pytest.raises(WorktreeError) as captured:
        _store(repo_root).read_candidate(path)

    error = captured.value
    assert error.path == path
    assert "元数据" in error.message
    assert "sensitive retained reason" not in error.message
    assert "{" not in error.message
    assert len(error.message.encode("utf-8")) <= 512


def test_read_ready_accepts_only_matching_ready_metadata(tmp_path: Path):
    repo_root = _repo_root(tmp_path)
    store = _store(repo_root)
    metadata = _metadata(repo_root)
    path = store.write(metadata)

    assert store.read_ready(
        metadata.identity,
        metadata.workspace_root,
        metadata.config_digest,
    ) == metadata
    assert path.exists()


def test_read_ready_does_not_call_write(monkeypatch, tmp_path: Path):
    repo_root = _repo_root(tmp_path)
    store = _store(repo_root)
    metadata = _metadata(repo_root)
    store.write(metadata)

    def fail_write(_metadata):
        raise AssertionError("read_ready must not write metadata")

    monkeypatch.setattr(store, "write", fail_write)

    assert store.read_ready(
        metadata.identity,
        metadata.workspace_root,
        metadata.config_digest,
    ) == metadata


@pytest.mark.parametrize(
    "mutate",
    [
        lambda payload: payload.update({"phase": "creating"}),
        lambda payload: payload.update({"config_digest": "c" * 64}),
        lambda payload: payload.update({"workspace_root": str(Path(payload["workspace_root"]).parent / "other")}),
        lambda payload: payload["identity"].update({"branch_name": "mycode/worktree/general/other"}),
    ],
)
def test_read_ready_rejects_phase_identity_target_and_digest_mismatch(
    tmp_path: Path,
    mutate,
):
    repo_root = _repo_root(tmp_path)
    metadata = _metadata(repo_root)
    path = _metadata_path(repo_root, metadata.identity)
    payload = _payload(metadata)
    mutate(payload)
    path.write_text(_canonical_json(payload), encoding="utf-8")

    with pytest.raises(WorktreeError, match="READY|不匹配"):
        _store(repo_root).read_ready(
            metadata.identity,
            metadata.workspace_root,
            metadata.config_digest,
        )


def test_read_candidate_accepts_all_phases_but_only_ready_can_recover(tmp_path: Path):
    repo_root = _repo_root(tmp_path)
    store = _store(repo_root)

    for phase in WorktreePhase:
        metadata = _metadata(repo_root, phase=phase)
        path = store.write(metadata)
        assert store.read_candidate(path).phase is phase
        if phase is not WorktreePhase.READY:
            with pytest.raises(WorktreeError, match="READY"):
                store.read_ready(metadata.identity, metadata.workspace_root, metadata.config_digest)


def test_scan_returns_sixty_four_candidates_in_stable_order(tmp_path: Path):
    repo_root = _repo_root(tmp_path)
    store = _store(repo_root)
    created: list[Path] = []
    for index in reversed(range(65)):
        identity = _identity(task_token=f"task-{index:03d}")
        metadata = _metadata(repo_root, identity=identity)
        created.append(store.write(metadata))

    assert store.scan(64) == tuple(sorted(created)[:64])


def test_remove_deletes_exact_sidecar_and_empty_parent_directories(tmp_path: Path):
    repo_root = _repo_root(tmp_path)
    store = _store(repo_root)
    first = _metadata(repo_root, identity=_identity(task_token="task-000001"))
    second = _metadata(repo_root, identity=_identity(task_token="task-000002"))
    first_path = store.write(first)
    second_path = store.write(second)

    store.remove(first.identity)

    assert not first_path.exists()
    assert second_path.exists()
    assert second_path.parent.exists()

    store.remove(second.identity)

    assert not second_path.exists()
    assert not second_path.parent.exists()
    assert (repo_root / ".worktrees" / ".metadata").exists()


def _repo_root(tmp_path: Path) -> Path:
    repo_root = tmp_path / "repo"
    (repo_root / ".worktrees" / ".metadata" / "general").mkdir(parents=True)
    return repo_root


def _store(repo_root: Path) -> WorktreeMetadataStore:
    return WorktreeMetadataStore(WorktreePathPolicy(repository_root=repo_root))


def _identity(
    repository_id: str = "repo-123",
    *,
    task_token: str = "task-000001",
    branch_name: str | None = None,
) -> WorkspaceTaskIdentity:
    relative_name = f"general/{task_token}"
    return WorkspaceTaskIdentity(
        repository_id=repository_id,
        task_id=task_token,
        role_name="general",
        task_token=task_token,
        relative_name=relative_name,
        branch_name=branch_name or f"mycode/worktree/{relative_name}",
        base_commit="a" * 40,
    )


def _metadata(
    repo_root: Path,
    *,
    identity: WorkspaceTaskIdentity | None = None,
    phase: WorktreePhase = WorktreePhase.READY,
    retained_reasons: tuple[str, ...] = (),
) -> WorktreeMetadata:
    identity = identity or _identity()
    return WorktreeMetadata(
        schema_version=1,
        phase=phase,
        repository_id=identity.repository_id,
        identity=identity,
        workspace_root=repo_root / ".worktrees" / Path(*identity.relative_name.split("/")),
        config_digest="b" * 64,
        created_at=datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc),
        last_active_at=datetime(2026, 1, 1, 0, 1, tzinfo=timezone.utc),
        initialized_rules=("0:copy",),
        retained_reasons=retained_reasons,
    )


def _metadata_path(repo_root: Path, identity: WorkspaceTaskIdentity | None = None) -> Path:
    identity = identity or _identity()
    relative = Path(*identity.relative_name.split("/"))
    return (repo_root / ".worktrees" / ".metadata" / relative).with_name(relative.name + ".json")


def _payload(metadata: WorktreeMetadata) -> dict[str, object]:
    return {
        "schema_version": metadata.schema_version,
        "phase": metadata.phase.value,
        "repository_id": metadata.repository_id,
        "identity": {
            "repository_id": metadata.identity.repository_id,
            "task_id": metadata.identity.task_id,
            "role_name": metadata.identity.role_name,
            "task_token": metadata.identity.task_token,
            "relative_name": metadata.identity.relative_name,
            "branch_name": metadata.identity.branch_name,
            "base_commit": metadata.identity.base_commit,
        },
        "workspace_root": str(metadata.workspace_root),
        "config_digest": metadata.config_digest,
        "created_at": "2026-01-01T00:00:00Z",
        "last_active_at": "2026-01-01T00:01:00Z",
        "initialized_rules": list(metadata.initialized_rules),
        "retained_reasons": list(metadata.retained_reasons),
    }


def _canonical_json(payload: dict[str, object]) -> str:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ) + "\n"
