from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from mycode.workspace import WorkspaceTaskIdentity
from mycode.worktree.cleaner import WorktreeCleaner
from mycode.worktree.metadata import WorktreeMetadataStore
from mycode.worktree.models import (
    CleanupBatchResult,
    RepositoryIdentity,
    WorktreeConfig,
    WorktreeDiagnostic,
    WorktreeDisposition,
    WorktreeDispositionResult,
    WorktreeError,
    WorktreeMetadata,
    WorktreePhase,
)
from mycode.worktree.pathing import WorktreePathPolicy


def test_run_batch_filters_invalid_candidates_before_disposal(tmp_path: Path):
    async def scenario() -> None:
        harness = CleanerHarness(tmp_path, batch_size=8)
        harness.write_raw_json("task-000001", {"schema_version": 2})
        harness.write_raw_metadata(
            "task-000002",
            workspace_root=str(tmp_path / "outside" / "task-000002"),
        )
        harness.write_metadata("task-000003", repository_id="other-repository")
        harness.write_metadata("task-000004", gitdir=tmp_path / "outside" / "gitdir")
        valid = harness.write_metadata("task-000005")

        result = await harness.cleaner.run_batch()

        assert result.scanned == 5
        assert result.deleted == 1
        assert result.skipped == 4
        assert result.retained == 0
        assert result.failed == 0
        assert result.has_more is False
        assert harness.manager.calls == [valid]
        assert tuple(diagnostic.branch_name for diagnostic in result.diagnostics) == (
            None,
            None,
            "mycode/worktree/general/task-000003",
            "mycode/worktree/general/task-000004",
        )

    asyncio.run(scenario())


def test_run_batch_skips_not_expired_and_active_candidates_without_disposal(tmp_path: Path):
    async def scenario() -> None:
        harness = CleanerHarness(tmp_path, batch_size=8)
        harness.write_metadata("task-000001", last_active_at=harness.now - timedelta(seconds=5))
        harness.active_registry.active_tokens.add("task-000002")
        harness.write_metadata("task-000002")
        expired = harness.write_metadata("task-000003")

        result = await harness.cleaner.run_batch()

        assert result.scanned == 3
        assert result.deleted == 1
        assert result.skipped == 2
        assert result.failed == 0
        assert harness.manager.calls == [expired]
        assert harness.active_registry.calls == ["task-000002", "task-000003"]
        assert tuple(diagnostic.code for diagnostic in result.diagnostics) == (
            "worktree_not_expired",
            "worktree_active",
        )

    asyncio.run(scenario())


def test_run_batch_counts_manager_dispositions_and_continues_after_failure(tmp_path: Path):
    async def scenario() -> None:
        harness = CleanerHarness(tmp_path, batch_size=2)
        retained = harness.write_metadata("task-000001")
        failed = harness.write_metadata("task-000002")
        not_scanned = harness.write_metadata("task-000003")
        harness.manager.results = {
            retained.identity.task_token: WorktreeDispositionResult(
                disposition=WorktreeDisposition.RETAINED,
                workspace_root=retained.workspace_root,
                branch_name=retained.identity.branch_name,
                reasons=("uncommitted changes",),
            ),
            failed.identity.task_token: WorktreeError(
                code="git_failed",
                phase="git",
                message="boom",
                path=failed.workspace_root,
                branch_name=failed.identity.branch_name,
            ),
            not_scanned.identity.task_token: WorktreeDispositionResult(
                disposition=WorktreeDisposition.DELETED,
                workspace_root=not_scanned.workspace_root,
                branch_name=not_scanned.identity.branch_name,
                reasons=(),
            ),
        }

        result = await harness.cleaner.run_batch()

        assert result.scanned == 2
        assert result.deleted == 0
        assert result.retained == 1
        assert result.failed == 1
        assert result.skipped == 0
        assert result.has_more is True
        assert harness.manager.calls == [retained, failed]
        assert tuple(diagnostic.code for diagnostic in result.diagnostics) == (
            "worktree_retained",
            "git_failed",
        )

    asyncio.run(scenario())


def test_start_is_idempotent_runs_immediate_and_interval_batches_then_closes(tmp_path: Path):
    async def scenario() -> None:
        harness = CleanerHarness(tmp_path, batch_size=1, cleanup_interval_seconds=3.0)
        sleep = ControlledSleep()
        store = EmptyMetadataStore()
        cleaner = harness.build_cleaner(metadata_store=store, sleep=sleep)

        await cleaner.start()
        await cleaner.start()
        await wait_until(lambda: store.scan_calls == 1)
        await wait_until(lambda: sleep.calls == [3.0])

        sleep.advance_one()
        await wait_until(lambda: store.scan_calls == 2)

        await cleaner.close()
        await cleaner.close()
        assert store.scan_calls == 2

    asyncio.run(scenario())


class CleanerHarness:
    def __init__(
        self,
        tmp_path: Path,
        *,
        batch_size: int,
        cleanup_interval_seconds: float = 3600.0,
    ) -> None:
        self.now = datetime(2026, 1, 10, 0, 0, tzinfo=timezone.utc)
        self.repo_root = tmp_path / "repo"
        self.common_dir = self.repo_root / ".git"
        (self.repo_root / ".worktrees" / ".metadata" / "general").mkdir(parents=True)
        (self.common_dir / "worktrees").mkdir(parents=True)
        self.repository = RepositoryIdentity(
            root=self.repo_root,
            common_dir=self.common_dir,
            repository_id="repo-123",
        )
        self.path_policy = WorktreePathPolicy(repository_root=self.repo_root)
        self.config = WorktreeConfig(
            digest="b" * 64,
            expire_after_seconds=10.0,
            cleanup_interval_seconds=cleanup_interval_seconds,
            scan_batch_size=batch_size,
        )
        self.store = WorktreeMetadataStore(self.path_policy)
        self.manager = FakeManager()
        self.active_registry = FakeActiveRegistry()
        self.cleaner = self.build_cleaner()

    def build_cleaner(
        self,
        *,
        metadata_store=None,
        sleep=None,
    ) -> WorktreeCleaner:
        return WorktreeCleaner(
            repository_identity=self.repository,
            path_policy=self.path_policy,
            config_loader=FixedConfigLoader(self.config),
            metadata_store=metadata_store or self.store,
            manager=self.manager,
            active_registry=self.active_registry,
            clock=lambda: self.now,
            sleep=sleep,
        )

    def write_metadata(
        self,
        task_token: str,
        *,
        repository_id: str | None = None,
        last_active_at: datetime | None = None,
        gitdir: Path | None = None,
    ) -> WorktreeMetadata:
        identity = self.identity(task_token, repository_id=repository_id or self.repository.repository_id)
        metadata = WorktreeMetadata(
            schema_version=1,
            phase=WorktreePhase.READY,
            repository_id=identity.repository_id,
            identity=identity,
            workspace_root=self.path_policy.resolve_target(identity.relative_name),
            config_digest=self.config.digest,
            created_at=self.now - timedelta(days=2),
            last_active_at=last_active_at or self.now - timedelta(seconds=20),
            initialized_rules=(),
            retained_reasons=(),
        )
        self.store.write(metadata)
        self.write_git_pointer(metadata, gitdir=gitdir)
        return metadata

    def write_raw_json(self, task_token: str, payload: dict[str, object]) -> Path:
        path = self.metadata_path(task_token)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def write_raw_metadata(self, task_token: str, **updates: object) -> Path:
        metadata = self.write_metadata(task_token)
        payload = metadata_payload(metadata)
        payload.update(updates)
        path = self.metadata_path(task_token)
        path.write_text(canonical_json(payload), encoding="utf-8")
        return path

    def write_git_pointer(self, metadata: WorktreeMetadata, *, gitdir: Path | None = None) -> None:
        workspace_root = metadata.workspace_root
        workspace_root.mkdir(parents=True, exist_ok=True)
        gitdir = gitdir or (self.common_dir / "worktrees" / metadata.identity.task_token)
        gitdir.mkdir(parents=True, exist_ok=True)
        (workspace_root / ".git").write_text(f"gitdir: {gitdir}\n", encoding="utf-8")

    def identity(self, task_token: str, *, repository_id: str) -> WorkspaceTaskIdentity:
        relative_name = f"general/{task_token}"
        return WorkspaceTaskIdentity(
            repository_id=repository_id,
            task_id=task_token,
            role_name="general",
            task_token=task_token,
            relative_name=relative_name,
            branch_name=f"mycode/worktree/{relative_name}",
            base_commit="a" * 40,
        )

    def metadata_path(self, task_token: str) -> Path:
        return self.path_policy.resolve_metadata_path(f"general/{task_token}")


class FixedConfigLoader:
    def __init__(self, config: WorktreeConfig) -> None:
        self.config = config

    def load(self, repository_root: Path) -> WorktreeConfig:
        return self.config


class FakeActiveRegistry:
    def __init__(self) -> None:
        self.active_tokens: set[str] = set()
        self.calls: list[str] = []

    def is_workspace_active(self, identity: WorkspaceTaskIdentity) -> bool:
        self.calls.append(identity.task_token)
        return identity.task_token in self.active_tokens


class FakeManager:
    def __init__(self) -> None:
        self.calls: list[WorktreeMetadata] = []
        self.results: dict[str, WorktreeDispositionResult | Exception] = {}

    async def inspect_and_dispose(
        self,
        metadata: WorktreeMetadata,
        *,
        require_expired: bool,
    ) -> WorktreeDispositionResult:
        assert require_expired is True
        self.calls.append(metadata)
        result = self.results.get(metadata.identity.task_token)
        if isinstance(result, Exception):
            raise result
        if result is not None:
            return result
        return WorktreeDispositionResult(
            disposition=WorktreeDisposition.DELETED,
            workspace_root=metadata.workspace_root,
            branch_name=metadata.identity.branch_name,
            reasons=(),
        )


class EmptyMetadataStore:
    def __init__(self) -> None:
        self.scan_calls = 0

    def scan(self, limit: int) -> tuple[Path, ...]:
        self.scan_calls += 1
        return ()


class ControlledSleep:
    def __init__(self) -> None:
        self.calls: list[float] = []
        self._pending: list[asyncio.Future[None]] = []

    async def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)
        future = asyncio.get_running_loop().create_future()
        self._pending.append(future)
        await future

    def advance_one(self) -> None:
        future = self._pending.pop(0)
        future.set_result(None)


async def wait_until(predicate) -> None:
    for _ in range(100):
        if predicate():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("condition was not reached")


def metadata_payload(metadata: WorktreeMetadata) -> dict[str, object]:
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
        "created_at": metadata.created_at.isoformat().replace("+00:00", "Z"),
        "last_active_at": metadata.last_active_at.isoformat().replace("+00:00", "Z"),
        "initialized_rules": list(metadata.initialized_rules),
        "retained_reasons": list(metadata.retained_reasons),
    }


def canonical_json(payload: dict[str, object]) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
