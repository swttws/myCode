import asyncio
from pathlib import Path

import pytest

from mycode.permission.models import PermissionMode
from mycode.subagent.isolation import SubAgentIsolationCoordinator
from mycode.subagent.models import (
    AgentIsolationMode,
    AgentModelTier,
    AgentPermissionMode,
    AgentRoleDefinition,
    AgentRoleMetadata,
    AgentRoleSource,
    ParentAgentSnapshot,
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
from mycode.worktree.models import WorktreeDisposition, WorktreeDispositionResult


def test_coordinator_returns_shared_lease_for_fork_and_shared_defined_roles(tmp_path):
    async def scenario():
        shared_context = _shared_context(tmp_path)
        manager = RecordingWorktreeManager()
        coordinator = SubAgentIsolationCoordinator(
            shared_workspace=shared_context,
            worktree_manager=manager,
            git=FakeGit("a" * 40),
        )

        fork_lease = await coordinator.prepare(
            request=_request(kind=SubAgentKind.FORK, role_name=None),
            role=None,
            task_id="task-000001",
            task_token="token-1",
        )
        shared_lease = await coordinator.prepare(
            request=_request(role_name="general"),
            role=_role(isolation=AgentIsolationMode.SHARED),
            task_id="task-000002",
            task_token="token-2",
        )

        assert fork_lease.context == shared_context
        assert fork_lease.preparation is WorkspacePreparation.SHARED
        assert shared_lease.context == shared_context
        assert shared_lease.preparation is WorkspacePreparation.SHARED
        assert manager.prepared == []

    asyncio.run(scenario())


def test_coordinator_builds_safe_identity_and_prepares_worktree_for_defined_role(tmp_path):
    async def scenario():
        shared_context = _shared_context(tmp_path)
        manager = RecordingWorktreeManager()
        coordinator = SubAgentIsolationCoordinator(
            shared_workspace=shared_context,
            worktree_manager=manager,
            git=FakeGit("b" * 40),
        )

        lease = await coordinator.prepare(
            request=_request(role_name="review"),
            role=_role(name="review", isolation=AgentIsolationMode.WORKTREE),
            task_id="task-000123",
            task_token="task-000123",
        )

        identity = lease.context.task_identity
        assert identity == WorkspaceTaskIdentity(
            repository_id="repo-123",
            task_id="task-000123",
            role_name="review",
            task_token="task-000123",
            relative_name="review/task-000123",
            branch_name="mycode/worktree/review/task-000123",
            base_commit="b" * 40,
        )
        assert lease.preparation is WorkspacePreparation.CREATED
        assert manager.prepared == [identity]

    asyncio.run(scenario())


def test_coordinator_releases_only_worktree_leases(tmp_path):
    async def scenario():
        shared_context = _shared_context(tmp_path)
        manager = RecordingWorktreeManager()
        coordinator = SubAgentIsolationCoordinator(
            shared_workspace=shared_context,
            worktree_manager=manager,
            git=FakeGit("c" * 40),
        )
        shared_lease = WorkspaceLease(
            context=shared_context,
            preparation=WorkspacePreparation.SHARED,
            metadata_path=None,
            initialized_rules=(),
        )
        worktree_lease = await coordinator.prepare(
            request=_request(role_name="general"),
            role=_role(isolation=AgentIsolationMode.WORKTREE),
            task_id="task-000001",
            task_token="task-000001",
        )

        shared_result = await coordinator.release(shared_lease)
        worktree_result = await coordinator.release(worktree_lease)

        assert shared_result is None
        assert worktree_result.disposition is WorktreeDisposition.DELETED
        assert manager.released == [worktree_lease]

    asyncio.run(scenario())


def test_coordinator_rejects_worktree_role_without_manager(tmp_path):
    async def scenario():
        coordinator = SubAgentIsolationCoordinator(
            shared_workspace=_shared_context(tmp_path),
            worktree_manager=None,
            git=FakeGit("d" * 40),
        )

        with pytest.raises(RuntimeError, match="worktree_isolation_unavailable"):
            await coordinator.prepare(
                request=_request(role_name="general"),
                role=_role(isolation=AgentIsolationMode.WORKTREE),
                task_id="task-000001",
                task_token="task-000001",
            )

    asyncio.run(scenario())


class RecordingWorktreeManager:
    def __init__(self) -> None:
        self.prepared: list[WorkspaceTaskIdentity] = []
        self.released: list[WorkspaceLease] = []

    async def prepare(self, identity: WorkspaceTaskIdentity) -> WorkspaceLease:
        self.prepared.append(identity)
        root = Path(f"C:/repo/.worktrees/{identity.relative_name}")
        return WorkspaceLease(
            context=WorkspaceContext(
                kind=WorkspaceKind.WORKTREE,
                root=root,
                repository_root=Path("C:/repo"),
                repository_id=identity.repository_id,
                task_identity=identity,
                branch_name=identity.branch_name,
                hooks_path=None,
            ),
            preparation=WorkspacePreparation.CREATED,
            metadata_path=Path(f"C:/repo/.worktrees/.metadata/{identity.relative_name}.json"),
            initialized_rules=(),
        )

    async def release(self, lease: WorkspaceLease) -> WorktreeDispositionResult:
        self.released.append(lease)
        assert lease.context.branch_name is not None
        return WorktreeDispositionResult(
            disposition=WorktreeDisposition.DELETED,
            workspace_root=lease.context.root,
            branch_name=lease.context.branch_name,
            reasons=(),
        )


class FakeGit:
    def __init__(self, head: str) -> None:
        self.head = head

    def capture_head(self, repository_root: Path) -> str:
        assert repository_root == Path("C:/repo")
        return self.head


def _shared_context(tmp_path: Path) -> WorkspaceContext:
    return WorkspaceContext(
        kind=WorkspaceKind.SHARED,
        root=Path("C:/repo"),
        repository_root=Path("C:/repo"),
        repository_id="repo-123",
        task_identity=None,
        branch_name=None,
        hooks_path=None,
    )


def _role(
    *,
    name: str = "general",
    isolation: AgentIsolationMode,
) -> AgentRoleDefinition:
    return AgentRoleDefinition(
        metadata=AgentRoleMetadata(
            name=name,
            description="测试角色",
            allowed_tools=("*",),
            denied_tools=("Agent",),
            model=AgentModelTier.INHERIT,
            max_rounds=4,
            permission_mode=AgentPermissionMode.INHERIT,
            isolation=isolation,
        ),
        instruction="测试指令",
        source=AgentRoleSource.BUILTIN,
        entry_path=Path("general.md"),
        revision="rev",
    )


def _request(
    *,
    kind: SubAgentKind = SubAgentKind.DEFINED,
    role_name: str | None = "general",
) -> SubAgentLaunchRequest:
    return SubAgentLaunchRequest(
        kind=kind,
        task="任务",
        role_name=role_name if kind is SubAgentKind.DEFINED else None,
        requested_background=kind is SubAgentKind.FORK,
        parent=ParentAgentSnapshot(
            messages=(),
            tools=(),
            model_id="model",
            max_rounds=8,
            permission_mode=PermissionMode.DEFAULT,
        ),
    )
