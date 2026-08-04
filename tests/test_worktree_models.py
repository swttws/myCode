from dataclasses import FrozenInstanceError, is_dataclass
from datetime import datetime, timezone
import os
from pathlib import Path
import subprocess
import sys

import pytest

from mycode.workspace import WorkspaceTaskIdentity
from mycode.worktree.models import (
    CleanupBatchResult,
    GitStatus,
    GitWorktreeEntry,
    InitializationResult,
    RepositoryIdentity,
    WorktreeConfig,
    WorktreeDiagnostic,
    WorktreeDisposition,
    WorktreeDispositionResult,
    WorktreeError,
    WorktreeInitRule,
    WorktreeMetadata,
    WorktreePhase,
    WorktreeProtectionStatus,
    WorktreeRuleType,
)


def _identity() -> WorkspaceTaskIdentity:
    return WorkspaceTaskIdentity(
        repository_id="repo-123",
        task_id="task-000001",
        role_name="general",
        task_token="task-000001",
        relative_name="general/task-000001",
        branch_name="mycode/worktree/general/task-000001",
        base_commit="0123456789abcdef0123456789abcdef01234567",
    )


def test_worktree_enums_use_stable_wire_values():
    assert WorktreeRuleType.COPY.value == "copy"
    assert WorktreeRuleType.IGNORED_COPY.value == "ignored_copy"
    assert WorktreeRuleType.SYMLINK.value == "symlink"
    assert WorktreeRuleType.HOOKS.value == "hooks"
    assert WorktreePhase.CREATING.value == "creating"
    assert WorktreePhase.READY.value == "ready"
    assert WorktreePhase.RETAINED.value == "retained"
    assert WorktreeDisposition.DELETED.value == "deleted"
    assert WorktreeDisposition.RETAINED.value == "retained"
    assert WorktreeDisposition.SKIPPED.value == "skipped"
    assert WorktreeDisposition.FAILED.value == "failed"


def test_worktree_config_defaults_and_bounds_are_strict():
    rule = WorktreeInitRule(
        type=WorktreeRuleType.COPY,
        source=".env.example",
        target=".env",
    )
    config = WorktreeConfig(rules=(rule,), digest="abc123")

    assert config.git_timeout_seconds == 30.0
    assert config.cleanup_interval_seconds == 3600.0
    assert config.expire_after_seconds == 604800.0
    assert config.scan_batch_size == 64
    assert config.rules == (rule,)

    with pytest.raises(FrozenInstanceError):
        config.digest = "changed"

    with pytest.raises(ValueError, match="digest"):
        WorktreeConfig(rules=(), digest="")
    with pytest.raises(ValueError, match="git_timeout_seconds"):
        WorktreeConfig(rules=(), digest="abc123", git_timeout_seconds=0)
    with pytest.raises(ValueError, match="git_timeout_seconds"):
        WorktreeConfig(rules=(), digest="abc123", git_timeout_seconds=121)
    with pytest.raises(ValueError, match="cleanup_interval_seconds"):
        WorktreeConfig(rules=(), digest="abc123", cleanup_interval_seconds=0)
    with pytest.raises(ValueError, match="expire_after_seconds"):
        WorktreeConfig(rules=(), digest="abc123", expire_after_seconds=-1)
    with pytest.raises(ValueError, match="scan_batch_size"):
        WorktreeConfig(rules=(), digest="abc123", scan_batch_size=65)


def test_worktree_value_models_are_frozen_and_validate_field_combinations(tmp_path: Path):
    identity = _identity()
    now = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
    repo = RepositoryIdentity(
        root=tmp_path,
        common_dir=tmp_path / ".git",
        repository_id=identity.repository_id,
    )
    entry = GitWorktreeEntry(
        path=tmp_path / ".worktrees" / "general" / "task-000001",
        head=identity.base_commit,
        branch=identity.branch_name,
        locked=False,
        prunable=False,
    )
    status = GitStatus(
        has_staged_changes=True,
        has_unstaged_changes=False,
        untracked_paths=("scratch.txt",),
    )
    metadata = WorktreeMetadata(
        schema_version=1,
        phase=WorktreePhase.READY,
        repository_id=identity.repository_id,
        identity=identity,
        workspace_root=entry.path,
        config_digest="abc123",
        created_at=now,
        last_active_at=now,
        initialized_rules=("copy:.env",),
        retained_reasons=(),
    )
    initialized = InitializationResult(
        completed_rules=("copy:.env",),
        hooks_path=entry.path / ".git-hooks",
    )
    protection = WorktreeProtectionStatus(
        has_uncommitted_changes=True,
        has_unpushed_commits=False,
        branch_tip=identity.base_commit,
        upstream=None,
        reasons=("存在未提交修改",),
    )
    disposition = WorktreeDispositionResult(
        disposition=WorktreeDisposition.RETAINED,
        workspace_root=entry.path,
        branch_name=identity.branch_name,
        reasons=("存在未提交修改",),
    )
    diagnostic = WorktreeDiagnostic(
        code="protected_changes",
        phase="cleanup",
        message="存在未提交修改",
        path=entry.path,
        branch_name=identity.branch_name,
    )
    batch = CleanupBatchResult(
        scanned=1,
        deleted=0,
        retained=1,
        skipped=0,
        failed=0,
        has_more=False,
        diagnostics=(diagnostic,),
    )

    for model in (
        RepositoryIdentity,
        GitWorktreeEntry,
        GitStatus,
        WorktreeMetadata,
        InitializationResult,
        WorktreeProtectionStatus,
        WorktreeDispositionResult,
        WorktreeDiagnostic,
        CleanupBatchResult,
    ):
        assert is_dataclass(model)
        assert model.__dataclass_params__.frozen is True

    assert repo.common_dir == tmp_path / ".git"
    assert status.untracked_paths == ("scratch.txt",)
    assert metadata.identity is identity
    assert initialized.hooks_path == entry.path / ".git-hooks"
    assert protection.reasons == ("存在未提交修改",)
    assert disposition.disposition is WorktreeDisposition.RETAINED
    assert batch.diagnostics == (diagnostic,)

    with pytest.raises(FrozenInstanceError):
        metadata.phase = WorktreePhase.RETAINED
    with pytest.raises(ValueError, match="created_at"):
        WorktreeMetadata(
            schema_version=1,
            phase=WorktreePhase.READY,
            repository_id=identity.repository_id,
            identity=identity,
            workspace_root=entry.path,
            config_digest="abc123",
            created_at=datetime(2026, 1, 2, 3, 4, 5),
            last_active_at=now,
            initialized_rules=(),
            retained_reasons=(),
        )
    with pytest.raises(ValueError, match="workspace_root"):
        WorktreeMetadata(
            schema_version=1,
            phase=WorktreePhase.READY,
            repository_id=identity.repository_id,
            identity=identity,
            workspace_root=Path("relative"),
            config_digest="abc123",
            created_at=now,
            last_active_at=now,
            initialized_rules=(),
            retained_reasons=(),
        )
    with pytest.raises(ValueError, match="scanned"):
        CleanupBatchResult(
            scanned=-1,
            deleted=0,
            retained=0,
            skipped=0,
            failed=0,
            has_more=False,
            diagnostics=(),
        )


def test_worktree_error_preserves_bounded_public_fields(tmp_path: Path):
    error = WorktreeError(
        code="git_failed",
        phase="create",
        message="Git 命令失败",
        path=tmp_path,
        branch_name="mycode/worktree/general/task-000001",
        git_exit_code=128,
    )

    assert str(error) == "Git 命令失败"
    assert error.code == "git_failed"
    assert error.phase == "create"
    assert error.path == tmp_path
    assert error.branch_name == "mycode/worktree/general/task-000001"
    assert error.git_exit_code == 128

    with pytest.raises(ValueError, match="code"):
        WorktreeError(code="", phase="create", message="bad")


def test_worktree_package_exports_public_lifecycle_classes_without_subagent_import():
    script = (
        "import sys\n"
        "import mycode.worktree as worktree\n"
        "required = [\n"
        "    'WorktreeConfigLoader', 'WorktreePathPolicy', 'GitWorktreeGateway',\n"
        "    'WorktreeMetadataStore', 'WorktreeInitializer', 'WorktreeProtectionInspector',\n"
        "    'WorktreeManager', 'WorktreeCleaner', 'ActiveWorkspaceRegistry',\n"
        "]\n"
        "missing = [name for name in required if not hasattr(worktree, name)]\n"
        "duplicates = len(worktree.__all__) != len(set(worktree.__all__))\n"
        "private = [name for name in worktree.__all__ if name.startswith('_')]\n"
        "subagent_loaded = 'mycode.subagent' in sys.modules\n"
        "if missing or duplicates or private or subagent_loaded:\n"
        "    print({'missing': missing, 'duplicates': duplicates, 'private': private, 'subagent': subagent_loaded})\n"
        "    raise SystemExit(1)\n"
    )
    env = os.environ.copy()
    env["PYTHONPATH"] = "src"

    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=Path.cwd(),
        env=env,
        shell=False,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stdout + result.stderr
