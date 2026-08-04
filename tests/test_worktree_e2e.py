from __future__ import annotations

import asyncio
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

from mycode.memory.paths import MemoryPaths
from mycode.tool.cache import FileTextCache
from mycode.workspace import WorkspacePreparation, WorkspaceTaskIdentity
from mycode.worktree.cleaner import WorktreeCleaner
from mycode.worktree.config import WorktreeConfigLoader
from mycode.worktree.git import GitWorktreeGateway
from mycode.worktree.initializer import WorktreeInitializer
from mycode.worktree.service import WorktreeService
from mycode.worktree.metadata import WorktreeMetadataStore
from mycode.worktree.models import (
    InitializationResult,
    RepositoryIdentity,
    WorktreeConfig,
    WorktreeDisposition,
    WorktreeMetadata,
    WorktreePhase,
)
from mycode.worktree.pathing import WorktreePathPolicy
from mycode.worktree.protection import WorktreeProtectionInspector
from tests.worktree_helpers import create_cli_git_repository, run_git


def test_defined_worktree_task_with_main_dirty_state_commits_and_is_retained_for_unpushed_commit(
    tmp_path: Path,
):
    async def scenario() -> None:
        repository = create_cli_git_repository(tmp_path)
        git = _git(repository.env)
        _commit_file(repository.root, "tracked.txt", "committed\n", "add tracked", repository.env)
        original_cwd = Path.cwd()
        identity = _identity(repository.root, git, "task-retained")
        manager = _manager(repository.root, repository.env, git=git)
        target = _policy(repository.root).resolve_target(identity.relative_name)

        (repository.root / "tracked.txt").write_text("dirty parent\n", encoding="utf-8")
        (repository.root / "dirty-only.txt").write_text("parent only\n", encoding="utf-8")

        lease = await manager.prepare(identity)

        assert Path.cwd() == original_cwd
        assert lease.preparation is WorkspacePreparation.CREATED
        assert lease.context.root == target
        assert (target / "tracked.txt").read_text(encoding="utf-8") == "committed\n"
        assert not (target / "dirty-only.txt").exists()

        _commit_file(target, "child.txt", "child change\n", "child commit", repository.env)
        result = await manager.release(lease)

        assert Path.cwd() == original_cwd
        assert result.disposition is WorktreeDisposition.RETAINED
        assert result.workspace_root == target
        assert result.branch_name == identity.branch_name
        assert result.reasons
        assert target.is_dir()
        assert (repository.root / "tracked.txt").read_text(encoding="utf-8") == "dirty parent\n"
        assert (repository.root / "dirty-only.txt").read_text(encoding="utf-8") == "parent only\n"
        assert _branch_exists(repository.root, identity.branch_name, repository.env)

        metadata = WorktreeMetadataStore(_policy(repository.root)).read_candidate(
            _policy(repository.root).resolve_metadata_path(identity.relative_name)
        )
        assert metadata.phase is WorktreePhase.RETAINED

    asyncio.run(scenario())


def test_no_change_worktree_task_deletes_branch_and_preserves_previous_retained_worktree(
    tmp_path: Path,
):
    async def scenario() -> None:
        repository = create_cli_git_repository(tmp_path)
        git = _git(repository.env)
        manager = _manager(repository.root, repository.env, git=git)
        policy = _policy(repository.root)

        retained_identity = _identity(repository.root, git, "task-retained")
        retained_lease = await manager.prepare(retained_identity)
        _commit_file(
            retained_lease.context.root,
            "child.txt",
            "retain me\n",
            "retained child commit",
            repository.env,
        )
        retained_result = await manager.release(retained_lease)
        assert retained_result.disposition is WorktreeDisposition.RETAINED
        retained_metadata_path = policy.resolve_metadata_path(retained_identity.relative_name)

        clean_identity = _identity(repository.root, git, "task-clean")
        clean_lease = await manager.prepare(clean_identity)
        clean_target = clean_lease.context.root
        clean_metadata_path = policy.resolve_metadata_path(clean_identity.relative_name)
        clean_result = await manager.release(clean_lease)

        assert clean_result.disposition is WorktreeDisposition.DELETED
        assert not clean_target.exists()
        assert not clean_metadata_path.exists()
        assert not _branch_exists(repository.root, clean_identity.branch_name, repository.env)
        assert retained_lease.context.root.is_dir()
        assert retained_metadata_path.exists()
        assert _branch_exists(repository.root, retained_identity.branch_name, repository.env)

    asyncio.run(scenario())


def test_ready_worktree_recovery_is_readonly_after_restart(tmp_path: Path):
    async def scenario() -> None:
        repository = create_cli_git_repository(tmp_path)
        git = _git(repository.env)
        identity = _identity(repository.root, git, "task-recover")
        manager = _manager(repository.root, repository.env, git=git)

        created = await manager.prepare(identity)
        recovering_manager = _manager(
            repository.root,
            repository.env,
            git=NoGit(),
            metadata_store=NoWriteMetadataStore(WorktreeMetadataStore(_policy(repository.root))),
            initializer=NoInitializer(),
            protection_inspector=NoProtectionInspector(),
        )

        recovered = await recovering_manager.prepare(identity)

        assert created.preparation is WorkspacePreparation.CREATED
        assert recovered.preparation is WorkspacePreparation.RECOVERED
        assert recovered.context == created.context
        assert recovered.metadata_path == created.metadata_path
        assert recovered.initialized_rules == created.initialized_rules

    asyncio.run(scenario())


def test_parallel_isolated_subagents_keep_cwd_workspace_and_cache_identities_separate(
    tmp_path: Path,
):
    async def scenario() -> None:
        repository = create_cli_git_repository(tmp_path)
        git = _git(repository.env)
        manager = _manager(repository.root, repository.env, git=git)
        home = tmp_path / "memory-home"
        home.mkdir()
        original_cwd = Path.cwd()

        identities = [
            _identity(repository.root, git, "task-parallel-a"),
            _identity(repository.root, git, "task-parallel-b"),
        ]
        leases = await asyncio.gather(*(manager.prepare(identity) for identity in identities))

        assert Path.cwd() == original_cwd
        assert leases[0].context.root != leases[1].context.root
        assert leases[0].context.branch_name != leases[1].context.branch_name

        cache = FileTextCache()
        relative = Path("same") / "file.txt"
        cache.write_text(leases[0].context.root / relative, "alpha")
        cache.write_text(leases[1].context.root / relative, "beta")

        assert cache.read_text(leases[0].context.root / relative) == "alpha"
        assert cache.read_text(leases[1].context.root / relative) == "beta"
        assert (
            MemoryPaths(workspace_root=leases[0].context.root, home=home).project_digest
            != MemoryPaths(workspace_root=leases[1].context.root, home=home).project_digest
        )

    asyncio.run(scenario())


def test_concurrent_release_and_cleaner_delete_same_worktree_at_most_once(tmp_path: Path):
    async def scenario() -> None:
        repository = create_cli_git_repository(tmp_path)
        base_git = _git(repository.env)
        config = WorktreeConfig(
            digest="e2e-cleanup-config",
            cleanup_interval_seconds=3600.0,
            expire_after_seconds=1.0,
            scan_batch_size=64,
        )
        clock = MutableClock(datetime(2026, 1, 1, tzinfo=timezone.utc))
        git = BlockingDeleteGit(base_git)
        manager = _manager(
            repository.root,
            repository.env,
            git=git,
            config_loader=FixedConfigLoader(config),
            clock=clock,
        )
        identity = _identity(repository.root, base_git, "task-delete-once")
        lease = await manager.prepare(identity)
        metadata_path = _policy(repository.root).resolve_metadata_path(identity.relative_name)
        metadata = WorktreeMetadataStore(_policy(repository.root)).read_candidate(metadata_path)
        repository_identity = base_git.identify_repository(repository.root)
        signaling_store = SignalingMetadataStore(WorktreeMetadataStore(_policy(repository.root)))
        cleaner = _cleaner(
            repository_identity,
            repository.root,
            manager,
            config,
            clock,
            metadata_store=signaling_store,
        )

        clock.advance(timedelta(seconds=2))
        release_task = asyncio.create_task(manager.release(lease))
        await asyncio.wait_for(asyncio.to_thread(git.remove_entered.wait), timeout=5)
        cleaner_task = asyncio.create_task(cleaner.run_batch())
        await asyncio.wait_for(asyncio.to_thread(signaling_store.scan_called.wait), timeout=5)
        await asyncio.wait_for(
            asyncio.to_thread(signaling_store.read_candidate_completed.wait),
            timeout=5,
        )

        git.allow_remove.set()
        release_result, cleaner_result = await asyncio.gather(release_task, cleaner_task)

        assert metadata.phase is WorktreePhase.READY
        assert release_result.disposition is WorktreeDisposition.DELETED
        assert cleaner_result.scanned == 1
        assert cleaner_result.deleted == 0
        assert cleaner_result.failed == 1
        assert git.remove_calls == 1
        assert git.delete_branch_calls == 1
        assert not lease.context.root.exists()
        assert not metadata_path.exists()
        assert not _branch_exists(repository.root, identity.branch_name, repository.env)

    asyncio.run(scenario())


class MutableClock:
    def __init__(self, value: datetime) -> None:
        self.value = value

    def __call__(self) -> datetime:
        return self.value

    def advance(self, delta: timedelta) -> None:
        self.value += delta


class FixedConfigLoader:
    def __init__(self, config: WorktreeConfig) -> None:
        self.config = config

    def load(self, repository_root: Path) -> WorktreeConfig:
        return self.config


class InactiveRegistry:
    def is_workspace_active(self, identity: WorkspaceTaskIdentity) -> bool:
        return False


class CleanerWorktreeServiceView:
    def __init__(
        self,
        *,
        repository_identity: RepositoryIdentity,
        path_policy: WorktreePathPolicy,
        config_loader,
        metadata_store,
        release_service: WorktreeService,
    ) -> None:
        self.repository_identity = repository_identity
        self.path_policy = path_policy
        self.config_loader = config_loader
        self.metadata_store = metadata_store
        self._release_service = release_service

    async def release_candidate(self, metadata: WorktreeMetadata, *, require_expired: bool):
        return await self._release_service.release_candidate(
            metadata,
            require_expired=require_expired,
        )


class SignalingMetadataStore:
    def __init__(self, delegate: WorktreeMetadataStore) -> None:
        self._delegate = delegate
        self.scan_called = threading.Event()
        self.read_candidate_completed = threading.Event()

    def scan(self, limit: int) -> tuple[Path, ...]:
        result = self._delegate.scan(limit)
        self.scan_called.set()
        return result

    def read_candidate(self, metadata_path: Path) -> WorktreeMetadata:
        result = self._delegate.read_candidate(metadata_path)
        self.read_candidate_completed.set()
        return result


class BlockingDeleteGit:
    def __init__(self, delegate: GitWorktreeGateway) -> None:
        self._delegate = delegate
        self.remove_entered = threading.Event()
        self.allow_remove = threading.Event()
        self.remove_calls = 0
        self.delete_branch_calls = 0

    def __getattr__(self, name: str):
        return getattr(self._delegate, name)

    def remove(self, repository_root: Path, target: Path) -> None:
        self.remove_calls += 1
        self.remove_entered.set()
        if not self.allow_remove.wait(timeout=5):
            raise AssertionError("remove was not released")
        self._delegate.remove(repository_root, target)

    def delete_branch(
        self,
        repository_root: Path,
        branch: str,
        *,
        expected_branch: str | None = None,
    ) -> None:
        self.delete_branch_calls += 1
        self._delegate.delete_branch(
            repository_root,
            branch,
            expected_branch=expected_branch,
        )


class NoGit:
    def __getattr__(self, name: str):
        def _fail(*args, **kwargs):
            raise AssertionError(f"git.{name} should not be called during recovery")

        return _fail


class NoInitializer:
    def initialize(
        self,
        identity: WorkspaceTaskIdentity,
        workspace_root: Path,
        config: WorktreeConfig,
    ) -> InitializationResult:
        raise AssertionError("initializer should not be called during recovery")


class NoProtectionInspector:
    def inspect(self, lease):
        raise AssertionError("protection inspector should not be called during recovery")


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
        raise AssertionError("metadata_store.write should not be called during recovery")

    def remove(self, *args, **kwargs):
        raise AssertionError("metadata_store.remove should not be called during recovery")


def _git(env: dict[str, str]) -> GitWorktreeGateway:
    return GitWorktreeGateway(config=WorktreeConfig(digest="git-e2e-config"), env=env)


def _policy(repo_root: Path) -> WorktreePathPolicy:
    return WorktreePathPolicy(repository_root=repo_root)


def _manager(
    repo_root: Path,
    env: dict[str, str],
    *,
    git,
    config_loader=None,
    metadata_store=None,
    initializer=None,
    protection_inspector=None,
    clock=None,
) -> WorktreeService:
    policy = _policy(repo_root)
    metadata_store = metadata_store or WorktreeMetadataStore(policy)
    initializer = initializer or WorktreeInitializer(path_policy=policy, git=git)
    protection_inspector = protection_inspector or WorktreeProtectionInspector(git=git)
    return WorktreeService(
        path_policy=policy,
        config_loader=config_loader or WorktreeConfigLoader(),
        git=git,
        metadata_store=metadata_store,
        initializer=initializer,
        protection_inspector=protection_inspector,
        clock=clock,
    )


def _cleaner(
    repository_identity: RepositoryIdentity,
    repo_root: Path,
    manager: WorktreeService,
    config: WorktreeConfig,
    clock: MutableClock,
    *,
    metadata_store=None,
) -> WorktreeCleaner:
    policy = _policy(repo_root)
    store = metadata_store or WorktreeMetadataStore(policy)
    return WorktreeCleaner(
        worktree_service=CleanerWorktreeServiceView(
            repository_identity=repository_identity,
            path_policy=policy,
            config_loader=FixedConfigLoader(config),
            metadata_store=store,
            release_service=manager,
        ),
        is_workspace_active=InactiveRegistry().is_workspace_active,
        clock=clock,
    )


def _identity(
    repo_root: Path,
    git: GitWorktreeGateway,
    task_token: str,
    *,
    role_name: str = "general",
) -> WorkspaceTaskIdentity:
    repository_identity = git.identify_repository(repo_root)
    base_commit = git.capture_head(repo_root)
    relative_name = f"{role_name}/{task_token}"
    return WorkspaceTaskIdentity(
        repository_id=repository_identity.repository_id,
        task_id=task_token,
        role_name=role_name,
        task_token=task_token,
        relative_name=relative_name,
        branch_name=f"mycode/worktree/{relative_name}",
        base_commit=base_commit,
    )


def _commit_file(
    repo_root: Path,
    relative_path: str,
    text: str,
    message: str,
    env: dict[str, str],
) -> None:
    path = repo_root / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    run_git(("add", relative_path), cwd=repo_root, env=env)
    run_git(("commit", "-m", message), cwd=repo_root, env=env)


def _branch_exists(repo_root: Path, branch_name: str, env: dict[str, str]) -> bool:
    output = run_git(
        ("branch", "--list", branch_name),
        cwd=repo_root,
        env=env,
    ).stdout.decode("utf-8")
    return any(line.strip().lstrip("*+ ").strip() == branch_name for line in output.splitlines())
