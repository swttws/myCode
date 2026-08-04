from __future__ import annotations

import inspect
from pathlib import Path
from typing import Protocol

from mycode.subagent.models import (
    AgentIsolationMode,
    AgentRoleDefinition,
    SubAgentKind,
    SubAgentLaunchRequest,
)
from mycode.workspace import (
    WorkspaceContext,
    WorkspaceKind,
    WorkspaceLease,
    WorkspacePreparation,
    WorkspaceTaskIdentity,
)
from mycode.worktree.models import WorktreeDispositionResult
from mycode.worktree.pathing import WorktreePathPolicy


class _GitHeadCapture(Protocol):
    def capture_head(self, repository_root: Path) -> str: ...


class _WorktreeManager(Protocol):
    def prepare(self, identity: WorkspaceTaskIdentity): ...
    def release(self, lease: WorkspaceLease): ...


class SubAgentIsolationCoordinator:
    def __init__(
        self,
        *,
        shared_workspace: WorkspaceContext,
        worktree_manager: _WorktreeManager | None = None,
        git: _GitHeadCapture | None = None,
        path_policy: WorktreePathPolicy | None = None,
    ) -> None:
        if shared_workspace.kind is not WorkspaceKind.SHARED:
            raise ValueError("shared_workspace must use WorkspaceKind.SHARED")
        self._shared_workspace = shared_workspace
        self._worktree_manager = worktree_manager
        self._git = git
        self._path_policy = path_policy or WorktreePathPolicy(
            repository_root=shared_workspace.repository_root
        )

    async def prepare(
        self,
        *,
        request: SubAgentLaunchRequest,
        role: AgentRoleDefinition | None,
        task_id: str,
        task_token: str,
    ) -> WorkspaceLease:
        if _uses_shared_workspace(request, role):
            return self._shared_lease()
        if self._worktree_manager is None or self._git is None:
            raise RuntimeError("worktree_isolation_unavailable")
        if role is None:
            raise RuntimeError("worktree_role_required")

        identity = self._identity_for(role=role, task_id=task_id, task_token=task_token)
        return await _maybe_await(self._worktree_manager.prepare(identity))

    async def release(
        self,
        lease: WorkspaceLease,
    ) -> WorktreeDispositionResult | None:
        if lease.context.kind is WorkspaceKind.SHARED:
            return None
        if self._worktree_manager is None:
            raise RuntimeError("worktree_isolation_unavailable")
        return await _maybe_await(self._worktree_manager.release(lease))

    def _shared_lease(self) -> WorkspaceLease:
        return WorkspaceLease(
            context=self._shared_workspace,
            preparation=WorkspacePreparation.SHARED,
            metadata_path=None,
            initialized_rules=(),
        )

    def _identity_for(
        self,
        *,
        role: AgentRoleDefinition,
        task_id: str,
        task_token: str,
    ) -> WorkspaceTaskIdentity:
        role_name = role.metadata.name
        relative_name = self._path_policy.validate_relative_name(f"{role_name}/{task_token}")
        branch_name = self._path_policy.validate_branch_name(
            f"{self._path_policy.branch_prefix}{relative_name}"
        )
        assert self._git is not None
        base_commit = self._git.capture_head(self._shared_workspace.repository_root)
        return WorkspaceTaskIdentity(
            repository_id=self._shared_workspace.repository_id,
            task_id=task_id,
            role_name=role_name,
            task_token=task_token,
            relative_name=relative_name,
            branch_name=branch_name,
            base_commit=base_commit,
        )


def _uses_shared_workspace(
    request: SubAgentLaunchRequest,
    role: AgentRoleDefinition | None,
) -> bool:
    if request.kind is SubAgentKind.FORK:
        return True
    if role is None:
        return True
    return role.metadata.isolation is AgentIsolationMode.SHARED


async def _maybe_await(value):
    if inspect.isawaitable(value):
        return await value
    return value
