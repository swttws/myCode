from __future__ import annotations

from pathlib import Path

import pytest

from mycode.workspace import (
    WorkspaceContext,
    WorkspaceKind,
    WorkspaceLease,
    WorkspacePreparation,
    WorkspaceTaskIdentity,
)
from mycode.worktree.models import GitStatus, WorktreeError, WorktreeProtectionStatus
from mycode.worktree.protection import WorktreeProtectionInspector


@pytest.mark.parametrize(
    ("status", "protected"),
    [
        (GitStatus(True, False, ()), True),
        (GitStatus(False, True, ()), True),
        (GitStatus(False, False, ("new.txt",)), True),
        (GitStatus(False, False, ()), False),
    ],
)
def test_protection_reports_uncommitted_changes_in_stable_fields(
    tmp_path: Path,
    status: GitStatus,
    protected: bool,
):
    git = FakeProtectionGit(status_result=status, head="a" * 40, upstream_result=None)
    result = WorktreeProtectionInspector(git=git).inspect(_lease(tmp_path))

    assert result == WorktreeProtectionStatus(
        has_uncommitted_changes=protected,
        has_unpushed_commits=False,
        branch_tip="a" * 40,
        upstream=None,
        reasons=("未提交修改",) if protected else (),
    )


@pytest.mark.parametrize(
    ("upstream", "commits", "head", "protected"),
    [
        ("origin/main", (), "a" * 40, False),
        ("origin/main", ("b" * 40,), "b" * 40, True),
        (None, (), "a" * 40, False),
        (None, (), "c" * 40, True),
    ],
)
def test_protection_reports_unpushed_commits_with_or_without_upstream(
    tmp_path: Path,
    upstream: str | None,
    commits: tuple[str, ...],
    head: str,
    protected: bool,
):
    git = FakeProtectionGit(
        status_result=GitStatus(False, False, ()),
        head=head,
        upstream_result=upstream,
        commits=commits,
    )

    result = WorktreeProtectionInspector(git=git).inspect(_lease(tmp_path))

    assert result.has_uncommitted_changes is False
    assert result.has_unpushed_commits is protected
    assert result.branch_tip == head
    assert result.upstream == upstream
    assert result.reasons == (("未推送提交",) if protected else ())


@pytest.mark.parametrize("method_name", ["status", "capture_head", "upstream", "commits"])
def test_protection_fails_closed_when_git_state_cannot_be_read(
    tmp_path: Path,
    method_name: str,
):
    git = FakeProtectionGit(
        status_result=GitStatus(False, False, ()),
        head="b" * 40,
        upstream_result="origin/main",
        commits=("b" * 40,),
        fail_method=method_name,
    )

    with pytest.raises(WorktreeError, match="boom"):
        WorktreeProtectionInspector(git=git).inspect(_lease(tmp_path))


class FakeProtectionGit:
    def __init__(
        self,
        *,
        status_result: GitStatus,
        head: str,
        upstream_result: str | None,
        commits: tuple[str, ...] = (),
        fail_method: str | None = None,
    ) -> None:
        self.status_result = status_result
        self.head = head
        self.upstream_result = upstream_result
        self.commits = commits
        self.fail_method = fail_method
        self.calls: list[str] = []

    def status(self, target: Path) -> GitStatus:
        self.calls.append("status")
        self._fail_if("status", target)
        return self.status_result

    def capture_head(self, target: Path) -> str:
        self.calls.append("capture_head")
        self._fail_if("capture_head", target)
        return self.head

    def upstream(self, target: Path) -> str | None:
        self.calls.append("upstream")
        self._fail_if("upstream", target)
        return self.upstream_result

    def commits_not_in_upstream(self, target: Path, upstream: str) -> tuple[str, ...]:
        self.calls.append("commits")
        self._fail_if("commits", target)
        return self.commits

    def _fail_if(self, method_name: str, target: Path) -> None:
        if self.fail_method == method_name:
            raise WorktreeError(
                code="git_failed",
                phase="git",
                message="boom",
                path=target,
            )


def _lease(tmp_path: Path) -> WorkspaceLease:
    identity = WorkspaceTaskIdentity(
        repository_id="repo-123",
        task_id="task-000001",
        role_name="general",
        task_token="task-000001",
        relative_name="general/task-000001",
        branch_name="mycode/worktree/general/task-000001",
        base_commit="a" * 40,
    )
    root = tmp_path / ".worktrees" / "general" / "task-000001"
    repository_root = tmp_path / "repo"
    root.mkdir(parents=True)
    repository_root.mkdir()
    return WorkspaceLease(
        context=WorkspaceContext(
            kind=WorkspaceKind.WORKTREE,
            root=root,
            repository_root=repository_root,
            repository_id=identity.repository_id,
            task_identity=identity,
            branch_name=identity.branch_name,
            hooks_path=None,
        ),
        preparation=WorkspacePreparation.CREATED,
        metadata_path=tmp_path / ".worktrees" / ".metadata" / "general" / "task-000001.json",
        initialized_rules=(),
    )
