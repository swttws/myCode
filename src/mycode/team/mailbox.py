from __future__ import annotations

import asyncio
import json
import os
import tempfile
import threading
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Sequence

from mycode.team.config import TeamConfig
from mycode.team.context import JsonConversationMemory
from mycode.team.locking import FileLease
from mycode.team.models import (
    DeliveryReceipt,
    MemberBackend,
    MemberRecord,
    MemberState,
    MessageProtocol,
    TeamError,
    TeamMessage,
    TeamState,
)
from mycode.team.storage import TeamStore


MAILBOX_SCHEMA_VERSION = 1
_REAL_DATETIME = datetime

__all__ = ["MailboxStore"]


@dataclass(frozen=True)
class _LockedMailbox:
    lease: FileLease


class MailboxStore:
    def __init__(
        self,
        team_name: str,
        store: TeamStore | None = None,
        config: TeamConfig | None = None,
        *,
        team_store: TeamStore | None = None,
    ) -> None:
        if type(team_name) is not str or not team_name:
            raise ValueError("team_name must be a non-empty string")
        if store is None:
            store = team_store
        elif team_store is not None and team_store is not store:
            raise ValueError("store and team_store must refer to the same TeamStore")
        if not isinstance(store, TeamStore):
            raise ValueError("store must be a TeamStore")
        if config is None:
            config = TeamConfig()
        elif not isinstance(config, TeamConfig):
            raise ValueError("config must be a TeamConfig")
        self._team_name = team_name
        self._store = store
        self._config = config
        self._registrations: dict[str, MemberRecord] = {}

    def register(self, member: MemberRecord) -> None:
        if not isinstance(member, MemberRecord):
            raise ValueError("member must be a MemberRecord")
        mailbox_path, context_path = self._member_paths(member.member_name)
        if member.mailbox_path is not None and member.mailbox_path.resolve(strict=False) != mailbox_path.resolve(strict=False):
            raise ValueError("member mailbox_path must match the team mailbox path")
        if member.context_path is not None and member.context_path.resolve(strict=False) != context_path.resolve(strict=False):
            raise ValueError("member context_path must match the team context path")
        mailbox_path.parent.mkdir(parents=True, exist_ok=True)
        if not mailbox_path.exists():
            _atomic_write_jsonl(mailbox_path, ())
        context_path.parent.mkdir(parents=True, exist_ok=True)
        if not context_path.exists():
            _atomic_write_json(
                context_path,
                {
                    "version": MAILBOX_SCHEMA_VERSION,
                    "schema_version": MAILBOX_SCHEMA_VERSION,
                    "messages": [],
                    "checkpoint": {},
                    "applied_message_ids": [],
                },
            )
        self._registrations[member.member_name] = member

    def register_lead(self) -> None:
        self.register(
            MemberRecord(
                member_name="lead",
                role_name="lead",
                role_revision=0,
                requested_backend=MemberBackend.IN_PROCESS,
                state=MemberState.IDLE,
                mailbox_path=self._store.mailbox_path(self._team_name, "lead"),
                context_path=self._store.context_path(self._team_name, "lead"),
            )
        )

    def send(self, message: TeamMessage) -> DeliveryReceipt:
        self._validate_message(message)
        self._ensure_writable()
        delivered_at = datetime.now(timezone.utc)
        normalized_message = replace(
            message,
            summary=_truncate_utf8(message.summary, self._config.mailbox_summary_max_bytes),
            timestamp=delivered_at,
            read=False,
            delivered=True,
        )
        encoded_line = _encode_message(normalized_message)

        recipients = self._resolve_recipients(message)
        delivered_names: list[str] = []
        duplicate_count = 0

        for member_name in recipients:
            member = self._member_or_error(member_name)
            mailbox_path, _ = self._member_paths(member.member_name)
            locked = self._acquire_mailbox_lock(mailbox_path, member.member_name)
            try:
                current_messages = _read_mailbox_messages(mailbox_path)
                if any(existing.message_id == normalized_message.message_id for existing in current_messages):
                    duplicate_count += 1
                    continue
                if len((encoded_line + "\n").encode("utf-8")) > self._config.mailbox_message_max_bytes:
                    raise TeamError(
                        code="mailbox_message_too_large",
                        phase="send",
                        message="mailbox message exceeds size limit",
                        team_name=self._team_name,
                        member_name=member_name,
                        path=mailbox_path,
                    )
                _atomic_write_jsonl(mailbox_path, (*current_messages, normalized_message))
                delivered_names.append(member_name)
            finally:
                _release_locked_mailbox(locked)

        return DeliveryReceipt(
            message_id=normalized_message.message_id,
            recipient_names=tuple(delivered_names),
            delivered_at=delivered_at,
            fanout_count=len(delivered_names),
            duplicate_count=duplicate_count,
        )

    def receive(self, member_name: str) -> tuple[TeamMessage, ...]:
        member = self._member_or_error(member_name)
        mailbox_path, _ = self._member_paths(member.member_name)
        locked = self._acquire_mailbox_lock(mailbox_path, member.member_name)
        try:
            return tuple(_read_mailbox_messages(mailbox_path))
        finally:
            _release_locked_mailbox(locked)

    def acknowledge(self, member_name: str, message_id: str) -> None:
        if type(message_id) is not str or not message_id:
            raise ValueError("message_id must be a non-empty string")
        self._ensure_writable()
        member = self._member_or_error(member_name)
        mailbox_path, context_path = self._member_paths(member.member_name)
        context = JsonConversationMemory(path=context_path, max_bytes=self._config.context_max_bytes)
        if not context.checkpoint or message_id not in context.applied_message_ids:
            raise TeamError(
                code="checkpoint_not_ready",
                phase="acknowledge",
                message="message checkpoint has not been applied",
                team_name=self._team_name,
                member_name=member_name,
                path=context_path,
            )

        locked = self._acquire_mailbox_lock(mailbox_path, member.member_name)
        try:
            messages = list(_read_mailbox_messages(mailbox_path))
            matched = False
            updated: list[TeamMessage] = []
            for message in messages:
                if message.message_id == message_id:
                    matched = True
                    if not message.read:
                        message = replace(message, read=True)
                updated.append(message)
            if not matched:
                raise TeamError(
                    code="message_not_found",
                    phase="acknowledge",
                    message="message was not found in the mailbox",
                    team_name=self._team_name,
                    member_name=member_name,
                    path=mailbox_path,
                )
            _atomic_write_jsonl(mailbox_path, updated)
        finally:
            _release_locked_mailbox(locked)

    def unread(self, member_name: str) -> tuple[TeamMessage, ...]:
        return tuple(message for message in self.receive(member_name) if not message.read)

    def _resolve_recipients(self, message: TeamMessage) -> tuple[str, ...]:
        if message.broadcast:
            return tuple(
                sorted(member_name for member_name in self._registrations if member_name != message.sender)
            )
        if message.target_name is None:
            raise ValueError("target_name must be present for non-broadcast messages")
        self._member_or_error(message.target_name)
        return (message.target_name,)

    def _member_or_error(self, member_name: str) -> MemberRecord:
        member = self._registrations.get(member_name)
        if member is None:
            raise TeamError(
                code="unknown_member",
                phase="mailbox",
                message=f"unknown mailbox member: {member_name}",
                team_name=self._team_name,
                member_name=member_name,
            )
        return member

    def _member_paths(self, member_name: str) -> tuple[Path, Path]:
        mailbox_path = self._store.mailbox_path(self._team_name, member_name)
        context_path = self._store.context_path(self._team_name, member_name)
        return mailbox_path, context_path

    def _ensure_writable(self) -> None:
        snapshot = self._store.load(self._team_name)
        if snapshot.team.state is TeamState.ARCHIVED:
            raise TeamError(
                code="team_archived",
                phase="write",
                message="team is archived and read-only",
                team_name=self._team_name,
                revision=snapshot.team.revision,
            )

    def _acquire_mailbox_lock(self, mailbox_path: Path, member_name: str) -> _LockedMailbox:
        lock_path = _mailbox_lock_path(mailbox_path)
        owner = f"mailbox:{self._team_name}:{member_name}"
        lease = _run_coroutine(FileLease.acquire(lock_path, config=self._config, owner=owner))
        return _LockedMailbox(lease=lease)

    def _validate_message(self, message: TeamMessage) -> None:
        if not isinstance(message, TeamMessage):
            raise ValueError("message must be a TeamMessage")
        _require_non_empty_string("message_id", message.message_id)
        _require_enum("protocol", message.protocol, MessageProtocol)
        _require_non_empty_string("sender", message.sender)
        _require_bool("broadcast", message.broadcast)
        _require_non_empty_string("body", message.body)
        _require_non_empty_string("summary", message.summary)
        _require_datetime("timestamp", message.timestamp)
        _require_bool("read", message.read)
        _require_bool("delivered", message.delivered)
        if message.task_id is not None:
            _require_non_empty_string("task_id", message.task_id)
        if message.batch_id is not None:
            _require_non_empty_string("batch_id", message.batch_id)
        if message.broadcast:
            if message.target_name is not None:
                raise ValueError("target_name must be empty for broadcast messages")
        else:
            _require_non_empty_string("target_name", message.target_name)
        if message.protocol is MessageProtocol.BROADCAST and not message.broadcast:
            raise ValueError("broadcast protocol must set broadcast=True")


def _release_locked_mailbox(locked: _LockedMailbox) -> None:
    _run_coroutine(locked.lease.release())


def _read_mailbox_messages(path: Path) -> list[TeamMessage]:
    if not path.exists():
        return []
    raw_lines = path.read_text(encoding="utf-8").splitlines()
    messages: list[TeamMessage] = []
    for line in raw_lines:
        if not line:
            continue
        messages.append(_decode_message(line, path=path))
    return messages


def _decode_message(raw_line: str, *, path: Path) -> TeamMessage:
    try:
        payload = json.loads(raw_line)
    except json.JSONDecodeError as exc:
        raise TeamError(
            code="mailbox_corrupt",
            phase="load",
            message="mailbox JSONL file is corrupt",
            path=path,
        ) from exc
    if not isinstance(payload, dict):
        raise TeamError(
            code="mailbox_corrupt",
            phase="load",
            message="mailbox JSONL file is corrupt",
            path=path,
        )
    schema_version = payload.get("schema_version", payload.get("version", MAILBOX_SCHEMA_VERSION))
    if type(schema_version) is not int or schema_version != MAILBOX_SCHEMA_VERSION:
        raise TeamError(
            code="mailbox_version_unsupported",
            phase="load",
            message="unsupported mailbox schema version",
            path=path,
        )
    try:
        return TeamMessage(
            message_id=_require_string(payload, "message_id"),
            protocol=MessageProtocol(_require_string(payload, "protocol")),
            sender=_require_string(payload, "sender"),
            target_name=_optional_string(payload.get("target_name")),
            broadcast=_require_optional_bool(payload, "broadcast", False),
            body=_require_string(payload, "body"),
            summary=_require_string(payload, "summary"),
            timestamp=_parse_datetime(payload.get("timestamp"), path=path),
            read=_require_optional_bool(payload, "read", False),
            delivered=_require_optional_bool(payload, "delivered", False),
            task_id=_optional_string(payload.get("task_id")),
            batch_id=_optional_string(payload.get("batch_id")),
        )
    except (KeyError, ValueError) as exc:
        raise TeamError(
            code="mailbox_corrupt",
            phase="load",
            message="mailbox JSONL file is corrupt",
            path=path,
        ) from exc


def _encode_message(message: TeamMessage) -> str:
    payload = {
        "schema_version": MAILBOX_SCHEMA_VERSION,
        "message_id": message.message_id,
        "protocol": message.protocol.value,
        "sender": message.sender,
        "broadcast": message.broadcast,
        "body": message.body,
        "summary": message.summary,
        "timestamp": message.timestamp.astimezone(timezone.utc).isoformat(),
        "read": message.read,
        "delivered": message.delivered,
    }
    if message.target_name is not None:
        payload["target_name"] = message.target_name
    if message.task_id is not None:
        payload["task_id"] = message.task_id
    if message.batch_id is not None:
        payload["batch_id"] = message.batch_id
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _atomic_write_jsonl(path: Path, messages: Sequence[TeamMessage]) -> None:
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
            for message in messages:
                encoded = _encode_message(message)
                if len((encoded + "\n").encode("utf-8")) > 0:
                    handle.write(encoded)
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


def _mailbox_lock_path(mailbox_path: Path) -> Path:
    return mailbox_path.with_name(f"{mailbox_path.name}.lock")


def _run_coroutine(coro):
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)

    result: list[object] = []
    error: list[BaseException] = []

    def runner() -> None:
        try:
            result.append(asyncio.run(coro))
        except BaseException as exc:  # pragma: no cover - defensive bridge
            error.append(exc)

    thread = threading.Thread(target=runner, daemon=True)
    thread.start()
    thread.join()
    if error:
        raise error[0]
    return result[0] if result else None


def _truncate_utf8(text: str, max_bytes: int) -> str:
    if len(text.encode("utf-8")) <= max_bytes:
        return text
    encoded = text.encode("utf-8")[:max_bytes]
    while encoded:
        try:
            return encoded.decode("utf-8")
        except UnicodeDecodeError:
            encoded = encoded[:-1]
    return ""


def _parse_datetime(value: object, *, path: Path) -> datetime:
    if type(value) is not str:
        raise ValueError("timestamp must be a string")
    parsed = _REAL_DATETIME.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamp must include timezone information")
    return parsed.astimezone(timezone.utc)


def _require_string(payload: dict[str, object], field_name: str) -> str:
    value = payload[field_name]
    if type(value) is not str or not value:
        raise ValueError(f"{field_name} must be a non-empty string")
    return value


def _optional_string(value: object) -> str | None:
    if value is None:
        return None
    if type(value) is not str:
        raise ValueError("optional string fields must be strings")
    return value


def _require_optional_bool(payload: dict[str, object], field_name: str, default: bool) -> bool:
    value = payload.get(field_name, default)
    if type(value) is not bool:
        raise ValueError(f"{field_name} must be a bool")
    return value


def _require_non_empty_string(field_name: str, value: object) -> None:
    if type(value) is not str or not value:
        raise ValueError(f"{field_name} must be a non-empty string")


def _require_bool(field_name: str, value: object) -> None:
    if type(value) is not bool:
        raise ValueError(f"{field_name} must be a bool")


def _require_enum(field_name: str, value: object, enum_type: type) -> None:
    if not isinstance(value, enum_type):
        raise ValueError(f"{field_name} must be a {enum_type.__name__}")


def _require_datetime(field_name: str, value: object) -> None:
    if not isinstance(value, _REAL_DATETIME):
        raise ValueError(f"{field_name} must be a datetime")
