from __future__ import annotations

import asyncio
import os
from contextlib import suppress
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Awaitable, Callable, Protocol

from mycode.workspace import WorkspaceTaskIdentity
from mycode.worktree.models import (
    CleanupBatchResult,
    RepositoryIdentity,
    WorktreeConfig,
    WorktreeDiagnostic,
    WorktreeDisposition,
    WorktreeDispositionResult,
    WorktreeError,
    WorktreeMetadata,
)
from mycode.worktree.pathing import WorktreePathPolicy


_DEFAULT_CLEANUP_INTERVAL_SECONDS = 3600.0
_MAX_GIT_POINTER_BYTES = 4096


class ActiveWorkspaceRegistry(Protocol):
    def is_workspace_active(self, identity: WorkspaceTaskIdentity) -> bool: ...


class _ConfigLoader(Protocol):
    def load(self, repository_root: Path) -> WorktreeConfig: ...


class _MetadataStore(Protocol):
    def scan(self, limit: int) -> tuple[Path, ...]: ...
    def read_candidate(self, metadata_path: Path) -> WorktreeMetadata: ...


class _WorktreeManager(Protocol):
    async def inspect_and_dispose(
        self,
        metadata: WorktreeMetadata,
        *,
        require_expired: bool,
    ) -> WorktreeDispositionResult: ...


class WorktreeCleaner:
    def __init__(
        self,
        *,
        repository_identity: RepositoryIdentity,
        path_policy: WorktreePathPolicy,
        config_loader: _ConfigLoader,
        metadata_store: _MetadataStore,
        manager: _WorktreeManager,
        active_registry: ActiveWorkspaceRegistry,
        clock: Callable[[], datetime] | None = None,
        sleep: Callable[[float], Awaitable[None]] | None = None,
    ) -> None:
        if not isinstance(repository_identity, RepositoryIdentity):
            raise ValueError("repository_identity must be a RepositoryIdentity")
        if not isinstance(path_policy, WorktreePathPolicy):
            raise ValueError("path_policy must be a WorktreePathPolicy")
        self._repository_identity = repository_identity
        self._path_policy = path_policy
        self._config_loader = config_loader
        self._metadata_store = metadata_store
        self._manager = manager
        self._active_registry = active_registry
        self._clock = clock or _utc_now
        self._sleep = sleep or asyncio.sleep
        self._task: asyncio.Task[None] | None = None
        self._task_guard = asyncio.Lock()

    async def start(self) -> None:
        async with self._task_guard:
            if self._task is not None and not self._task.done():
                return
            self._task = asyncio.create_task(self._run_loop())

    async def run_batch(self) -> CleanupBatchResult:
        try:
            config = await self._load_config()
            return await self._run_batch_with_config(config)
        except Exception as exc:
            return CleanupBatchResult(
                scanned=0,
                deleted=0,
                retained=0,
                skipped=0,
                failed=1,
                has_more=False,
                diagnostics=(self._diagnostic_from_exception(exc, fallback_path=self._repository_identity.root),),
            )

    async def close(self) -> None:
        async with self._task_guard:
            task = self._task
            self._task = None
        if task is None:
            return
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task

    async def _run_loop(self) -> None:
        while True:
            delay = _DEFAULT_CLEANUP_INTERVAL_SECONDS
            try:
                config = await self._load_config()
                delay = config.cleanup_interval_seconds
                await self._run_batch_with_config(config)
            except asyncio.CancelledError:
                raise
            except Exception:
                delay = _DEFAULT_CLEANUP_INTERVAL_SECONDS
            await asyncio.sleep(0)
            await self._sleep(delay)

    async def _run_batch_with_config(self, config: WorktreeConfig) -> CleanupBatchResult:
        scan_limit = config.scan_batch_size + 1
        candidates = await asyncio.to_thread(self._metadata_store.scan, scan_limit)
        batch = tuple(candidates[: config.scan_batch_size])
        has_more = len(candidates) > config.scan_batch_size
        counts = _DispositionCounts()
        diagnostics: list[WorktreeDiagnostic] = []

        for metadata_path in batch:
            metadata: WorktreeMetadata | None = None
            try:
                metadata = await asyncio.to_thread(self._metadata_store.read_candidate, metadata_path)
                self._validate_candidate(metadata_path, metadata)
            except Exception as exc:
                counts.skipped += 1
                diagnostics.append(
                    self._diagnostic_from_exception(
                        exc,
                        fallback_path=metadata_path,
                        metadata=metadata,
                    )
                )
                continue

            skip_reason = self._skip_reason(metadata, config)
            if skip_reason is not None:
                counts.skipped += 1
                diagnostics.append(skip_reason)
                continue

            try:
                if self._active_registry.is_workspace_active(metadata.identity):
                    counts.skipped += 1
                    diagnostics.append(
                        self._diagnostic(
                            code="worktree_active",
                            phase="cleanup",
                            message="Worktree 仍有关联任务活动，跳过清理",
                            metadata=metadata,
                        )
                    )
                    continue
            except Exception as exc:
                counts.failed += 1
                diagnostics.append(
                    self._diagnostic_from_exception(
                        exc,
                        fallback_path=metadata.workspace_root,
                        metadata=metadata,
                    )
                )
                continue

            try:
                result = await self._manager.inspect_and_dispose(
                    metadata,
                    require_expired=True,
                )
            except Exception as exc:
                counts.failed += 1
                diagnostics.append(
                    self._diagnostic_from_exception(
                        exc,
                        fallback_path=metadata.workspace_root,
                        metadata=metadata,
                    )
                )
                continue

            self._count_result(counts, result)
            diagnostic = self._diagnostic_from_result(result)
            if diagnostic is not None:
                diagnostics.append(diagnostic)

        return CleanupBatchResult(
            scanned=len(batch),
            deleted=counts.deleted,
            retained=counts.retained,
            skipped=counts.skipped,
            failed=counts.failed,
            has_more=has_more,
            diagnostics=tuple(diagnostics),
        )

    async def _load_config(self) -> WorktreeConfig:
        return await asyncio.to_thread(self._config_loader.load, self._repository_identity.root)

    def _validate_candidate(self, metadata_path: Path, metadata: WorktreeMetadata) -> None:
        if metadata.repository_id != self._repository_identity.repository_id:
            raise self._candidate_error(
                "worktree_metadata_repository_mismatch",
                "sidecar 仓库身份不匹配，跳过清理",
                path=metadata_path,
                branch_name=metadata.identity.branch_name,
            )
        expected_metadata_path = self._path_policy.resolve_metadata_path(metadata.identity.relative_name)
        if not _same_path(metadata_path.resolve(strict=False), expected_metadata_path):
            raise self._candidate_error(
                "worktree_metadata_path_mismatch",
                "sidecar 路径与任务身份不匹配，跳过清理",
                path=metadata_path,
                branch_name=metadata.identity.branch_name,
            )
        expected_workspace_root = self._path_policy.resolve_target(metadata.identity.relative_name)
        if not _same_path(metadata.workspace_root, expected_workspace_root):
            raise self._candidate_error(
                "worktree_workspace_path_mismatch",
                "Worktree 路径与任务身份不匹配，跳过清理",
                path=metadata.workspace_root,
                branch_name=metadata.identity.branch_name,
            )
        workspace_root = self._path_policy.assert_target_boundary(metadata.workspace_root)
        if not workspace_root.is_dir():
            raise self._candidate_error(
                "worktree_workspace_missing",
                "Worktree 目录不存在，跳过清理",
                path=workspace_root,
                branch_name=metadata.identity.branch_name,
            )
        self._validate_git_pointer(workspace_root, metadata.identity.branch_name)

    def _validate_git_pointer(self, workspace_root: Path, branch_name: str) -> None:
        git_file = workspace_root / ".git"
        if git_file.is_symlink() or not git_file.is_file():
            raise self._candidate_error(
                "worktree_git_pointer_invalid",
                "Worktree .git 指针无效，跳过清理",
                path=git_file,
                branch_name=branch_name,
            )
        try:
            if git_file.stat().st_size > _MAX_GIT_POINTER_BYTES:
                raise OSError("git pointer too large")
            text = git_file.read_text(encoding="utf-8")
        except OSError as exc:
            raise self._candidate_error(
                "worktree_git_pointer_invalid",
                "Worktree .git 指针无法读取，跳过清理",
                path=git_file,
                branch_name=branch_name,
            ) from exc

        lines = text.strip().splitlines()
        if len(lines) != 1 or not lines[0].startswith("gitdir: "):
            raise self._candidate_error(
                "worktree_git_pointer_invalid",
                "Worktree .git 指针格式无效，跳过清理",
                path=git_file,
                branch_name=branch_name,
            )
        raw_gitdir = lines[0][len("gitdir: ") :].strip()
        if not raw_gitdir:
            raise self._candidate_error(
                "worktree_git_pointer_invalid",
                "Worktree .git 指针为空，跳过清理",
                path=git_file,
                branch_name=branch_name,
            )

        gitdir = Path(raw_gitdir)
        if not gitdir.is_absolute():
            gitdir = workspace_root / gitdir
        try:
            resolved_gitdir = gitdir.resolve(strict=True)
            allowed_root = (self._repository_identity.common_dir / "worktrees").resolve(strict=True)
        except OSError as exc:
            raise self._candidate_error(
                "worktree_git_pointer_invalid",
                "Worktree .git 指针无法解析，跳过清理",
                path=git_file,
                branch_name=branch_name,
            ) from exc
        if not _is_relative_to(resolved_gitdir, allowed_root):
            raise self._candidate_error(
                "worktree_git_pointer_invalid",
                "Worktree .git 指针越过仓库边界，跳过清理",
                path=git_file,
                branch_name=branch_name,
            )

    def _skip_reason(
        self,
        metadata: WorktreeMetadata,
        config: WorktreeConfig,
    ) -> WorktreeDiagnostic | None:
        deadline = metadata.last_active_at + timedelta(seconds=config.expire_after_seconds)
        if self._clock() < deadline:
            return self._diagnostic(
                code="worktree_not_expired",
                phase="cleanup",
                message="Worktree 尚未过期，跳过清理",
                metadata=metadata,
            )
        return None

    def _count_result(
        self,
        counts: "_DispositionCounts",
        result: WorktreeDispositionResult,
    ) -> None:
        if result.disposition is WorktreeDisposition.DELETED:
            counts.deleted += 1
        elif result.disposition is WorktreeDisposition.RETAINED:
            counts.retained += 1
        elif result.disposition is WorktreeDisposition.SKIPPED:
            counts.skipped += 1
        elif result.disposition is WorktreeDisposition.FAILED:
            counts.failed += 1
        else:
            counts.failed += 1

    def _diagnostic_from_result(
        self,
        result: WorktreeDispositionResult,
    ) -> WorktreeDiagnostic | None:
        if result.disposition is WorktreeDisposition.DELETED:
            return None
        if result.disposition is WorktreeDisposition.RETAINED:
            code = "worktree_retained"
            message = "Worktree 受保护，保留目录"
        elif result.disposition is WorktreeDisposition.SKIPPED:
            code = "worktree_skipped"
            message = "Worktree 未满足清理条件，跳过"
        else:
            code = "worktree_cleanup_failed"
            message = "Worktree 清理失败"
        return WorktreeDiagnostic(
            code=code,
            phase="cleanup",
            message=message,
            path=result.workspace_root,
            branch_name=result.branch_name,
        )

    def _diagnostic_from_exception(
        self,
        exc: Exception,
        *,
        fallback_path: Path,
        metadata: WorktreeMetadata | None = None,
    ) -> WorktreeDiagnostic:
        branch_name = metadata.identity.branch_name if metadata is not None else None
        if isinstance(exc, WorktreeError):
            path = exc.path if exc.path is not None else fallback_path
            return WorktreeDiagnostic(
                code=exc.code,
                phase=exc.phase,
                message=exc.message,
                path=path if path.is_absolute() else fallback_path,
                branch_name=exc.branch_name or branch_name,
            )
        return WorktreeDiagnostic(
            code=exc.__class__.__name__,
            phase="cleanup",
            message="Worktree 清理候选处理失败",
            path=fallback_path,
            branch_name=branch_name,
        )

    def _diagnostic(
        self,
        *,
        code: str,
        phase: str,
        message: str,
        metadata: WorktreeMetadata,
    ) -> WorktreeDiagnostic:
        return WorktreeDiagnostic(
            code=code,
            phase=phase,
            message=message,
            path=metadata.workspace_root,
            branch_name=metadata.identity.branch_name,
        )

    def _candidate_error(
        self,
        code: str,
        message: str,
        *,
        path: Path,
        branch_name: str | None = None,
    ) -> WorktreeError:
        return WorktreeError(
            code=code,
            phase="cleanup",
            message=message,
            path=path,
            branch_name=branch_name,
        )


class _DispositionCounts:
    def __init__(self) -> None:
        self.deleted = 0
        self.retained = 0
        self.skipped = 0
        self.failed = 0


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _is_relative_to(child: Path, parent: Path) -> bool:
    try:
        child_text = os.path.normcase(str(child))
        parent_text = os.path.normcase(str(parent))
        return os.path.commonpath([child_text, parent_text]) == parent_text
    except ValueError:
        return False


def _same_path(left: Path, right: Path) -> bool:
    return os.path.normcase(str(left)) == os.path.normcase(str(right))
