from __future__ import annotations

from mycode.workspace import WorkspaceKind, WorkspaceLease
from mycode.worktree.git import GitWorktreeGateway
from mycode.worktree.models import WorktreeProtectionStatus


class WorktreeProtectionInspector:
    def __init__(self, *, git: GitWorktreeGateway) -> None:
        self._git = git

    def inspect(self, lease: WorkspaceLease) -> WorktreeProtectionStatus:
        if not isinstance(lease, WorkspaceLease):
            raise ValueError("lease must be a WorkspaceLease")
        context = lease.context
        if context.kind is not WorkspaceKind.WORKTREE or context.task_identity is None:
            raise ValueError("protection inspection requires a worktree lease")

        root = context.root
        status = self._git.status(root)
        has_uncommitted = (
            status.has_staged_changes
            or status.has_unstaged_changes
            or bool(status.untracked_paths)
        )
        branch_tip = self._git.capture_head(root)
        upstream = self._git.upstream(root)
        if upstream is None:
            has_unpushed = branch_tip != context.task_identity.base_commit
        else:
            has_unpushed = bool(self._git.commits_not_in_upstream(root, upstream))

        reasons: list[str] = []
        if has_uncommitted:
            reasons.append("未提交修改")
        if has_unpushed:
            reasons.append("未推送提交")

        return WorktreeProtectionStatus(
            has_uncommitted_changes=has_uncommitted,
            has_unpushed_commits=has_unpushed,
            branch_tip=branch_tip,
            upstream=upstream,
            reasons=tuple(reasons),
        )
