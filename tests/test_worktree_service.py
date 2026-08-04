import asyncio
from datetime import datetime, timezone
from pathlib import Path

from mycode.workspace import WorkspaceContext, WorkspaceKind, WorkspacePreparation
from mycode.worktree.metadata import WorktreeMetadataStore
from mycode.worktree.models import (
    InitializationResult,
    RepositoryIdentity,
    WorktreeConfig,
    WorktreePhase,
    WorktreeProtectionStatus,
)
from mycode.worktree.pathing import WorktreePathPolicy
from mycode.worktree.service import WorktreeService


def test_service_builds_shared_lease_and_safe_task_identity(tmp_path: Path):
    service = _service(tmp_path, head="b" * 40)

    shared = service.shared_lease()
    identity = service.identity_for(
        role_name="review",
        task_id="task-000123",
        task_token="task-000123",
    )

    assert shared.context == service.shared_workspace
    assert shared.preparation is WorkspacePreparation.SHARED
    assert shared.metadata_path is None
    assert identity.repository_id == service.shared_workspace.repository_id
    assert identity.task_id == "task-000123"
    assert identity.role_name == "review"
    assert identity.task_token == "task-000123"
    assert identity.relative_name == "review/task-000123"
    assert identity.branch_name == "mycode/worktree/review/task-000123"
    assert identity.base_commit == "b" * 40


def test_service_prepare_accepts_role_task_fields_and_writes_ready_metadata(tmp_path: Path):
    async def scenario():
        git = FakeGit(head="c" * 40)
        service = _service(tmp_path, git=git)

        lease = await service.prepare(
            role_name="general",
            task_id="task-000001",
            task_token="task-000001",
        )

        assert lease.preparation is WorkspacePreparation.CREATED
        assert lease.context.kind is WorkspaceKind.WORKTREE
        assert lease.context.task_identity is not None
        assert lease.context.task_identity.relative_name == "general/task-000001"
        assert git.added == [(lease.context.task_identity, lease.context.root)]
        metadata = service.metadata_store.read_candidate(lease.metadata_path)
        assert metadata.phase is WorktreePhase.READY

    asyncio.run(scenario())


def test_service_release_ignores_shared_lease(tmp_path: Path):
    async def scenario():
        service = _service(tmp_path)

        disposition = await service.release(service.shared_lease())

        assert disposition is None

    asyncio.run(scenario())


def _service(tmp_path: Path, *, head: str = "a" * 40, git=None) -> WorktreeService:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    (repo_root / ".worktrees").mkdir()
    repository = RepositoryIdentity(
        root=repo_root,
        common_dir=repo_root / ".git",
        repository_id="repo-123",
    )
    path_policy = WorktreePathPolicy(repository_root=repo_root)
    git = git or FakeGit(head=head)
    return WorktreeService(
        shared_workspace=WorkspaceContext(
            kind=WorkspaceKind.SHARED,
            root=repo_root,
            repository_root=repo_root,
            repository_id=repository.repository_id,
            task_identity=None,
            branch_name=None,
            hooks_path=None,
        ),
        repository_identity=repository,
        path_policy=path_policy,
        config_loader=FixedConfigLoader(),
        git=git,
        metadata_store=WorktreeMetadataStore(path_policy),
        initializer=FakeInitializer(),
        protection_inspector=FakeProtectionInspector(),
        clock=lambda: datetime(2026, 1, 10, tzinfo=timezone.utc),
    )


class FixedConfigLoader:
    def load(self, repository_root: Path) -> WorktreeConfig:
        return WorktreeConfig(digest="d" * 64)


class FakeGit:
    def __init__(self, *, head: str = "a" * 40) -> None:
        self.head = head
        self.added = []

    def identify_repository(self, repository_root: Path):
        return RepositoryIdentity(
            root=repository_root,
            common_dir=repository_root / ".git",
            repository_id="repo-123",
        )

    def capture_head(self, repository_root: Path) -> str:
        return self.head

    def validate_ignored_root(self, worktrees_root: Path) -> None:
        return None

    def add(self, identity, target: Path) -> None:
        target.mkdir(parents=True)
        self.added.append((identity, target))

    def remove(self, repository_root: Path, target: Path) -> None:
        return None

    def delete_branch(self, repository_root: Path, branch: str, *, expected_branch=None) -> None:
        return None


class FakeInitializer:
    def initialize(self, identity, workspace_root: Path, config: WorktreeConfig):
        return InitializationResult(completed_rules=(), hooks_path=None)


class FakeProtectionInspector:
    def inspect(self, lease):
        return WorktreeProtectionStatus(
            has_uncommitted_changes=False,
            has_unpushed_commits=False,
            branch_tip="a" * 40,
            upstream=None,
            reasons=(),
        )
