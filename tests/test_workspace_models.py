from dataclasses import FrozenInstanceError, fields, is_dataclass
from pathlib import Path

import pytest

from mycode.workspace import (
    WorkspaceContext,
    WorkspaceKind,
    WorkspaceLease,
    WorkspacePreparation,
    WorkspaceTaskIdentity,
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


def test_shared_workspace_context_rejects_worktree_identity_fields(tmp_path: Path):
    assert WorkspaceKind.SHARED.value == "shared"
    assert WorkspaceKind.WORKTREE.value == "worktree"

    with pytest.raises(ValueError, match="branch_name"):
        WorkspaceContext(
            kind=WorkspaceKind.SHARED,
            root=tmp_path,
            repository_root=tmp_path,
            repository_id="repo-123",
            task_identity=None,
            branch_name="mycode/worktree/general/task-000001",
            hooks_path=None,
        )

    with pytest.raises(ValueError, match="task_identity"):
        WorkspaceContext(
            kind=WorkspaceKind.SHARED,
            root=tmp_path,
            repository_root=tmp_path,
            repository_id="repo-123",
            task_identity=_identity(),
            branch_name=None,
            hooks_path=None,
        )


def test_worktree_context_requires_identity_branch_and_absolute_roots(tmp_path: Path):
    identity = _identity()

    with pytest.raises(ValueError, match="task_identity"):
        WorkspaceContext(
            kind=WorkspaceKind.WORKTREE,
            root=tmp_path,
            repository_root=tmp_path,
            repository_id="repo-123",
            task_identity=None,
            branch_name=identity.branch_name,
            hooks_path=None,
        )

    with pytest.raises(ValueError, match="branch_name"):
        WorkspaceContext(
            kind=WorkspaceKind.WORKTREE,
            root=tmp_path,
            repository_root=tmp_path,
            repository_id="repo-123",
            task_identity=identity,
            branch_name=None,
            hooks_path=None,
        )

    with pytest.raises(ValueError, match="root"):
        WorkspaceContext(
            kind=WorkspaceKind.WORKTREE,
            root=Path("relative-worktree"),
            repository_root=tmp_path,
            repository_id="repo-123",
            task_identity=identity,
            branch_name=identity.branch_name,
            hooks_path=None,
        )

    with pytest.raises(ValueError, match="repository_root"):
        WorkspaceContext(
            kind=WorkspaceKind.WORKTREE,
            root=tmp_path,
            repository_root=Path("relative-repo"),
            repository_id="repo-123",
            task_identity=identity,
            branch_name=identity.branch_name,
            hooks_path=None,
        )

    context = WorkspaceContext(
        kind=WorkspaceKind.WORKTREE,
        root=tmp_path / "worktree",
        repository_root=tmp_path,
        repository_id="repo-123",
        task_identity=identity,
        branch_name=identity.branch_name,
        hooks_path=tmp_path / "worktree" / ".git-hooks",
    )

    assert context.task_identity is identity
    assert context.branch_name == identity.branch_name
    assert context.hooks_path == tmp_path / "worktree" / ".git-hooks"


def test_workspace_lease_is_frozen_and_preserves_preparation(tmp_path: Path):
    for model in (WorkspaceTaskIdentity, WorkspaceContext, WorkspaceLease):
        assert is_dataclass(model)
        assert model.__dataclass_params__.frozen is True

    assert [field.name for field in fields(WorkspaceLease)] == [
        "context",
        "preparation",
        "metadata_path",
        "initialized_rules",
    ]
    assert WorkspacePreparation.SHARED.value == "shared"
    assert WorkspacePreparation.CREATED.value == "created"
    assert WorkspacePreparation.RECOVERED.value == "recovered"

    context = WorkspaceContext(
        kind=WorkspaceKind.SHARED,
        root=tmp_path,
        repository_root=tmp_path,
        repository_id="repo-123",
        task_identity=None,
        branch_name=None,
        hooks_path=None,
    )
    lease = WorkspaceLease(
        context=context,
        preparation=WorkspacePreparation.SHARED,
        metadata_path=None,
        initialized_rules=(),
    )

    assert lease.context is context
    assert lease.preparation is WorkspacePreparation.SHARED
    assert lease.metadata_path is None
    assert lease.initialized_rules == ()
    with pytest.raises(FrozenInstanceError):
        lease.preparation = WorkspacePreparation.CREATED
