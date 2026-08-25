from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path

from mycode.team.domain.models import TeamError
from mycode.team.infrastructure.storage import TeamStore


class TeamRequestKind(str, Enum):
    """请求需要 Lead 处理的协作问题类型。"""

    CLARIFICATION = "clarification"  # 成员需要 Lead 澄清业务或技术选择。
    TOOL_APPROVAL = "tool_approval"  # 成员请求一个受权限保护的工具动作。
    PLAN_REVIEW = "plan_review"  # 成员请求 Lead 审核任务计划。
    USER_DECISION = "user_decision"  # Lead 将不确定的业务判断升级给用户。


class TeamRequestState(str, Enum):
    """协作请求从提出到结束的状态。"""

    PENDING = "pending"  # 请求等待 Lead 或用户处理。
    RESOLVED = "resolved"  # 请求已给出可执行的解决方案。
    REJECTED = "rejected"  # 请求被明确拒绝。
    CANCELLED = "cancelled"  # 请求被发起方或系统取消。
    EXPIRED = "expired"  # 请求超过等待期限仍未解决。


@dataclass(frozen=True)
class TeamRequest:
    request_id: str
    team_name: str
    batch_id: str | None
    task_id: str | None
    member_name: str
    kind: TeamRequestKind
    question: str
    options: tuple[str, ...]
    context_summary: str
    state: TeamRequestState
    created_at: datetime
    resolution: str | None = None
    resolved_by: str | None = None
    resolved_at: datetime | None = None

    def __post_init__(self) -> None:
        _require_non_empty("request_id", self.request_id)
        _require_non_empty("team_name", self.team_name)
        _require_non_empty("member_name", self.member_name)
        _require_optional_non_empty("batch_id", self.batch_id)
        _require_optional_non_empty("task_id", self.task_id)
        if not isinstance(self.kind, TeamRequestKind):
            raise ValueError("kind must be a TeamRequestKind")
        _require_non_empty("question", self.question)
        _require_non_empty("context_summary", self.context_summary)
        if not isinstance(self.options, tuple):
            object.__setattr__(self, "options", tuple(self.options))
        seen: set[str] = set()
        for option in self.options:
            _require_non_empty("options item", option)
            if option in seen:
                raise ValueError("options must not contain duplicates")
            seen.add(option)
        if not isinstance(self.state, TeamRequestState):
            raise ValueError("state must be a TeamRequestState")
        _require_utc("created_at", self.created_at)
        _require_optional_non_empty("resolution", self.resolution)
        _require_optional_non_empty("resolved_by", self.resolved_by)
        if self.resolved_at is not None:
            _require_utc("resolved_at", self.resolved_at)
        if self.state is TeamRequestState.PENDING:
            if self.resolution is not None or self.resolved_by is not None or self.resolved_at is not None:
                raise ValueError("pending request cannot contain a resolution")
        elif self.resolution is None or self.resolved_by is None or self.resolved_at is None:
            raise ValueError("resolved request must contain resolution metadata")


class TeamRequestStore:
    def __init__(self, store: TeamStore) -> None:
        if not isinstance(store, TeamStore):
            raise ValueError("store must be a TeamStore")
        self._store = store

    def create(self, request: TeamRequest) -> TeamRequest:
        if not isinstance(request, TeamRequest):
            raise ValueError("request must be a TeamRequest")
        self._store.load(request.team_name)
        self._store.ensure_writable(request.team_name)
        path = self._store.request_path(request.team_name, request.request_id)
        if path.exists():
            current = self.get(request.team_name, request.request_id)
            if current == request:
                return current
            raise ValueError("request already exists")
        _atomic_write_json(path, _encode_request(request))
        return request

    def get(self, team_name: str, request_id: str) -> TeamRequest:
        path = self._store.request_path(team_name, request_id)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise TeamError(
                code="request_not_found",
                phase="request",
                message=f"request not found: {request_id}",
                team_name=team_name,
            ) from exc
        except json.JSONDecodeError as exc:
            raise TeamError(
                code="request_corrupt",
                phase="request",
                message=f"request JSON is corrupt: {request_id}",
                team_name=team_name,
                path=path.resolve(strict=False),
            ) from exc
        return _decode_request(payload)

    def list(
        self,
        team_name: str,
        *,
        state: TeamRequestState | None = None,
    ) -> tuple[TeamRequest, ...]:
        self._store.load(team_name)
        root = self._store.request_root(team_name)
        if not root.exists():
            return ()
        requests = tuple(
            self.get(team_name, path.stem)
            for path in sorted(root.glob("*.json"))
        )
        if state is None:
            return requests
        if not isinstance(state, TeamRequestState):
            raise ValueError("state must be a TeamRequestState")
        return tuple(request for request in requests if request.state is state)

    def resolve(
        self,
        team_name: str,
        request_id: str,
        *,
        state: TeamRequestState,
        resolution: str,
        resolved_by: str,
    ) -> TeamRequest:
        if state is TeamRequestState.PENDING:
            raise ValueError("resolution state must be terminal")
        _require_non_empty("resolution", resolution)
        _require_non_empty("resolved_by", resolved_by)
        current = self.get(team_name, request_id)
        if current.state is not TeamRequestState.PENDING:
            if (
                current.state is state
                and current.resolution == resolution
                and current.resolved_by == resolved_by
            ):
                return current
            raise ValueError("request already resolved")
        updated = replace(
            current,
            state=state,
            resolution=resolution,
            resolved_by=resolved_by,
            resolved_at=datetime.now(timezone.utc),
        )
        self._store.ensure_writable(team_name)
        _atomic_write_json(
            self._store.request_path(team_name, request_id),
            _encode_request(updated),
        )
        return updated


def _encode_request(request: TeamRequest) -> dict[str, object]:
    return {
        "schema_version": 1,
        "request_id": request.request_id,
        "team_name": request.team_name,
        "batch_id": request.batch_id,
        "task_id": request.task_id,
        "member_name": request.member_name,
        "kind": request.kind.value,
        "question": request.question,
        "options": list(request.options),
        "context_summary": request.context_summary,
        "state": request.state.value,
        "created_at": request.created_at.isoformat(),
        "resolution": request.resolution,
        "resolved_by": request.resolved_by,
        "resolved_at": request.resolved_at.isoformat() if request.resolved_at else None,
    }


def _decode_request(payload: object) -> TeamRequest:
    if not isinstance(payload, dict):
        raise ValueError("request JSON must contain an object")
    return TeamRequest(
        request_id=_required_string(payload, "request_id"),
        team_name=_required_string(payload, "team_name"),
        batch_id=_optional_string(payload.get("batch_id")),
        task_id=_optional_string(payload.get("task_id")),
        member_name=_required_string(payload, "member_name"),
        kind=TeamRequestKind(_required_string(payload, "kind")),
        question=_required_string(payload, "question"),
        options=tuple(_string_list(payload.get("options", []))),
        context_summary=_required_string(payload, "context_summary"),
        state=TeamRequestState(_required_string(payload, "state")),
        created_at=_parse_datetime(_required_string(payload, "created_at")),
        resolution=_optional_string(payload.get("resolution")),
        resolved_by=_optional_string(payload.get("resolved_by")),
        resolved_at=(
            _parse_datetime(_required_string(payload, "resolved_at"))
            if payload.get("resolved_at") is not None
            else None
        ),
    )


def _atomic_write_json(path: Path, payload: dict[str, object]) -> None:
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


def _required_string(payload: dict[str, object], name: str) -> str:
    value = payload.get(name)
    _require_non_empty(name, value)
    return value


def _optional_string(value: object) -> str | None:
    if value is None:
        return None
    _require_non_empty("optional string", value)
    return value


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        raise ValueError("options must be a list")
    result: list[str] = []
    for item in value:
        _require_non_empty("options item", item)
        result.append(item)
    return result


def _parse_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    _require_utc("datetime", parsed)
    return parsed


def _require_non_empty(name: str, value: object) -> None:
    if type(value) is not str or not value:
        raise ValueError(f"{name} must be a non-empty string")


def _require_optional_non_empty(name: str, value: object) -> None:
    if value is not None:
        _require_non_empty(name, value)


def _require_utc(name: str, value: object) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() != timezone.utc.utcoffset(value):
        raise ValueError(f"{name} must be a UTC datetime")


__all__ = ["TeamRequest", "TeamRequestKind", "TeamRequestState", "TeamRequestStore"]
