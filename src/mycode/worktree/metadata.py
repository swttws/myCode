from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from mycode.workspace import WorkspaceTaskIdentity
from mycode.worktree.models import WorktreeError, WorktreeMetadata, WorktreePhase
from mycode.worktree.pathing import WorktreePathPolicy


_SCHEMA_VERSION = 1
_MAX_METADATA_BYTES = 64 * 1024
_TOP_LEVEL_FIELDS = frozenset(
    {
        "schema_version",
        "phase",
        "repository_id",
        "identity",
        "workspace_root",
        "config_digest",
        "created_at",
        "last_active_at",
        "initialized_rules",
        "retained_reasons",
    }
)
_IDENTITY_FIELDS = frozenset(
    {
        "repository_id",
        "task_id",
        "role_name",
        "task_token",
        "relative_name",
        "branch_name",
        "base_commit",
    }
)


class WorktreeMetadataStore:
    def __init__(self, path_policy: WorktreePathPolicy) -> None:
        if not isinstance(path_policy, WorktreePathPolicy):
            raise ValueError("path_policy must be a WorktreePathPolicy")
        self._path_policy = path_policy

    def write(self, metadata: WorktreeMetadata) -> Path:
        if not isinstance(metadata, WorktreeMetadata):
            raise ValueError("metadata must be a WorktreeMetadata")
        path = self._path_policy.resolve_metadata_path(metadata.identity.relative_name)
        self._path_policy.assert_target_boundary(metadata.workspace_root)
        payload = _metadata_to_payload(metadata)
        text = _canonical_json(payload)
        if len(text.encode("utf-8")) > _MAX_METADATA_BYTES:
            raise self._error("元数据超过 64 KiB，拒绝写入", path=path)

        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        with tmp_path.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        tmp_path.replace(path)
        return path

    def read_ready(
        self,
        identity: WorkspaceTaskIdentity,
        target: Path,
        config_digest: str,
    ) -> WorktreeMetadata:
        if not isinstance(identity, WorkspaceTaskIdentity):
            raise self._error("元数据恢复身份必须是 WorkspaceTaskIdentity")
        if type(config_digest) is not str or not config_digest:
            raise self._error("元数据配置摘要不能为空")
        path = self._path_policy.resolve_metadata_path(identity.relative_name)
        metadata = self.read_candidate(path)
        target_root = self._path_policy.assert_target_boundary(target)

        if metadata.phase is not WorktreePhase.READY:
            raise self._error("元数据不是 READY 阶段，拒绝恢复", path=path)
        if metadata.repository_id != identity.repository_id:
            raise self._error("元数据 repository_id 不匹配", path=path)
        if metadata.identity != identity:
            raise self._error("元数据任务身份不匹配", path=path)
        if metadata.workspace_root != target_root:
            raise self._error("元数据工作区路径不匹配", path=path)
        if metadata.config_digest != config_digest:
            raise self._error("元数据配置摘要不匹配", path=path)
        return metadata

    def read_candidate(self, metadata_path: Path) -> WorktreeMetadata:
        path = self._metadata_path(metadata_path)
        try:
            size = path.stat().st_size
        except OSError as exc:
            raise self._error("元数据无法读取", path=path) from exc
        if size > _MAX_METADATA_BYTES:
            raise self._error("元数据超过 64 KiB，拒绝读取", path=path)
        try:
            raw_text = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise self._error("元数据无法读取", path=path) from exc
        return self._decode(raw_text, path)

    def scan(self, limit: int) -> tuple[Path, ...]:
        if type(limit) is not int or limit < 1:
            raise self._error("元数据扫描数量必须是正整数")
        metadata_root = self._metadata_root()
        if not metadata_root.exists():
            return ()
        candidates = sorted(
            (
                path.resolve(strict=False)
                for path in metadata_root.rglob("*.json")
                if path.is_file()
            ),
            key=lambda path: os.path.normcase(str(path)),
        )
        return tuple(candidates[:limit])

    def remove(self, identity: WorkspaceTaskIdentity) -> None:
        if not isinstance(identity, WorkspaceTaskIdentity):
            raise self._error("元数据删除身份必须是 WorkspaceTaskIdentity")
        path = self._path_policy.resolve_metadata_path(identity.relative_name)
        try:
            path.unlink()
        except FileNotFoundError:
            return
        except OSError as exc:
            raise self._error("元数据无法删除", path=path) from exc

        metadata_root = self._metadata_root()
        current = path.parent
        while current != metadata_root and _is_relative_to(current, metadata_root):
            try:
                current.rmdir()
            except OSError:
                return
            current = current.parent

    def _metadata_path(self, metadata_path: Path) -> Path:
        if not isinstance(metadata_path, Path):
            raise self._error("元数据路径必须是路径")
        if not metadata_path.is_absolute():
            raise self._error("元数据路径必须是绝对路径")
        return self._path_policy.assert_target_boundary(metadata_path)

    def _metadata_root(self) -> Path:
        worktrees_root = self._path_policy.validate_root(self._path_policy.repository_root)
        metadata_root = worktrees_root / ".metadata"
        return self._path_policy.assert_target_boundary(metadata_root)

    def _decode(self, raw_text: str, path: Path) -> WorktreeMetadata:
        try:
            payload = json.loads(raw_text, object_pairs_hook=_reject_duplicate_pairs)
        except _DuplicateFieldError as exc:
            raise self._error(f"元数据字段重复：{exc.field}", path=path) from exc
        except json.JSONDecodeError as exc:
            raise self._error("元数据 JSON 无法解析", path=path) from exc
        if not isinstance(payload, dict):
            raise self._error("元数据必须是 object", path=path)
        try:
            _reject_unknown_fields(payload, _TOP_LEVEL_FIELDS, "元数据")
            schema_version = _exact_int(payload["schema_version"], "schema_version")
            if schema_version != _SCHEMA_VERSION:
                raise ValueError("schema_version")
            phase = WorktreePhase(_exact_str(payload["phase"], "phase"))
            identity = _parse_identity(payload["identity"])
            workspace_root = Path(_exact_str(payload["workspace_root"], "workspace_root"))
            if not workspace_root.is_absolute():
                raise ValueError("workspace_root")
            workspace_root = self._path_policy.assert_target_boundary(workspace_root)
            metadata = WorktreeMetadata(
                schema_version=schema_version,
                phase=phase,
                repository_id=_exact_str(payload["repository_id"], "repository_id"),
                identity=identity,
                workspace_root=workspace_root,
                config_digest=_exact_str(payload["config_digest"], "config_digest"),
                created_at=_parse_utc_datetime(payload["created_at"], "created_at"),
                last_active_at=_parse_utc_datetime(
                    payload["last_active_at"],
                    "last_active_at",
                ),
                initialized_rules=_string_tuple(payload["initialized_rules"], "initialized_rules"),
                retained_reasons=_string_tuple(payload["retained_reasons"], "retained_reasons"),
            )
        except (KeyError, TypeError, ValueError, WorktreeError) as exc:
            if isinstance(exc, WorktreeError):
                raise self._error(f"元数据字段非法：{exc.message}", path=path) from exc
            raise self._error("元数据字段非法", path=path) from exc
        return metadata

    def _error(self, message: str, *, path: Path | None = None) -> WorktreeError:
        return WorktreeError(
            code="invalid_worktree_metadata",
            phase="metadata",
            message=message,
            path=path,
        )


class _DuplicateFieldError(ValueError):
    def __init__(self, field: str) -> None:
        super().__init__(field)
        self.field = field


def _metadata_to_payload(metadata: WorktreeMetadata) -> dict[str, Any]:
    return {
        "schema_version": _SCHEMA_VERSION,
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
        "created_at": _format_utc_datetime(metadata.created_at),
        "last_active_at": _format_utc_datetime(metadata.last_active_at),
        "initialized_rules": list(metadata.initialized_rules),
        "retained_reasons": list(metadata.retained_reasons),
    }


def _canonical_json(payload: dict[str, Any]) -> str:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ) + "\n"


def _reject_duplicate_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateFieldError(key)
        result[key] = value
    return result


def _reject_unknown_fields(raw: dict[str, Any], allowed: frozenset[str], location: str) -> None:
    for key in raw:
        if key not in allowed:
            raise ValueError(f"{location}.{key}")


def _parse_identity(raw: Any) -> WorkspaceTaskIdentity:
    if not isinstance(raw, dict):
        raise ValueError("identity")
    _reject_unknown_fields(raw, _IDENTITY_FIELDS, "identity")
    return WorkspaceTaskIdentity(
        repository_id=_exact_str(raw["repository_id"], "identity.repository_id"),
        task_id=_exact_str(raw["task_id"], "identity.task_id"),
        role_name=_exact_str(raw["role_name"], "identity.role_name"),
        task_token=_exact_str(raw["task_token"], "identity.task_token"),
        relative_name=_exact_str(raw["relative_name"], "identity.relative_name"),
        branch_name=_exact_str(raw["branch_name"], "identity.branch_name"),
        base_commit=_exact_str(raw["base_commit"], "identity.base_commit"),
    )


def _exact_int(value: Any, field_name: str) -> int:
    if type(value) is not int:
        raise ValueError(field_name)
    return value


def _exact_str(value: Any, field_name: str) -> str:
    if type(value) is not str or not value:
        raise ValueError(field_name)
    return value


def _string_tuple(value: Any, field_name: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise ValueError(field_name)
    result: list[str] = []
    for item in value:
        if type(item) is not str:
            raise ValueError(field_name)
        result.append(item)
    return tuple(result)


def _format_utc_datetime(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() != timezone.utc.utcoffset(value):
        raise ValueError("datetime must be UTC")
    return value.isoformat().replace("+00:00", "Z")


def _parse_utc_datetime(value: Any, field_name: str) -> datetime:
    text = _exact_str(value, field_name)
    if not text.endswith("Z"):
        raise ValueError(field_name)
    parsed = datetime.fromisoformat(text[:-1] + "+00:00")
    if parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise ValueError(field_name)
    return parsed


def _is_relative_to(child: Path, parent: Path) -> bool:
    try:
        child_text = os.path.normcase(str(child))
        parent_text = os.path.normcase(str(parent))
        return os.path.commonpath([child_text, parent_text]) == parent_text
    except ValueError:
        return False
