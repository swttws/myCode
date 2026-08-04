from __future__ import annotations

import asyncio
import threading
from pathlib import Path

import pytest

from mycode.workspace import WorkspacePreparation, WorkspaceTaskIdentity
from mycode.worktree.config import WorktreeConfigLoader
from mycode.worktree.git import GitWorktreeGateway
from mycode.worktree.initializer import WorktreeInitializer
from mycode.worktree.manager import WorktreeManager
from mycode.worktree.metadata import WorktreeMetadataStore
from mycode.worktree.models import (
    InitializationResult,
    WorktreeDisposition,
    WorktreeError,
    WorktreePhase,
    WorktreeProtectionStatus,
)
from mycode.worktree.pathing import WorktreePathPolicy
from mycode.worktree.protection import WorktreeProtectionInspector
from tests.worktree_helpers import create_git_repository_with_bare_remote, run_git


def test_prepare_serializes_same_identity_and_recovers_after_first_create(tmp_path: Path):
    async def scenario() -> None:
        repository = _repository(tmp_path)
        _ignore_worktrees(repository.root)
        git = _git(repository.root, repository.env)
        identity = _identity(repository.root, git)
        release_event = threading.Event()
        initializer = BlockingInitializer(release_event)
        manager = _manager(repository.root, repository.env, git=git, initializer=initializer)
        target = _path_policy(repository.root).resolve_target(identity.relative_name)
        metadata_path = _path_policy(repository.root).resolve_metadata_path(identity.relative_name)

        first = asyncio.create_task(manager.prepare(identity))
        await asyncio.wait_for(asyncio.to_thread(initializer.entered.wait), timeout=5)
        second = asyncio.create_task(manager.prepare(identity))
        await asyncio.sleep(0.05)

        assert initializer.calls == 1
        assert not second.done()

        release_event.set()
        first_lease = await asyncio.wait_for(first, timeout=5)
        second_lease = await asyncio.wait_for(second, timeout=5)

        assert first_lease.preparation is WorkspacePreparation.CREATED
        assert second_lease.preparation is WorkspacePreparation.RECOVERED
        assert first_lease.metadata_path == metadata_path
        assert second_lease.metadata_path == metadata_path
        assert first_lease.initialized_rules == ()
        assert second_lease.initialized_rules == ()
        assert target.is_dir()
        assert metadata_path.exists()

    asyncio.run(scenario())


def test_prepare_reuses_ready_metadata_without_writing_or_calling_git_or_initializer(
    tmp_path: Path,
):
    async def scenario() -> None:
        repository = _repository(tmp_path)
        _ignore_worktrees(repository.root)
        git = _git(repository.root, repository.env)
        identity = _identity(repository.root, git)
        manager = _manager(repository.root, repository.env, git=git)
        target = _path_policy(repository.root).resolve_target(identity.relative_name)
        metadata_path = _path_policy(repository.root).resolve_metadata_path(identity.relative_name)

        created = await manager.prepare(identity)
        assert created.preparation is WorkspacePreparation.CREATED
        assert target.is_dir()
        assert metadata_path.exists()

        recovering_manager = _manager(
            repository.root,
            repository.env,
            git=NoGit(),
            metadata_store=NoWriteMetadataStore(WorktreeMetadataStore(_path_policy(repository.root))),
            initializer=NoInitializer(),
            protection_inspector=NoProtectionInspector(),
        )
        recovered = await recovering_manager.prepare(identity)

        assert recovered.preparation is WorkspacePreparation.RECOVERED
        assert recovered.metadata_path == metadata_path
        assert recovered.initialized_rules == ()
        assert recovered.context.root == target

    asyncio.run(scenario())


def test_prepare_rolls_back_when_initializer_fails(tmp_path: Path):
    async def scenario() -> None:
        repository = _repository(tmp_path)
        _ignore_worktrees(repository.root)
        git = _git(repository.root, repository.env)
        identity = _identity(repository.root, git)
        manager = _manager(
            repository.root,
            repository.env,
            git=git,
            initializer=FailingInitializer(),
        )
        target = _path_policy(repository.root).resolve_target(identity.relative_name)
        metadata_path = _path_policy(repository.root).resolve_metadata_path(identity.relative_name)

        with pytest.raises(WorktreeError):
            await manager.prepare(identity)

        assert not target.exists()
        assert not metadata_path.exists()
        assert (
            run_git(("branch", "--list", identity.branch_name), cwd=repository.root, env=repository.env)
            .stdout.decode("utf-8")
            .strip()
            == ""
        )

    asyncio.run(scenario())


def test_release_retains_protected_worktree_without_deleting(tmp_path: Path):
    async def scenario() -> None:
        repository = _repository(tmp_path)
        _ignore_worktrees(repository.root)
        git = _git(repository.root, repository.env)
        identity = _identity(repository.root, git)
        manager = _manager(repository.root, repository.env, git=git)
        target = _path_policy(repository.root).resolve_target(identity.relative_name)
        metadata_path = _path_policy(repository.root).resolve_metadata_path(identity.relative_name)

        lease = await manager.prepare(identity)

        retain_status = WorktreeProtectionStatus(
            has_uncommitted_changes=True,
            has_unpushed_commits=False,
            branch_tip=identity.base_commit,
            upstream=None,
            reasons=("uncommitted changes",),
        )
        retaining_manager = _manager(
            repository.root,
            repository.env,
            git=NoDeleteGit(),
            protection_inspector=FixedProtectionInspector(retain_status),
        )

        result = await retaining_manager.release(lease)

        assert result.disposition is WorktreeDisposition.RETAINED
        assert target.is_dir()
        assert metadata_path.exists()
        branch_line = (
            run_git(("branch", "--list", identity.branch_name), cwd=repository.root, env=repository.env)
            .stdout.decode("utf-8")
            .strip()
        )
        assert branch_line.split(maxsplit=1)[-1] == identity.branch_name
        retained = WorktreeMetadataStore(_path_policy(repository.root)).read_candidate(metadata_path)
        assert retained.phase is WorktreePhase.RETAINED
        assert retained.retained_reasons == ("uncommitted changes",)

    asyncio.run(scenario())


def test_release_deletes_clean_worktree_and_branch(tmp_path: Path):
    async def scenario() -> None:
        repository = _repository(tmp_path)
        _ignore_worktrees(repository.root)
        git = _git(repository.root, repository.env)
        identity = _identity(repository.root, git)
        manager = _manager(repository.root, repository.env, git=git)
        target = _path_policy(repository.root).resolve_target(identity.relative_name)
        metadata_path = _path_policy(repository.root).resolve_metadata_path(identity.relative_name)

        lease = await manager.prepare(identity)
        result = await manager.release(lease)

        assert result.disposition is WorktreeDisposition.DELETED
        assert result.workspace_root == target
        assert result.branch_name == identity.branch_name
        assert result.reasons == ()
        assert not target.exists()
        assert not metadata_path.exists()
        assert (
            run_git(("branch", "--list", identity.branch_name), cwd=repository.root, env=repository.env)
            .stdout.decode("utf-8")
            .strip()
            == ""
        )

    asyncio.run(scenario())


class BlockingInitializer:
    def __init__(self, release_event: threading.Event) -> None:
        self.release_event = release_event
        self.entered = threading.Event()
        self.calls = 0

    def initialize(
        self,
        identity: WorkspaceTaskIdentity,
        workspace_root: Path,
        config,
    ) -> InitializationResult:
        self.calls += 1
        self.entered.set()
        if not self.release_event.wait(timeout=5):
            raise AssertionError("initializer release gate was not opened")
        return InitializationResult(completed_rules=(), hooks_path=None)


class FailingInitializer:
    def __init__(self) -> None:
        self.calls = 0

    def initialize(
        self,
        identity: WorkspaceTaskIdentity,
        workspace_root: Path,
        config,
    ) -> InitializationResult:
        self.calls += 1
        raise WorktreeError(
            code="worktree_initialization_failed",
            phase="initializer",
            message="initializer boom",
        )


class NoGit:
    def __getattr__(self, name: str):
        def _fail(*args, **kwargs):
            raise AssertionError(f"git.{name} should not be called")

        return _fail


class NoInitializer:
    def initialize(self, *args, **kwargs):  # noqa: D401 - test helper
        raise AssertionError("initializer should not be called")


class NoProtectionInspector:
    def inspect(self, lease):  # noqa: D401 - test helper
        raise AssertionError("protection inspector should not be called")


class NoDeleteGit:
    def __getattr__(self, name: str):
        def _fail(*args, **kwargs):
            raise AssertionError(f"git.{name} should not be called")

        return _fail

    def remove(self, *args, **kwargs):
        raise AssertionError("git.remove should not be called")

    def delete_branch(self, *args, **kwargs):
        raise AssertionError("git.delete_branch should not be called")


class NoWriteMetadataStore:
    def __init__(self, delegate: WorktreeMetadataStore) -> None:
        self._delegate = delegate

    def read_ready(self, *args, **kwargs):
        return self._delegate.read_ready(*args, **kwargs)

    def read_candidate(self, *args, **kwargs):
        return self._delegate.read_candidate(*args, **kwargs)

    def scan(self, *args, **kwargs):
        return self._delegate.scan(*args, **kwargs)

    def write(self, *args, **kwargs):
        raise AssertionError("metadata_store.write should not be called")

    def remove(self, *args, **kwargs):
        raise AssertionError("metadata_store.remove should not be called")


class FixedProtectionInspector:
    def __init__(self, status: WorktreeProtectionStatus) -> None:
        self.status = status
        self.calls: list = []

    def inspect(self, lease):
        self.calls.append(lease)
        return self.status


def _repository(tmp_path: Path):
    repository = create_git_repository_with_bare_remote(tmp_path)
    return repository


def _ignore_worktrees(repo_root: Path) -> None:
    (repo_root / ".worktrees").mkdir(exist_ok=True)
    (repo_root / ".gitignore").write_text(".worktrees/\n", encoding="utf-8")


def _path_policy(repo_root: Path) -> WorktreePathPolicy:
    return WorktreePathPolicy(repository_root=repo_root)


def _git(repo_root: Path, env: dict[str, str]) -> GitWorktreeGateway:
    return GitWorktreeGateway(config=_git_config(), env=env)


def _git_config():
    from mycode.worktree.models import WorktreeConfig

    return WorktreeConfig(digest="abc123")


def _manager(
    repo_root: Path,
    env: dict[str, str],
    *,
    git,
    metadata_store=None,
    initializer=None,
    protection_inspector=None,
) -> WorktreeManager:
    policy = _path_policy(repo_root)
    metadata_store = metadata_store or WorktreeMetadataStore(policy)
    initializer = initializer or WorktreeInitializer(path_policy=policy, git=git)
    protection_inspector = protection_inspector or WorktreeProtectionInspector(git=git)
    return WorktreeManager(
        path_policy=policy,
        config_loader=WorktreeConfigLoader(),
        git=git,
        metadata_store=metadata_store,
        initializer=initializer,
        protection_inspector=protection_inspector,
    )


def _identity(repo_root: Path, git: GitWorktreeGateway) -> WorkspaceTaskIdentity:
    repository_identity = git.identify_repository(repo_root)
    base_commit = git.capture_head(repo_root)
    return WorkspaceTaskIdentity(
        repository_id=repository_identity.repository_id,
        task_id="task-000001",
        role_name="general",
        task_token="task-000001",
        relative_name="general/task-000001",
        branch_name="mycode/worktree/general/task-000001",
        base_commit=base_commit,
    )
