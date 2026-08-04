from __future__ import annotations

import asyncio
import os
from contextlib import suppress
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Protocol

from mycode.workspace import (
    WorkspaceContext,
    WorkspaceKind,
    WorkspaceLease,
    WorkspacePreparation,
    WorkspaceTaskIdentity,
)
from mycode.worktree.models import (
    InitializationResult,
    RepositoryIdentity,
    WorktreeConfig,
    WorktreeDisposition,
    WorktreeDispositionResult,
    WorktreeError,
    WorktreeMetadata,
    WorktreePhase,
    WorktreeProtectionStatus,
)
from mycode.worktree.pathing import WorktreePathPolicy


class _ConfigLoader(Protocol):
    def load(self, repository_root: Path) -> WorktreeConfig: ...


class _GitGateway(Protocol):
    def identify_repository(self, repository_root: Path) -> RepositoryIdentity: ...
    def validate_ignored_root(self, worktrees_root: Path) -> None: ...
    def add(self, identity: WorkspaceTaskIdentity, target: Path) -> None: ...
    def remove(self, repository_root: Path, target: Path) -> None: ...
    def delete_branch(
        self,
        repository_root: Path,
        branch: str,
        *,
        expected_branch: str | None = None,
    ) -> None: ...


class _MetadataStore(Protocol):
    def write(self, metadata: WorktreeMetadata) -> Path: ...
    def read_ready(
        self,
        identity: WorkspaceTaskIdentity,
        target: Path,
        config_digest: str,
    ) -> WorktreeMetadata: ...
    def read_candidate(self, metadata_path: Path) -> WorktreeMetadata: ...
    def remove(self, identity: WorkspaceTaskIdentity) -> None: ...


class _Initializer(Protocol):
    def initialize(
        self,
        identity: WorkspaceTaskIdentity,
        workspace_root: Path,
        config: WorktreeConfig,
    ) -> InitializationResult: ...


class _ProtectionInspector(Protocol):
    def inspect(self, lease: WorkspaceLease) -> WorktreeProtectionStatus: ...


class WorktreeManager:
    def __init__(
        self,
        *,
        path_policy: WorktreePathPolicy,
        config_loader: _ConfigLoader,
        git: _GitGateway,
        metadata_store: _MetadataStore,
        initializer: _Initializer,
        protection_inspector: _ProtectionInspector,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._path_policy = path_policy
        self._config_loader = config_loader
        self._git = git
        self._metadata_store = metadata_store
        self._initializer = initializer
        self._protection_inspector = protection_inspector
        self._clock = clock or _utc_now
        self._lock_guard = asyncio.Lock()
        self._locks: dict[str, asyncio.Lock] = {}

    async def prepare(self, identity: WorkspaceTaskIdentity) -> WorkspaceLease:
        if not isinstance(identity, WorkspaceTaskIdentity):
            raise ValueError("identity must be a WorkspaceTaskIdentity")

        workspace_root = self._workspace_root(identity)
        metadata_path = self._metadata_path(identity)
        lock = await self._lock_for(self._lock_key(identity.repository_id, identity.relative_name, workspace_root))

        async with lock:
            config = await self._load_config()
            if workspace_root.exists():
                if not workspace_root.is_dir():
                    raise self._error(
                        code="worktree_target_invalid",
                        phase="prepare",
                        message="worktree target exists but is not a directory",
                        path=workspace_root,
                    )
                return await self._recover_locked(identity, workspace_root, metadata_path, config)
            return await self._create_locked(identity, workspace_root, metadata_path, config)

    async def release(self, lease: WorkspaceLease) -> WorktreeDispositionResult:
        self._require_worktree_lease(lease)
        identity = lease.context.task_identity
        assert identity is not None
        lock = await self._lock_for(
            self._lock_key(identity.repository_id, identity.relative_name, lease.context.root)
        )

        async with lock:
            config = await self._load_config()
            metadata = await asyncio.to_thread(
                self._metadata_store.read_ready,
                identity,
                lease.context.root,
                config.digest,
            )
            self._validate_lease_matches_metadata(lease, metadata, config)
            return await self._inspect_and_dispose_locked(
                metadata,
                config,
                require_expired=False,
                lease=lease,
            )

    async def inspect_and_dispose(
        self,
        metadata: WorktreeMetadata,
        *,
        require_expired: bool,
    ) -> WorktreeDispositionResult:
        if not isinstance(metadata, WorktreeMetadata):
            raise ValueError("metadata must be a WorktreeMetadata")

        lock = await self._lock_for(
            self._lock_key(
                metadata.repository_id,
                metadata.identity.relative_name,
                metadata.workspace_root,
            )
        )

        async with lock:
            config = await self._load_config()
            path = self._metadata_path(metadata.identity)
            current = await asyncio.to_thread(self._metadata_store.read_candidate, path)
            if current != metadata:
                raise self._error(
                    code="worktree_metadata_mismatch",
                    phase="inspect",
                    message="metadata changed before disposal",
                    path=path,
                )
            if current.config_digest != config.digest:
                raise self._error(
                    code="worktree_config_mismatch",
                    phase="inspect",
                    message="worktree config digest mismatch",
                    path=path,
                )
            return await self._inspect_and_dispose_locked(
                current,
                config,
                require_expired=require_expired,
                lease=self._lease_from_metadata(current, config, WorkspacePreparation.RECOVERED),
            )

    async def _create_locked(
        self,
        identity: WorkspaceTaskIdentity,
        workspace_root: Path,
        metadata_path: Path,
        config: WorktreeConfig,
    ) -> WorkspaceLease:
        repository_root = self._repository_root()
        repository_identity = await asyncio.to_thread(self._git.identify_repository, repository_root)
        if repository_identity.repository_id != identity.repository_id:
            raise self._error(
                code="repository_identity_mismatch",
                phase="prepare",
                message="repository identity mismatch",
                path=repository_identity.root,
            )

        worktrees_root = self._worktrees_root()
        await asyncio.to_thread(self._git.validate_ignored_root, worktrees_root)

        creating = self._metadata_for_phase(
            identity=identity,
            workspace_root=workspace_root,
            repository_id=repository_identity.repository_id,
            config_digest=config.digest,
            phase=WorktreePhase.CREATING,
            initialized_rules=(),
            retained_reasons=(),
        )
        await asyncio.to_thread(self._metadata_store.write, creating)

        worktree_created = False
        try:
            await asyncio.to_thread(self._ensure_workspace_parent, workspace_root)
            await asyncio.to_thread(self._git.add, identity, workspace_root)
            worktree_created = True
            initialization = await asyncio.to_thread(
                self._initializer.initialize,
                identity,
                workspace_root,
                config,
            )
            workspace_root = self._path_policy.assert_target_boundary(workspace_root)
            ready = self._with_phase(
                creating,
                phase=WorktreePhase.READY,
                initialized_rules=initialization.completed_rules,
                retained_reasons=(),
            )
            await asyncio.to_thread(self._metadata_store.write, ready)
            return self._lease_from_ready(
                ready,
                config=config,
                preparation=WorkspacePreparation.CREATED,
                hooks_path=initialization.hooks_path,
            )
        except Exception as exc:
            await self._rollback_created_workspace(
                identity=identity,
                workspace_root=workspace_root,
                worktree_created=worktree_created,
            )
            with suppress(Exception):
                await asyncio.to_thread(self._metadata_store.remove, identity)
            raise self._prepare_error(exc, identity, workspace_root) from exc

    async def _recover_locked(
        self,
        identity: WorkspaceTaskIdentity,
        workspace_root: Path,
        metadata_path: Path,
        config: WorktreeConfig,
    ) -> WorkspaceLease:
        metadata = await asyncio.to_thread(
            self._metadata_store.read_ready,
            identity,
            workspace_root,
            config.digest,
        )
        expected = self._lease_from_ready(metadata, config=config, preparation=WorkspacePreparation.RECOVERED)
        if expected.metadata_path != metadata_path:
            raise self._error(
                code="metadata_path_mismatch",
                phase="prepare",
                message="metadata path mismatch",
                path=metadata_path,
            )
        return expected

    async def _inspect_and_dispose_locked(
        self,
        metadata: WorktreeMetadata,
        config: WorktreeConfig,
        *,
        require_expired: bool,
        lease: WorkspaceLease,
    ) -> WorktreeDispositionResult:
        if metadata.phase is WorktreePhase.CREATING:
            return self._disposition(
                WorktreeDisposition.SKIPPED,
                metadata,
                reasons=("creating",),
            )

        if metadata.phase is WorktreePhase.RETAINED:
            return self._disposition(
                WorktreeDisposition.RETAINED,
                metadata,
                reasons=metadata.retained_reasons,
            )

        if require_expired and not self._is_expired(metadata, config):
            return self._disposition(
                WorktreeDisposition.SKIPPED,
                metadata,
                reasons=("not_expired",),
            )

        try:
            protection = await asyncio.to_thread(self._protection_inspector.inspect, lease)
        except Exception as exc:
            return self._disposition(
                WorktreeDisposition.FAILED,
                metadata,
                reasons=(self._reason_from_exception(exc),),
            )

        if protection.has_uncommitted_changes or protection.has_unpushed_commits:
            retained = self._with_phase(
                metadata,
                phase=WorktreePhase.RETAINED,
                initialized_rules=metadata.initialized_rules,
                retained_reasons=protection.reasons,
            )
            try:
                await asyncio.to_thread(self._metadata_store.write, retained)
            except Exception as exc:
                return self._disposition(
                    WorktreeDisposition.FAILED,
                    metadata,
                    reasons=(self._reason_from_exception(exc),),
                )
            return self._disposition(
                WorktreeDisposition.RETAINED,
                retained,
                reasons=protection.reasons,
            )

        remove_ok = await self._remove_worktree(metadata.workspace_root)
        branch_ok = False
        if remove_ok:
            branch_ok = await self._delete_branch(metadata.identity.branch_name)
        if remove_ok and branch_ok:
            try:
                await asyncio.to_thread(self._metadata_store.remove, metadata.identity)
            except Exception as exc:
                return self._disposition(
                    WorktreeDisposition.FAILED,
                    metadata,
                    reasons=(self._reason_from_exception(exc),),
                )
            return self._disposition(WorktreeDisposition.DELETED, metadata, reasons=())

        return self._disposition(
            WorktreeDisposition.FAILED,
            metadata,
            reasons=("cleanup_failed",),
        )

    async def _rollback_created_workspace(
        self,
        *,
        identity: WorkspaceTaskIdentity,
        workspace_root: Path,
        worktree_created: bool,
    ) -> None:
        if worktree_created:
            await self._remove_worktree(workspace_root)
            await self._delete_branch(identity.branch_name)

    async def _remove_worktree(self, workspace_root: Path) -> bool:
        try:
            await asyncio.to_thread(self._git.remove, self._repository_root(), workspace_root)
        except Exception:
            return False
        return True

    async def _delete_branch(self, branch_name: str) -> bool:
        try:
            await asyncio.to_thread(
                self._git.delete_branch,
                self._repository_root(),
                branch_name,
                expected_branch=branch_name,
            )
        except Exception:
            return False
        return True

    async def _load_config(self) -> WorktreeConfig:
        return await asyncio.to_thread(self._config_loader.load, self._repository_root())

    async def _lock_for(self, key: str) -> asyncio.Lock:
        async with self._lock_guard:
            lock = self._locks.get(key)
            if lock is None:
                lock = asyncio.Lock()
                self._locks[key] = lock
            return lock

    def _repository_root(self) -> Path:
        return self._path_policy.repository_root.resolve(strict=True)

    def _worktrees_root(self) -> Path:
        return self._path_policy.validate_root(self._repository_root())

    def _workspace_root(self, identity: WorkspaceTaskIdentity) -> Path:
        return self._path_policy.resolve_target(identity.relative_name)

    def _metadata_path(self, identity: WorkspaceTaskIdentity) -> Path:
        return self._path_policy.resolve_metadata_path(identity.relative_name)

    def _lease_from_ready(
        self,
        metadata: WorktreeMetadata,
        *,
        config: WorktreeConfig,
        preparation: WorkspacePreparation,
        hooks_path: Path | None = None,
    ) -> WorkspaceLease:
        context = self._context_from_metadata(
            metadata,
            config=config,
            hooks_path=hooks_path if hooks_path is not None else self._hooks_path_from_config(config, metadata.workspace_root),
        )
        return WorkspaceLease(
            context=context,
            preparation=preparation,
            metadata_path=self._metadata_path(metadata.identity),
            initialized_rules=metadata.initialized_rules,
        )

    def _lease_from_metadata(
        self,
        metadata: WorktreeMetadata,
        config: WorktreeConfig,
        preparation: WorkspacePreparation,
    ) -> WorkspaceLease:
        return self._lease_from_ready(metadata, config=config, preparation=preparation)

    def _context_from_metadata(
        self,
        metadata: WorktreeMetadata,
        *,
        config: WorktreeConfig,
        hooks_path: Path | None,
    ) -> WorkspaceContext:
        return WorkspaceContext(
            kind=WorkspaceKind.WORKTREE,
            root=metadata.workspace_root,
            repository_root=self._repository_root(),
            repository_id=metadata.repository_id,
            task_identity=metadata.identity,
            branch_name=metadata.identity.branch_name,
            hooks_path=hooks_path,
        )

    def _hooks_path_from_config(self, config: WorktreeConfig, workspace_root: Path) -> Path | None:
        for rule in config.rules:
            if rule.type.value == "hooks":
                return self._path_policy.resolve_rule_target(workspace_root, rule.target)
        return None

    def _validate_lease_matches_metadata(
        self,
        lease: WorkspaceLease,
        metadata: WorktreeMetadata,
        config: WorktreeConfig,
    ) -> None:
        expected = self._lease_from_ready(
            metadata,
            config=config,
            preparation=lease.preparation,
            hooks_path=lease.context.hooks_path,
        )
        if lease.context != expected.context:
            raise self._error(
                code="lease_context_mismatch",
                phase="release",
                message="lease context mismatch",
                path=lease.context.root,
            )
        if tuple(lease.initialized_rules) != metadata.initialized_rules:
            raise self._error(
                code="lease_rules_mismatch",
                phase="release",
                message="lease initialized rules mismatch",
                path=lease.context.root,
            )
        if lease.metadata_path != expected.metadata_path:
            raise self._error(
                code="lease_metadata_path_mismatch",
                phase="release",
                message="lease metadata path mismatch",
                path=lease.metadata_path,
            )

    def _require_worktree_lease(self, lease: WorkspaceLease) -> None:
        if not isinstance(lease, WorkspaceLease):
            raise ValueError("lease must be a WorkspaceLease")
        if lease.context.kind is not WorkspaceKind.WORKTREE or lease.context.task_identity is None:
            raise ValueError("release requires a worktree lease")
        if lease.metadata_path is None:
            raise ValueError("worktree lease must include metadata_path")

    def _metadata_for_phase(
        self,
        *,
        identity: WorkspaceTaskIdentity,
        workspace_root: Path,
        repository_id: str,
        config_digest: str,
        phase: WorktreePhase,
        initialized_rules: tuple[str, ...],
        retained_reasons: tuple[str, ...],
    ) -> WorktreeMetadata:
        now = self._clock()
        return WorktreeMetadata(
            schema_version=1,
            phase=phase,
            repository_id=repository_id,
            identity=identity,
            workspace_root=workspace_root,
            config_digest=config_digest,
            created_at=now,
            last_active_at=now,
            initialized_rules=initialized_rules,
            retained_reasons=retained_reasons,
        )

    def _with_phase(
        self,
        metadata: WorktreeMetadata,
        *,
        phase: WorktreePhase,
        initialized_rules: tuple[str, ...],
        retained_reasons: tuple[str, ...],
    ) -> WorktreeMetadata:
        return WorktreeMetadata(
            schema_version=metadata.schema_version,
            phase=phase,
            repository_id=metadata.repository_id,
            identity=metadata.identity,
            workspace_root=metadata.workspace_root,
            config_digest=metadata.config_digest,
            created_at=metadata.created_at,
            last_active_at=self._clock(),
            initialized_rules=initialized_rules,
            retained_reasons=retained_reasons,
        )

    def _disposition(
        self,
        disposition: WorktreeDisposition,
        metadata: WorktreeMetadata,
        *,
        reasons: tuple[str, ...],
    ) -> WorktreeDispositionResult:
        return WorktreeDispositionResult(
            disposition=disposition,
            workspace_root=metadata.workspace_root,
            branch_name=metadata.identity.branch_name,
            reasons=reasons,
        )

    def _is_expired(self, metadata: WorktreeMetadata, config: WorktreeConfig) -> bool:
        deadline = metadata.last_active_at + timedelta(seconds=config.expire_after_seconds)
        return self._clock() >= deadline

    def _ensure_workspace_parent(self, workspace_root: Path) -> None:
        workspace_root.parent.mkdir(parents=True, exist_ok=True)

    def _prepare_error(
        self,
        exc: Exception,
        identity: WorkspaceTaskIdentity,
        workspace_root: Path,
    ) -> WorktreeError:
        if isinstance(exc, WorktreeError):
            return exc
        return self._error(
            code="worktree_prepare_failed",
            phase="prepare",
            message=str(exc) or exc.__class__.__name__,
            path=workspace_root,
            branch_name=identity.branch_name,
        )

    def _reason_from_exception(self, exc: Exception) -> str:
        if isinstance(exc, WorktreeError):
            return exc.code
        return exc.__class__.__name__

    def _error(
        self,
        *,
        code: str,
        phase: str,
        message: str,
        path: Path | None = None,
        branch_name: str | None = None,
    ) -> WorktreeError:
        return WorktreeError(
            code=code,
            phase=phase,
            message=message,
            path=path,
            branch_name=branch_name,
        )

    async def _lock_for(self, key: str) -> asyncio.Lock:
        async with self._lock_guard:
            lock = self._locks.get(key)
            if lock is None:
                lock = asyncio.Lock()
                self._locks[key] = lock
            return lock

    def _lock_key(self, repository_id: str, relative_name: str, workspace_root: Path) -> str:
        return "|".join(
            (
                os.path.normcase(repository_id),
                os.path.normcase(relative_name),
                os.path.normcase(str(workspace_root)),
            )
        )


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)
