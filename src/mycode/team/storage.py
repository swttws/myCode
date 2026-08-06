from __future__ import annotations

import json
import os
import tempfile
from dataclasses import fields, is_dataclass, replace
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

from mycode.team.models import (
    ApprovalState,
    BatchRecord,
    BatchState,
    MemberBackend,
    MemberRecord,
    MemberState,
    ResolvedBackend,
    TaskKind,
    TaskResult,
    TeamError,
    TeamRecord,
    TeamSnapshot,
    TeamState,
    TeamTask,
    TeamTaskState,
    WakeEndpoint,
)


SCHEMA_VERSION = 1


class TeamStore:
    def __init__(self, *, home: Path | None = None) -> None:
        self.home = (Path.home() if home is None else home).resolve()
        self.teams_root = self.home / ".mycode" / "teams"

    def team_root(self, team_name: str) -> Path:
        return self._bounded_path(self.teams_root, _safe_segment(team_name))

    def mailbox_path(self, team_name: str, member_name: str) -> Path:
        return self.member_root(team_name, member_name) / "mailbox.jsonl"

    def context_path(self, team_name: str, member_name: str) -> Path:
        return self.member_root(team_name, member_name) / "context.json"

    def lead_lock_path(self, team_name: str) -> Path:
        return self.team_root(team_name) / "lead.lock"

    def member_root(self, team_name: str, member_name: str) -> Path:
        root = self.team_root(team_name) / "members" / _safe_segment(member_name)
        return self._bounded_path(self.team_root(team_name), root.relative_to(self.team_root(team_name)))

    def batch_root(self, team_name: str, batch_id: str) -> Path:
        root = self.team_root(team_name) / "batches" / _safe_segment(batch_id)
        return self._bounded_path(self.team_root(team_name), root.relative_to(self.team_root(team_name)))

    def create(self, team: TeamRecord) -> TeamSnapshot:
        root = self.team_root(team.team_name)
        root.mkdir(parents=True, exist_ok=True)
        (root / "members").mkdir(exist_ok=True)
        (root / "batches").mkdir(exist_ok=True)
        _atomic_write_json(root / "team.json", _encode_record(team))
        _atomic_write_json(root / "registry.json", {"schema_version": SCHEMA_VERSION, "entries": {}})
        return TeamSnapshot(team=team, members=(), batches=(), registry={})

    def load(self, team_name: str) -> TeamSnapshot:
        root = self.team_root(team_name)
        team = _decode_team_record(_read_json(root / "team.json"))
        members = tuple(
            _decode_member_record(_read_json(path))
            for path in sorted((root / "members").glob("*/member.json"))
        )
        batches = tuple(
            _decode_batch_record(_read_json(path))
            for path in sorted((root / "batches").glob("*/batch.json"))
        )
        registry = self._read_registry(root / "registry.json")
        return TeamSnapshot(team=team, members=members, batches=batches, registry=registry)

    def save(self, snapshot: TeamSnapshot) -> None:
        self._ensure_writable(snapshot.team.team_name)
        root = self.team_root(snapshot.team.team_name)
        root.mkdir(parents=True, exist_ok=True)
        _atomic_write_json(root / "team.json", _encode_record(snapshot.team))
        _atomic_write_json(root / "registry.json", _encode_registry(snapshot.registry))
        for member in snapshot.members:
            self.write_member(snapshot.team.team_name, member)
        for batch in snapshot.batches:
            self.write_batch(snapshot.team.team_name, batch)

    def write_member(self, team_name: str, member: MemberRecord) -> None:
        self._ensure_writable(team_name)
        root = self.member_root(team_name, member.member_name)
        root.mkdir(parents=True, exist_ok=True)
        _atomic_write_json(root / "member.json", _encode_record(member))
        self.mailbox_path(team_name, member.member_name).touch(exist_ok=True)
        if not self.context_path(team_name, member.member_name).exists():
            _atomic_write_json(
                self.context_path(team_name, member.member_name),
                {"schema_version": SCHEMA_VERSION, "messages": [], "applied_message_ids": []},
            )

    def write_batch(self, team_name: str, batch: BatchRecord) -> None:
        self._ensure_writable(team_name)
        root = self.batch_root(team_name, batch.batch_id)
        (root / "tasks").mkdir(parents=True, exist_ok=True)
        _atomic_write_json(root / "batch.json", _encode_record(batch))

    def write_task(self, team_name: str, batch_id: str, task: TeamTask) -> None:
        self._ensure_writable(team_name)
        if task.batch_id != batch_id:
            raise ValueError("task batch_id must match batch_id")
        root = self.batch_root(team_name, batch_id) / "tasks"
        root.mkdir(parents=True, exist_ok=True)
        path = self._bounded_path(root, f"{_safe_segment(task.task_id)}.json")
        _atomic_write_json(path, _encode_record(task))

    def read_task(self, team_name: str, batch_id: str, task_id: str) -> TeamTask:
        root = self.batch_root(team_name, batch_id) / "tasks"
        path = self._bounded_path(root, f"{_safe_segment(task_id)}.json")
        return _decode_team_task(_read_json(path))

    def list_tasks(self, team_name: str, batch_id: str) -> tuple[TeamTask, ...]:
        root = self.batch_root(team_name, batch_id) / "tasks"
        if not root.exists():
            return ()
        return tuple(_decode_team_task(_read_json(path)) for path in sorted(root.glob("*.json")))

    def write_registry(self, team_name: str, registry: Mapping[str, WakeEndpoint]) -> None:
        self._ensure_writable(team_name)
        for member_name, endpoint in registry.items():
            if member_name != endpoint.member_name:
                raise ValueError("registry key must match endpoint member_name")
        _atomic_write_json(self.team_root(team_name) / "registry.json", _encode_registry(registry))

    def archive(self, team_name: str) -> TeamRecord:
        snapshot = self.load(team_name)
        if snapshot.team.state is TeamState.ARCHIVED:
            return snapshot.team
        archived = replace(
            snapshot.team,
            state=TeamState.ARCHIVED,
            revision=snapshot.team.revision + 1,
            updated_at=datetime.now(timezone.utc),
        )
        _atomic_write_json(self.team_root(team_name) / "team.json", _encode_record(archived))
        return archived

    def _read_registry(self, path: Path) -> Mapping[str, WakeEndpoint]:
        if not path.exists():
            return MappingProxyType({})
        data = _read_json(path)
        entries = data.get("entries", data)
        if not isinstance(entries, dict):
            raise TeamError(
                code="invalid_registry",
                phase="load",
                message="registry JSON must contain an entries object",
                path=path.resolve(),
            )
        return MappingProxyType(
            {
                str(member_name): _decode_wake_endpoint(endpoint)
                for member_name, endpoint in entries.items()
            },
        )

    def _ensure_writable(self, team_name: str) -> None:
        team_path = self.team_root(team_name) / "team.json"
        if not team_path.exists():
            return
        team = _decode_team_record(_read_json(team_path))
        if team.state is TeamState.ARCHIVED:
            raise TeamError(
                code="team_archived",
                phase="write",
                message="team is archived and read-only",
                team_name=team_name,
                path=team_path.resolve(),
                revision=team.revision,
            )

    def _bounded_path(self, root: Path, relative: str | os.PathLike[str]) -> Path:
        root_resolved = root.resolve(strict=False)
        candidate = (root_resolved / relative).resolve(strict=False)
        try:
            candidate.relative_to(root_resolved)
        except ValueError as exc:
            raise ValueError("team path escapes teams root") from exc
        return candidate


def _safe_segment(value: str) -> str:
    if type(value) is not str or value in {"", ".", ".."}:
        raise ValueError("team path segment must be a safe non-empty name")
    if "/" in value or "\\" in value:
        raise ValueError("team path segment must not contain path separators")
    return value


def _atomic_write_json(path: Path, payload: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            newline="\n",
            dir=path.parent,
            prefix=f"{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temp_name = handle.name
            json.dump(payload, handle, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    finally:
        if temp_name is not None:
            try:
                Path(temp_name).unlink()
            except FileNotFoundError:
                pass


def _read_json(path: Path) -> dict[str, Any]:
    try:
        raw = path.read_text(encoding="utf-8")
        data = json.loads(raw)
    except FileNotFoundError as exc:
        raise TeamError(
            code="missing_json",
            phase="load",
            message=f"missing JSON file: {path.name}",
            path=path.resolve(strict=False),
        ) from exc
    except json.JSONDecodeError as exc:
        raise TeamError(
            code="corrupt_json",
            phase="load",
            message=f"corrupt JSON file: {path.name}",
            path=path.resolve(strict=False),
        ) from exc
    if not isinstance(data, dict):
        raise TeamError(
            code="invalid_json",
            phase="load",
            message=f"JSON file must contain an object: {path.name}",
            path=path.resolve(strict=False),
        )
    return data


def _encode_record(record: object) -> dict[str, object]:
    return {"schema_version": SCHEMA_VERSION, **_encode_dataclass(record)}


def _encode_registry(registry: Mapping[str, WakeEndpoint]) -> dict[str, object]:
    return {
        "schema_version": SCHEMA_VERSION,
        "entries": {member_name: _encode_dataclass(endpoint) for member_name, endpoint in registry.items()},
    }


def _encode_value(value: object) -> object:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat()
    if is_dataclass(value):
        return _encode_dataclass(value)
    if isinstance(value, Mapping):
        return {str(key): _encode_value(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_encode_value(item) for item in value]
    return value


def _encode_dataclass(record: object) -> dict[str, object]:
    return {
        field.name: _encode_value(getattr(record, field.name))
        for field in fields(record)
        if getattr(record, field.name) is not None
    }


def _decode_team_record(data: Mapping[str, Any]) -> TeamRecord:
    return TeamRecord(
        team_name=_string(data, "team_name"),
        repository_root=_path(data, "repository_root"),
        repository_id=_string(data, "repository_id"),
        target_branch=_string(data, "target_branch"),
        state=TeamState(_string(data, "state")),
        revision=_int(data, "revision", 0),
        lead_owner=_optional_string(data, "lead_owner"),
        max_members=_int(data, "max_members", 16),
        max_active_members=_int(data, "max_active_members", 4),
        created_at=_optional_datetime(data, "created_at"),
        updated_at=_optional_datetime(data, "updated_at"),
    )


def _decode_member_record(data: Mapping[str, Any]) -> MemberRecord:
    wake_raw = data.get("wake_endpoint")
    return MemberRecord(
        member_name=_string(data, "member_name"),
        role_name=_string(data, "role_name"),
        role_revision=_int(data, "role_revision", 0),
        requested_backend=MemberBackend(_string(data, "requested_backend")),
        resolved_backend=_optional_enum(data, "resolved_backend", ResolvedBackend),
        state=MemberState(_string(data, "state")),
        approval_required=_bool(data, "approval_required", False),
        worktree_root=_optional_path(data, "worktree_root"),
        branch_name=_optional_string(data, "branch_name"),
        mailbox_path=_optional_path(data, "mailbox_path"),
        context_path=_optional_path(data, "context_path"),
        wake_endpoint=_decode_wake_endpoint(wake_raw) if wake_raw is not None else None,
        task_id=_optional_string(data, "task_id"),
        batch_id=_optional_string(data, "batch_id"),
        revision=_int(data, "revision", 0),
        created_at=_optional_datetime(data, "created_at"),
        updated_at=_optional_datetime(data, "updated_at"),
        last_seen_at=_optional_datetime(data, "last_seen_at"),
    )


def _decode_batch_record(data: Mapping[str, Any]) -> BatchRecord:
    return BatchRecord(
        batch_id=_string(data, "batch_id"),
        goal=_string(data, "goal"),
        baseline_commit=_string(data, "baseline_commit"),
        state=BatchState(_string(data, "state")),
        task_id=_optional_string(data, "task_id"),
        revision=_int(data, "revision", 0),
        integration_diagnostics=_string_tuple(data, "integration_diagnostics"),
        created_at=_optional_datetime(data, "created_at"),
        updated_at=_optional_datetime(data, "updated_at"),
        completed_at=_optional_datetime(data, "completed_at"),
        result_commit_id=_optional_string(data, "result_commit_id"),
    )


def _decode_team_task(data: Mapping[str, Any]) -> TeamTask:
    result_raw = data.get("result")
    return TeamTask(
        task_id=_string(data, "task_id"),
        batch_id=_string(data, "batch_id"),
        title=_string(data, "title"),
        description=_string(data, "description"),
        dependency_ids=_string_tuple(data, "dependency_ids"),
        kind=TaskKind(_string(data, "kind")),
        owner=_optional_string(data, "owner"),
        state=TeamTaskState(_string(data, "state")),
        plan_revision=_int(data, "plan_revision", 0),
        approval_state=ApprovalState(_string(data, "approval_state")),
        result=_decode_task_result(result_raw) if result_raw is not None else None,
        error=_optional_string(data, "error"),
        revision=_int(data, "revision", 0),
        created_at=_optional_datetime(data, "created_at"),
        updated_at=_optional_datetime(data, "updated_at"),
    )


def _decode_task_result(data: object) -> TaskResult:
    if not isinstance(data, Mapping):
        raise ValueError("task result must be an object")
    return TaskResult(
        summary=_string(data, "summary"),
        commit_id=_optional_string(data, "commit_id"),
        verification_summary=_optional_string(data, "verification_summary"),
        details=_optional_string(data, "details"),
        artifact_paths=tuple(Path(item) for item in data.get("artifact_paths", ())),
        diagnostics=_string_tuple(data, "diagnostics"),
    )


def _decode_wake_endpoint(data: object) -> WakeEndpoint:
    if not isinstance(data, Mapping):
        raise ValueError("wake endpoint must be an object")
    return WakeEndpoint(
        member_name=_string(data, "member_name"),
        backend=ResolvedBackend(_string(data, "backend")),
        endpoint=_string(data, "endpoint"),
        revision=_int(data, "revision", 0),
    )


def _string(data: Mapping[str, Any], field_name: str) -> str:
    value = data[field_name]
    if type(value) is not str:
        raise ValueError(f"{field_name} must be a string")
    return value


def _optional_string(data: Mapping[str, Any], field_name: str) -> str | None:
    value = data.get(field_name)
    if value is None:
        return None
    if type(value) is not str:
        raise ValueError(f"{field_name} must be a string")
    return value


def _int(data: Mapping[str, Any], field_name: str, default: int) -> int:
    value = data.get(field_name, default)
    if type(value) is not int:
        raise ValueError(f"{field_name} must be an int")
    return value


def _bool(data: Mapping[str, Any], field_name: str, default: bool) -> bool:
    value = data.get(field_name, default)
    if type(value) is not bool:
        raise ValueError(f"{field_name} must be a bool")
    return value


def _path(data: Mapping[str, Any], field_name: str) -> Path:
    return Path(_string(data, field_name))


def _optional_path(data: Mapping[str, Any], field_name: str) -> Path | None:
    value = _optional_string(data, field_name)
    return Path(value) if value is not None else None


def _optional_datetime(data: Mapping[str, Any], field_name: str) -> datetime | None:
    value = _optional_string(data, field_name)
    if value is None:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed.astimezone(timezone.utc)


def _optional_enum(
    data: Mapping[str, Any],
    field_name: str,
    enum_type: type[Enum],
) -> Any | None:
    value = _optional_string(data, field_name)
    return enum_type(value) if value is not None else None


def _string_tuple(data: Mapping[str, Any], field_name: str) -> tuple[str, ...]:
    value = data.get(field_name, ())
    if not isinstance(value, list | tuple):
        raise ValueError(f"{field_name} must be a list")
    return tuple(str(item) for item in value)
