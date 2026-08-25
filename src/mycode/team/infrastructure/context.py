from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Mapping, Sequence

from mycode.llm import ChatMessage, MessageOrigin
from mycode.memory.base import ConversationMemory
from mycode.team.domain.models import TeamError


DEFAULT_CONTEXT_MAX_BYTES = 4 * 1024 * 1024
SCHEMA_VERSION = 1


class JsonConversationMemory(ConversationMemory):
    def __init__(
        self,
        *,
        path: Path | str,
        max_bytes: int = DEFAULT_CONTEXT_MAX_BYTES,
        version: int = SCHEMA_VERSION,
        checkpoint: Mapping[str, object] | None = None,
        applied_message_ids: Sequence[str] | None = None,
    ) -> None:
        if type(max_bytes) is not int or max_bytes <= 0:
            raise ValueError("max_bytes must be a positive integer")
        if type(version) is not int or version <= 0:
            raise ValueError("version must be a positive integer")
        self._path = Path(path).resolve(strict=False)
        self._max_bytes = max_bytes
        self._version = version
        self._messages: list[ChatMessage] = []
        self._checkpoint = dict(checkpoint or {})
        self._applied_message_ids = _normalize_ids(applied_message_ids)
        if self._path.exists():
            self.reload()

    @property
    def path(self) -> Path:
        return self._path

    @property
    def checkpoint(self) -> dict[str, object]:
        return dict(self._checkpoint)

    @property
    def applied_message_ids(self) -> tuple[str, ...]:
        return self._applied_message_ids

    def append(self, message: ChatMessage) -> None:
        messages = [*self._messages, message]
        self._write_state(messages=messages)

    def messages(self) -> list[ChatMessage]:
        return list(self._messages)

    def replace(self, messages: Sequence[ChatMessage]) -> None:
        self._write_state(messages=list(messages))

    def clear(self) -> None:
        self._write_state(messages=[])

    def reload(self) -> None:
        if not self._path.exists():
            self._messages = []
            self._checkpoint = {}
            self._applied_message_ids = ()
            return
        raw = self._path.read_text(encoding="utf-8")
        if len(raw.encode("utf-8")) > self._max_bytes:
            raise _context_error(
                code="context_too_large",
                message="context file exceeds size limit",
                path=self._path,
            )
        payload = _read_json(raw, path=self._path)
        self._messages = [
            _decode_message(item, path=self._path)
            for item in _list_value(payload, "messages", path=self._path)
        ]
        self._checkpoint = _mapping_value(payload, "checkpoint", path=self._path)
        self._applied_message_ids = _normalize_ids(
            _list_value(payload, "applied_message_ids", default=(), path=self._path),
        )

    def set_checkpoint(self, checkpoint: Mapping[str, object] | None) -> None:
        self._checkpoint = dict(checkpoint or {})
        self._persist()

    def set_applied_message_ids(self, message_ids: Sequence[str]) -> None:
        self._applied_message_ids = _normalize_ids(message_ids)
        self._persist()

    def mark_applied(self, message_id: str) -> None:
        if type(message_id) is not str or not message_id:
            raise ValueError("message_id must be a non-empty string")
        if message_id in self._applied_message_ids:
            return
        self._applied_message_ids = (*self._applied_message_ids, message_id)
        self._persist()

    def _write_state(self, *, messages: list[ChatMessage]) -> None:
        encoded = _build_payload(
            messages,
            version=self._version,
            checkpoint=self._checkpoint,
            applied_message_ids=self._applied_message_ids,
        )
        _persist_json(self._path, encoded, max_bytes=self._max_bytes)
        self._messages = list(messages)

    def _persist(self) -> None:
        self._write_state(messages=list(self._messages))


def _build_payload(
    messages: Sequence[ChatMessage],
    *,
    version: int,
    checkpoint: Mapping[str, object],
    applied_message_ids: Sequence[str],
) -> dict[str, object]:
    return {
        "version": version,
        "schema_version": version,
        "messages": [_encode_message(message) for message in messages],
        "checkpoint": dict(checkpoint),
        "applied_message_ids": list(applied_message_ids),
    }


def _persist_json(path: Path, payload: Mapping[str, object], *, max_bytes: int) -> None:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    if len(encoded.encode("utf-8")) > max_bytes:
        raise _context_error(
            code="context_too_large",
            message="context file exceeds size limit",
            path=path,
        )
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
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    finally:
        if temp_name is not None:
            try:
                Path(temp_name).unlink()
            except FileNotFoundError:
                pass


def _read_json(raw: str, *, path: Path) -> dict[str, Any]:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise _context_error(code="context_corrupt", message="corrupt context file", path=path) from exc
    if not isinstance(data, dict):
        raise _context_error(code="context_corrupt", message="corrupt context file", path=path)
    version = data.get("version", data.get("schema_version"))
    if type(version) is not int or version <= 0:
        raise _context_error(code="context_corrupt", message="corrupt context file", path=path)
    if version != SCHEMA_VERSION:
        raise _context_error(code="context_version_unsupported", message="unsupported context version", path=path)
    return data


def _encode_message(message: ChatMessage) -> dict[str, object]:
    return {
        "role": message.role,
        "content": message.content,
        "tool_call_id": message.tool_call_id,
        "tool_name": message.tool_name,
        "tool_arguments": message.tool_arguments,
        "origin": message.origin.value if isinstance(message.origin, MessageOrigin) else str(message.origin),
    }


def _decode_message(data: object, *, path: Path) -> ChatMessage:
    if not isinstance(data, dict):
        raise _context_error(code="context_corrupt", message="corrupt context file", path=path)
    role = _string(data, "role", path=path)
    content = _string(data, "content", path=path)
    origin = data.get("origin", MessageOrigin.CONVERSATION.value)
    if type(origin) is not str:
        raise _context_error(code="context_corrupt", message="corrupt context file", path=path)
    try:
        parsed_origin = MessageOrigin(origin)
    except ValueError as exc:
        raise _context_error(code="context_corrupt", message="corrupt context file", path=path) from exc
    return ChatMessage(
        role=role,
        content=content,
        tool_call_id=_optional_string(data.get("tool_call_id")),
        tool_name=_optional_string(data.get("tool_name")),
        tool_arguments=_optional_string(data.get("tool_arguments")),
        origin=parsed_origin,
    )


def _list_value(data: Mapping[str, Any], field_name: str, default: Sequence[Any] = (), *, path: Path) -> list[Any]:
    value = data.get(field_name, default)
    if not isinstance(value, list | tuple):
        raise _context_error(code="context_corrupt", message="corrupt context file", path=path)
    return list(value)


def _mapping_value(data: Mapping[str, Any], field_name: str, *, path: Path) -> dict[str, object]:
    value = data.get(field_name, {})
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise _context_error(code="context_corrupt", message="corrupt context file", path=path)
    return {str(key): item for key, item in value.items()}


def _normalize_ids(message_ids: Sequence[str] | None) -> tuple[str, ...]:
    if message_ids is None:
        return ()
    normalized: list[str] = []
    seen: set[str] = set()
    for message_id in message_ids:
        if type(message_id) is not str or not message_id:
            raise ValueError("applied_message_ids must contain non-empty strings")
        if message_id in seen:
            continue
        seen.add(message_id)
        normalized.append(message_id)
    return tuple(normalized)


def _string(data: Mapping[str, Any], field_name: str, *, path: Path) -> str:
    value = data.get(field_name)
    if type(value) is not str or not value:
        raise _context_error(code="context_corrupt", message="corrupt context file", path=path)
    return value


def _optional_string(value: object) -> str | None:
    if value is None:
        return None
    if type(value) is not str:
        return str(value)
    return value


def _context_error(*, code: str, message: str, path: Path) -> TeamError:
    return TeamError(code=code, phase="context", message=message, path=path)
