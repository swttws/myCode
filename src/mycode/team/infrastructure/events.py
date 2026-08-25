from __future__ import annotations

import asyncio
import json
import os
import tempfile
import threading
import uuid
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable

from mycode.team.infrastructure.config import TeamConfig
from mycode.team.infrastructure.locking import FileLease
from mycode.team.domain.models import (
    DeliveryReceipt,
    EventFailure,
    EventRecipientType,
    TeamError,
    TeamEvent,
    TeamEventState,
    TeamMessage,
    TeamState,
)
from mycode.team.domain.roles import LEAD_ROLE_NAME
from mycode.team.infrastructure.storage import TeamStore


EVENT_SCHEMA_VERSION = 1
class TeamEventStore:
    """Persistent event log and per-role acknowledgement state for one team."""

    def __init__(self, team_name: str, *, store: TeamStore, config: TeamConfig | None = None) -> None:
        if type(team_name) is not str or not team_name:
            raise ValueError("team_name must be a non-empty string")
        if not isinstance(store, TeamStore):
            raise ValueError("store must be a TeamStore")
        if config is not None and not isinstance(config, TeamConfig):
            raise ValueError("config must be a TeamConfig")
        self._team_name = team_name
        self._store = store
        self._config = TeamConfig() if config is None else config

    @property
    def store(self) -> TeamStore:
        return self._store

    def register_role(self, role_name: str) -> None:
        recipient_type = _recipient_type(role_name)
        with self._locked():
            cursors = self._read_cursors()
            if role_name not in cursors:
                cursors[role_name] = {"recipient_type": recipient_type.value, "acknowledged_sequence": 0}
                _write_json(self._store.event_cursors_path(self._team_name), {"roles": cursors})

    def registered_roles(self) -> tuple[str, ...]:
        with self._locked():
            return tuple(self._read_cursors())

    def append_message(self, message: TeamMessage, *, recipients: tuple[str, ...]) -> DeliveryReceipt:
        if not isinstance(message, TeamMessage):
            raise ValueError("message must be a TeamMessage")
        if not recipients or len(set(recipients)) != len(recipients):
            raise ValueError("recipients must be a non-empty tuple of unique role names")
        if any(type(recipient) is not str or not recipient for recipient in recipients):
            raise ValueError("recipients must contain non-empty strings")
        self._ensure_writable()
        delivered_at = datetime.now(timezone.utc)
        with self._locked():
            cursors = self._read_cursors()
            unknown_recipient = next((recipient for recipient in recipients if recipient not in cursors), None)
            if unknown_recipient is not None:
                raise TeamError(
                    code="unknown_recipient",
                    phase="event_send",
                    message="event recipient is not registered",
                    team_name=self._team_name,
                    member_name=unknown_recipient,
                )
            events = self._read_events()
            existing = [event for event in events if event.message.message_id == message.message_id]
            if existing:
                return DeliveryReceipt(
                    message_id=message.message_id,
                    recipient_names=tuple(event.recipient_name for event in existing),
                    delivered_at=delivered_at,
                    fanout_count=len(existing),
                    duplicate_count=len(existing),
                )
            sequence = events[-1].sequence if events else 0
            normalized = replace(message, timestamp=delivered_at, read=False, delivered=True)
            appended = tuple(
                TeamEvent(
                    event_id=uuid.uuid4().hex,
                    sequence=sequence + index,
                    recipient_name=recipient,
                    recipient_type=_recipient_type(recipient),
                    message=normalized,
                    state=TeamEventState.PENDING,
                    attempts=0,
                    created_at=delivered_at,
                    updated_at=delivered_at,
                )
                for index, recipient in enumerate(recipients, start=1)
            )
            _write_jsonl(self._store.event_log_path(self._team_name), (*events, *appended), _encode_event)
        return DeliveryReceipt(
            message_id=message.message_id,
            recipient_names=recipients,
            delivered_at=delivered_at,
            fanout_count=len(recipients),
        )

    def next_event(self, role_name: str) -> TeamEvent | None:
        _recipient_type(role_name)
        with self._locked():
            cursor = self._read_cursors().get(role_name, {"acknowledged_sequence": 0})
            acknowledged_sequence = cursor["acknowledged_sequence"]
            for event in self._read_events():
                if event.recipient_name == role_name and event.sequence > acknowledged_sequence:
                    if event.state is not TeamEventState.FAILED:
                        return event
        return None

    def events_for_role(self, role_name: str) -> tuple[TeamEvent, ...]:
        """Return all events for a role, including acknowledged ones."""
        _recipient_type(role_name)
        with self._locked():
            return tuple(e for e in self._read_events() if e.recipient_name == role_name)

    def begin_event(self, role_name: str, event_id: str) -> TeamEvent:
        return self._update_event(role_name, event_id, lambda event: replace(
            event, state=TeamEventState.PROCESSING, updated_at=datetime.now(timezone.utc)
        ))

    def ack_event(self, role_name: str, event_id: str) -> TeamEvent:
        now = datetime.now(timezone.utc)
        with self._locked():
            events = self._read_events()
            event = _event_for_role(events, role_name, event_id)
            cursors = self._read_cursors()
            current = cursors.get(role_name, {"acknowledged_sequence": 0})
            acknowledged_sequence = current["acknowledged_sequence"]
            if event.state is TeamEventState.ACKED and event.sequence <= acknowledged_sequence:
                return event
            expected = next(
                (
                    candidate
                    for candidate in events
                    if candidate.recipient_name == role_name
                    and candidate.sequence > acknowledged_sequence
                    and candidate.state not in {TeamEventState.ACKED, TeamEventState.FAILED}
                ),
                None,
            )
            if expected is None or expected.event_id != event.event_id:
                raise TeamError(
                    code="event_sequence_violation",
                    phase="event_ack",
                    message="event must be acknowledged in recipient sequence order",
                    team_name=self._team_name,
                    member_name=role_name,
                )
            updated = replace(event, state=TeamEventState.ACKED, updated_at=now, acknowledged_at=now)
            _write_jsonl(self._store.event_log_path(self._team_name), _replace_event(events, updated), _encode_event)
            current = cursors.get(role_name, {"recipient_type": _recipient_type(role_name).value, "acknowledged_sequence": 0})
            cursors[role_name] = {
                "recipient_type": current["recipient_type"],
                "acknowledged_sequence": max(current["acknowledged_sequence"], updated.sequence),
            }
            _write_json(self._store.event_cursors_path(self._team_name), {"roles": cursors})
            return updated

    def fail_event(
        self,
        role_name: str,
        event_id: str,
        reason: str,
        *,
        reason_code: str = "handler_error",
    ) -> EventFailure | None:
        if type(reason) is not str or not reason:
            raise ValueError("reason must be a non-empty string")
        if type(reason_code) is not str or not reason_code:
            raise ValueError("reason_code must be a non-empty string")
        now = datetime.now(timezone.utc)
        with self._locked():
            events = self._read_events()
            event = _event_for_role(events, role_name, event_id)
            if event.state is TeamEventState.ACKED:
                raise TeamError(
                    code="event_already_acked",
                    phase="event_fail",
                    message="acked events cannot be failed",
                    team_name=self._team_name,
                    member_name=role_name,
                )
            if event.state is TeamEventState.FAILED:
                existing_failure = next(
                    (failure for failure in self._read_failures() if failure.event_id == event_id),
                    None,
                )
                return existing_failure
            attempts = event.attempts + 1
            if attempts < 3:
                updated = replace(event, state=TeamEventState.PENDING, attempts=attempts, updated_at=now)
                _write_jsonl(self._store.event_log_path(self._team_name), _replace_event(events, updated), _encode_event)
                return None
            updated = replace(event, state=TeamEventState.FAILED, attempts=attempts, updated_at=now)
            _write_jsonl(self._store.event_log_path(self._team_name), _replace_event(events, updated), _encode_event)
            failure = EventFailure(
                event_id=event_id,
                team_name=self._team_name,
                recipient_name=role_name,
                recipient_type=event.recipient_type,
                message_id=event.message.message_id,
                protocol=event.message.protocol,
                task_id=event.message.task_id,
                batch_id=event.message.batch_id,
                attempts=attempts,
                reason_code=reason_code,
                reason=reason,
                final_state=TeamEventState.FAILED,
                failed_at=now,
            )
            failures = self._read_failures()
            _write_jsonl(self._store.event_failures_path(self._team_name), (*failures, failure), _encode_failure)
            return failure

    def _update_event(self, role_name: str, event_id: str, update: Callable[[TeamEvent], TeamEvent]) -> TeamEvent:
        with self._locked():
            events = self._read_events()
            updated = update(_event_for_role(events, role_name, event_id))
            _write_jsonl(self._store.event_log_path(self._team_name), _replace_event(events, updated), _encode_event)
            return updated

    def _locked(self):
        lock_path = self._store.event_log_path(self._team_name).with_suffix(".lock")
        return _EventLock(lock_path, self._config, f"events:{self._team_name}")

    def _ensure_writable(self) -> None:
        if self._store.load(self._team_name).team.state is TeamState.ARCHIVED:
            raise TeamError(code="team_archived", phase="event_send", message="team is archived and read-only", team_name=self._team_name)

    def _read_events(self) -> tuple[TeamEvent, ...]:
        return tuple(_decode_event(item) for item in _read_jsonl(self._store.event_log_path(self._team_name)))

    def _read_cursors(self) -> dict[str, dict[str, object]]:
        path = self._store.event_cursors_path(self._team_name)
        if not path.exists():
            return {}
        payload = _read_json(path)
        roles = payload.get("roles")
        if not isinstance(roles, dict):
            raise TeamError(code="event_cursors_corrupt", phase="event_load", message="event cursor data is corrupt", path=path)
        return {name: value for name, value in roles.items() if type(name) is str and isinstance(value, dict)}

    def _read_failures(self) -> tuple[EventFailure, ...]:
        return tuple(_decode_failure(item) for item in _read_jsonl(self._store.event_failures_path(self._team_name)))


class _EventLock:
    def __init__(self, path: Path, config: TeamConfig, owner: str) -> None:
        self._path = path.resolve()
        self._config = config
        self._owner = owner
        self._lease: FileLease | None = None

    def __enter__(self) -> "_EventLock":
        self._lease = _run_coroutine(FileLease.acquire(self._path, config=self._config, owner=self._owner))
        return self

    def __exit__(self, _exc_type: object, _exc_value: object, _traceback: object) -> None:
        if self._lease is not None:
            _run_coroutine(self._lease.release())


def _recipient_type(role_name: str) -> EventRecipientType:
    if type(role_name) is not str or not role_name:
        raise ValueError("role_name must be a non-empty string")
    return EventRecipientType.LEAD if role_name == LEAD_ROLE_NAME else EventRecipientType.MEMBER


def _event_for_role(events: Iterable[TeamEvent], role_name: str, event_id: str) -> TeamEvent:
    for event in events:
        if event.event_id == event_id and event.recipient_name == role_name:
            return event
    raise TeamError(code="event_not_found", phase="event_update", message="event was not found for role", member_name=role_name)


def _replace_event(events: Iterable[TeamEvent], updated: TeamEvent) -> tuple[TeamEvent, ...]:
    return tuple(updated if event.event_id == updated.event_id else event for event in events)


def _encode_event(event: TeamEvent) -> dict[str, object]:
    return {
        "schema_version": EVENT_SCHEMA_VERSION, "event_id": event.event_id, "sequence": event.sequence,
        "recipient_name": event.recipient_name, "recipient_type": event.recipient_type.value,
        "message": _encode_message(event.message), "state": event.state.value, "attempts": event.attempts,
        "created_at": _encode_datetime(event.created_at), "updated_at": _encode_datetime(event.updated_at),
        "acknowledged_at": _encode_datetime(event.acknowledged_at) if event.acknowledged_at else None,
    }


def _decode_event(payload: dict[str, object]) -> TeamEvent:
    message = payload.get("message")
    if not isinstance(message, dict):
        raise ValueError("event message must be an object")
    return TeamEvent(
        event_id=_string(payload, "event_id"), sequence=_integer(payload, "sequence"), recipient_name=_string(payload, "recipient_name"),
        recipient_type=EventRecipientType(_string(payload, "recipient_type")), message=_decode_message(message),
        state=TeamEventState(_string(payload, "state")), attempts=_integer(payload, "attempts"),
        created_at=_datetime(payload, "created_at"), updated_at=_datetime(payload, "updated_at"),
        acknowledged_at=_optional_datetime(payload.get("acknowledged_at")),
    )


def _encode_failure(failure: EventFailure) -> dict[str, object]:
    return {
        "schema_version": EVENT_SCHEMA_VERSION,
        "event_id": failure.event_id,
        "team_name": failure.team_name,
        "recipient_name": failure.recipient_name,
        "recipient_type": failure.recipient_type.value,
        "message_id": failure.message_id,
        "protocol": failure.protocol.value,
        "task_id": failure.task_id,
        "batch_id": failure.batch_id,
        "attempts": failure.attempts,
        "reason_code": failure.reason_code,
        "reason": failure.reason,
        "final_state": failure.final_state.value,
        "failed_at": _encode_datetime(failure.failed_at),
    }


def _decode_failure(payload: dict[str, object]) -> EventFailure:
    from mycode.team.domain.models import MessageProtocol
    return EventFailure(
        event_id=_string(payload, "event_id"),
        team_name=_string(payload, "team_name"),
        recipient_name=_string(payload, "recipient_name"),
        recipient_type=EventRecipientType(_string(payload, "recipient_type")),
        message_id=_string(payload, "message_id"),
        protocol=MessageProtocol(_string(payload, "protocol")),
        task_id=_optional_string(payload.get("task_id")),
        batch_id=_optional_string(payload.get("batch_id")),
        attempts=_integer(payload, "attempts"),
        reason_code=_string(payload, "reason_code"),
        reason=_string(payload, "reason"),
        final_state=TeamEventState(_string(payload, "final_state")),
        failed_at=_datetime(payload, "failed_at"),
    )


def _encode_message(message: TeamMessage) -> dict[str, object]:
    return {"message_id": message.message_id, "protocol": message.protocol.value, "sender": message.sender,
            "target_name": message.target_name, "broadcast": message.broadcast, "body": message.body,
            "summary": message.summary, "timestamp": _encode_datetime(message.timestamp), "read": message.read,
            "delivered": message.delivered, "task_id": message.task_id, "batch_id": message.batch_id}


def _decode_message(payload: dict[str, object]) -> TeamMessage:
    from mycode.team.domain.models import MessageProtocol
    return TeamMessage(message_id=_string(payload, "message_id"), protocol=MessageProtocol(_string(payload, "protocol")),
                       sender=_string(payload, "sender"), target_name=_optional_string(payload.get("target_name")),
                       broadcast=_boolean(payload, "broadcast"), body=_string(payload, "body"), summary=_string(payload, "summary"),
                       timestamp=_datetime(payload, "timestamp"), read=_boolean(payload, "read"), delivered=_boolean(payload, "delivered"),
                       task_id=_optional_string(payload.get("task_id")), batch_id=_optional_string(payload.get("batch_id")))


def _read_jsonl(path: Path) -> tuple[dict[str, object], ...]:
    if not path.exists():
        return ()
    try:
        return tuple(json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line)
    except (OSError, json.JSONDecodeError) as exc:
        raise TeamError(code="event_log_corrupt", phase="event_load", message="event log is corrupt", path=path) from exc


def _read_json(path: Path) -> dict[str, object]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise TeamError(code="event_cursors_corrupt", phase="event_load", message="event cursor data is corrupt", path=path) from exc
    if not isinstance(payload, dict):
        raise TeamError(code="event_cursors_corrupt", phase="event_load", message="event cursor data is corrupt", path=path)
    return payload


def _write_jsonl(path: Path, values: Iterable[object], encoder: Callable[[object], dict[str, object]]) -> None:
    _atomic_write(path, "".join(json.dumps(encoder(value), ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n" for value in values))


def _write_json(path: Path, value: dict[str, object]) -> None:
    _atomic_write(path, json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", newline="\n", dir=path.parent, delete=False) as handle:
            temp_name = handle.name
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    finally:
        if temp_name is not None:
            try:
                Path(temp_name).unlink()
            except FileNotFoundError:
                pass


def _run_coroutine(coro: object) -> object:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)  # type: ignore[arg-type]
    result: list[object] = []
    error: list[BaseException] = []
    def runner() -> None:
        try:
            result.append(asyncio.run(coro))  # type: ignore[arg-type]
        except BaseException as exc:
            error.append(exc)
    thread = threading.Thread(target=runner, daemon=True)
    thread.start()
    thread.join()
    if error:
        raise error[0]
    return result[0]


def _string(payload: dict[str, object], field_name: str) -> str:
    value = payload.get(field_name)
    if type(value) is not str or not value:
        raise ValueError(f"{field_name} must be a non-empty string")
    return value


def _integer(payload: dict[str, object], field_name: str) -> int:
    value = payload.get(field_name)
    if type(value) is not int:
        raise ValueError(f"{field_name} must be an int")
    return value


def _boolean(payload: dict[str, object], field_name: str) -> bool:
    value = payload.get(field_name)
    if type(value) is not bool:
        raise ValueError(f"{field_name} must be a bool")
    return value


def _optional_string(value: object) -> str | None:
    if value is None:
        return None
    if type(value) is not str or not value:
        raise ValueError("optional string must be a non-empty string")
    return value


def _encode_datetime(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def _datetime(payload: dict[str, object], field_name: str) -> datetime:
    return _required_datetime(payload.get(field_name))


def _optional_datetime(value: object) -> datetime | None:
    return None if value is None else _required_datetime(value)


def _required_datetime(value: object) -> datetime:
    if type(value) is not str:
        raise ValueError("datetime must be an ISO string")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("datetime must include timezone")
    return parsed.astimezone(timezone.utc)
