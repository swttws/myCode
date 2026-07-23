from __future__ import annotations

import json
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Sequence

from mycode.llm import ChatMessage, MessageOrigin
from mycode.memory.models import (
    FrameworkContextBlock,
    FrameworkContextKind,
    MemoryDiagnostic,
    SessionRecordType,
    SessionRestoreResult,
    SessionSummary,
)
from mycode.memory.paths import MemoryPaths


_TIME_GAP_NOTICE_SECONDS = 86_400


@dataclass(frozen=True)
class _ParsedMessage:
    message: ChatMessage
    timestamp: datetime


@dataclass(frozen=True)
class _ScanResult:
    summary: SessionSummary
    history: tuple[ChatMessage, ...]
    skipped_lines: int
    truncated_at_boundary: bool
    updated_at: datetime | None
    diagnostics: tuple[MemoryDiagnostic, ...]


class SessionArchiveStore:
    def __init__(
        self,
        *,
        paths: MemoryPaths,
        now: Callable[[], datetime],
        max_age_days: int = 30,
    ) -> None:
        if max_age_days < 1:
            raise ValueError("max_age_days must be positive")
        self._paths = paths
        self._now = now
        self._max_age_days = max_age_days
        self._closed = False
        self._last_selection_now: datetime | None = None
        self._paths.sessions_dir.mkdir(parents=True, exist_ok=True)
        self._current_session_id = self._new_session_id()
        self._current_session_path.touch(exist_ok=True)

    @property
    def current_session_id(self) -> str:
        return self._current_session_id

    def start_new_session(self) -> None:
        self._ensure_open()
        self._last_selection_now = None
        self._current_session_id = self._new_session_id()
        self._current_session_path.touch(exist_ok=True)

    def append_message(self, message: ChatMessage) -> None:
        self._ensure_open()
        self._last_selection_now = None
        record = _message_to_record(message, timestamp=_normalize_datetime(self._now()).isoformat())
        with self._current_session_path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True))
            handle.write("\n")

    def append_messages(self, messages: Sequence[ChatMessage]) -> None:
        for message in messages:
            self.append_message(message)

    def list_sessions(self) -> tuple[SessionSummary, ...]:
        scans = [self._scan_session(path) for path in self._session_paths()]
        summaries = [scan.summary for scan in scans]
        return tuple(
            sorted(
                summaries,
                key=lambda summary: (summary.updated_at or "", summary.session_id),
                reverse=True,
            )
        )

    def latest_recoverable_session(self) -> SessionSummary | None:
        reference_now = _normalize_datetime(self._now())
        self._last_selection_now = reference_now
        return self._latest_recoverable_session(reference_now)

    def restore_latest(self) -> SessionRestoreResult:
        reference_now = self._last_selection_now or _normalize_datetime(self._now())
        self._last_selection_now = None
        summary = self._latest_recoverable_session(reference_now)
        if summary is None:
            return SessionRestoreResult(
                summary=None,
                history=(),
                skipped_lines=0,
                truncated_at_boundary=False,
            )

        scan = self._scan_session(Path(summary.path), reference_now=reference_now)
        time_gap_seconds, time_gap_block = _time_gap(reference_now, scan.updated_at)
        return SessionRestoreResult(
            summary=scan.summary,
            history=scan.history,
            skipped_lines=scan.skipped_lines,
            truncated_at_boundary=scan.truncated_at_boundary,
            time_gap_seconds=time_gap_seconds,
            time_gap_block=time_gap_block,
            diagnostics=scan.diagnostics,
        )

    def cleanup_expired(self) -> tuple[MemoryDiagnostic, ...]:
        reference_now = _normalize_datetime(self._now())
        self._last_selection_now = None
        diagnostics: list[MemoryDiagnostic] = []
        for path in self._session_paths():
            if path.stem == self._current_session_id:
                continue
            scan = self._scan_session(path)
            updated_at = scan.updated_at or _timestamp_from_session_id(path.stem)
            if updated_at is None or not _is_expired(updated_at, reference_now, self._max_age_days):
                continue
            try:
                path.unlink()
            except OSError as exc:
                diagnostics.append(
                    MemoryDiagnostic(
                        code="session_cleanup_failed",
                        message=str(exc),
                        path=str(path),
                    )
                )
        return tuple(diagnostics)

    def close(self) -> None:
        self._closed = True

    @property
    def _current_session_path(self) -> Path:
        return self._paths.sessions_dir / f"{self._current_session_id}.jsonl"

    def _new_session_id(self) -> str:
        while True:
            timestamp = _normalize_datetime(self._now()).strftime("%Y%m%d-%H%M%S")
            suffix = secrets.token_hex(2)
            session_id = f"{timestamp}-{suffix}"
            if not (self._paths.sessions_dir / f"{session_id}.jsonl").exists():
                return session_id

    def _session_paths(self) -> tuple[Path, ...]:
        if not self._paths.sessions_dir.exists():
            return ()
        return tuple(sorted(self._paths.sessions_dir.glob("*.jsonl"), key=lambda path: path.name))

    def _latest_recoverable_session(self, reference_now: datetime) -> SessionSummary | None:
        candidates: list[SessionSummary] = []
        for path in self._session_paths():
            scan = self._scan_session(path, reference_now=reference_now)
            if scan.summary.recoverable:
                candidates.append(scan.summary)
        if not candidates:
            return None
        return max(candidates, key=lambda summary: (summary.updated_at or "", summary.session_id))

    def _scan_session(self, path: Path, reference_now: datetime | None = None) -> _ScanResult:
        parsed_messages: list[_ParsedMessage] = []
        diagnostics: list[MemoryDiagnostic] = []
        skipped_lines = 0

        if not path.exists():
            summary = SessionSummary(
                session_id=path.stem,
                path=str(path),
                title="Untitled session",
                message_count=0,
                updated_at=None,
                recoverable=False,
            )
            return _ScanResult(
                summary=summary,
                history=(),
                skipped_lines=0,
                truncated_at_boundary=False,
                updated_at=None,
                diagnostics=(),
            )

        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError) as exc:
            diagnostics.append(
                MemoryDiagnostic(
                    code="session_unreadable",
                    message=str(exc),
                    path=str(path),
                )
            )
            summary = SessionSummary(
                session_id=path.stem,
                path=str(path),
                title="Untitled session",
                message_count=0,
                updated_at=None,
                recoverable=False,
            )
            return _ScanResult(
                summary=summary,
                history=(),
                skipped_lines=0,
                truncated_at_boundary=False,
                updated_at=None,
                diagnostics=tuple(diagnostics),
            )

        for line_number, line in enumerate(lines, start=1):
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                skipped_lines += 1
                diagnostics.append(
                    MemoryDiagnostic(
                        code="session_json_invalid",
                        message="invalid JSONL record",
                        path=str(path),
                        line=line_number,
                    )
                )
                continue

            parsed = _parse_record(record, path=path, line=line_number, diagnostics=diagnostics)
            if parsed is None:
                skipped_lines += 1
                continue
            parsed_messages.append(parsed)

        parsed_messages, boundary_skipped, truncated_at_boundary, boundary_diagnostics = _truncate_open_tool_boundary(
            parsed_messages,
            path=path,
        )
        skipped_lines += boundary_skipped
        diagnostics.extend(boundary_diagnostics)

        history = tuple(parsed.message for parsed in parsed_messages)
        updated_at = max((parsed.timestamp for parsed in parsed_messages), default=None)
        updated_text = updated_at.isoformat() if updated_at is not None else None
        recoverable = bool(history)
        if reference_now is not None and updated_at is not None:
            recoverable = recoverable and not _is_expired(updated_at, reference_now, self._max_age_days)

        summary = SessionSummary(
            session_id=path.stem,
            path=str(path),
            title=_derive_title(history),
            message_count=len(history),
            updated_at=updated_text,
            recoverable=recoverable,
        )
        return _ScanResult(
            summary=summary,
            history=history,
            skipped_lines=skipped_lines,
            truncated_at_boundary=truncated_at_boundary,
            updated_at=updated_at,
            diagnostics=tuple(diagnostics),
        )

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError("session archive store is closed")


def _message_to_record(message: ChatMessage, *, timestamp: str) -> dict[str, object]:
    origin = message.origin.value if isinstance(message.origin, MessageOrigin) else str(message.origin)
    record: dict[str, object] = {
        "type": SessionRecordType.MESSAGE.value,
        "timestamp": timestamp,
        "role": message.role,
        "content": message.content,
        "origin": origin,
    }
    if message.tool_call_id is not None:
        record["tool_call_id"] = message.tool_call_id
    if message.tool_name is not None:
        record["tool_name"] = message.tool_name
    if message.tool_arguments is not None:
        record["tool_arguments"] = message.tool_arguments
    return record


def _parse_record(
    record: object,
    *,
    path: Path,
    line: int,
    diagnostics: list[MemoryDiagnostic],
) -> _ParsedMessage | None:
    if not isinstance(record, dict):
        diagnostics.append(
            MemoryDiagnostic(
                code="session_record_invalid",
                message="JSONL record is not an object",
                path=str(path),
                line=line,
            )
        )
        return None

    if record.get("type") != SessionRecordType.MESSAGE.value:
        diagnostics.append(
            MemoryDiagnostic(
                code="session_record_invalid",
                message="JSONL record type is not message",
                path=str(path),
                line=line,
            )
        )
        return None

    role = record.get("role")
    content = record.get("content")
    timestamp_value = record.get("timestamp")
    if not isinstance(role, str) or not isinstance(content, str) or not isinstance(timestamp_value, str):
        diagnostics.append(
            MemoryDiagnostic(
                code="session_record_invalid",
                message="message record is missing role, content, or timestamp",
                path=str(path),
                line=line,
            )
        )
        return None

    timestamp = _parse_timestamp(timestamp_value)
    if timestamp is None:
        diagnostics.append(
            MemoryDiagnostic(
                code="session_timestamp_invalid",
                message="message record timestamp is invalid",
                path=str(path),
                line=line,
            )
        )
        return None

    origin = _parse_origin(record.get("origin"), path=path, line=line, diagnostics=diagnostics)
    return _ParsedMessage(
        message=ChatMessage(
            role=role,
            content=content,
            tool_call_id=_optional_string(record.get("tool_call_id")),
            tool_name=_optional_string(record.get("tool_name")),
            tool_arguments=_optional_string(record.get("tool_arguments")),
            origin=origin,
        ),
        timestamp=timestamp,
    )


def _parse_origin(
    value: object,
    *,
    path: Path,
    line: int,
    diagnostics: list[MemoryDiagnostic],
) -> MessageOrigin:
    if isinstance(value, str):
        try:
            return MessageOrigin(value)
        except ValueError:
            diagnostics.append(
                MemoryDiagnostic(
                    code="session_origin_unknown",
                    message="message origin is unknown and was downgraded",
                    path=str(path),
                    line=line,
                )
            )
            return MessageOrigin.CONVERSATION
    return MessageOrigin.CONVERSATION


def _truncate_open_tool_boundary(
    parsed_messages: list[_ParsedMessage],
    *,
    path: Path,
) -> tuple[list[_ParsedMessage], int, bool, tuple[MemoryDiagnostic, ...]]:
    pending_call_id: str | None = None
    pending_index: int | None = None
    for index, parsed in enumerate(parsed_messages):
        message = parsed.message
        if pending_call_id is not None:
            if message.role == "tool" and message.tool_call_id == pending_call_id:
                pending_call_id = None
                pending_index = None
                continue

            # 工具调用必须和结果成对恢复，否则模型会看到悬空协议边界。
            skipped = len(parsed_messages) - pending_index
            return (
                parsed_messages[:pending_index],
                skipped,
                True,
                (
                    MemoryDiagnostic(
                        code="session_tool_boundary_truncated",
                        message="truncated history at an incomplete tool boundary",
                        path=str(path),
                    ),
                ),
            )

        if message.role == "assistant" and message.tool_call_id:
            pending_call_id = message.tool_call_id
            pending_index = index

    if pending_call_id is not None and pending_index is not None:
        skipped = len(parsed_messages) - pending_index
        return (
            parsed_messages[:pending_index],
            skipped,
            True,
            (
                MemoryDiagnostic(
                    code="session_tool_boundary_truncated",
                    message="truncated history at an incomplete tool boundary",
                    path=str(path),
                ),
            ),
        )

    return parsed_messages, 0, False, ()


def _derive_title(history: tuple[ChatMessage, ...]) -> str:
    for message in history:
        if message.role != "user":
            continue
        first_line = next((line.strip() for line in message.content.splitlines() if line.strip()), "")
        if not first_line:
            continue
        return first_line[:80]
    return "Untitled session"


def _time_gap(reference_now: datetime, updated_at: datetime | None) -> tuple[int | None, FrameworkContextBlock | None]:
    if updated_at is None:
        return None, None
    gap_seconds = int((reference_now - updated_at).total_seconds())
    if gap_seconds <= 0:
        return None, None
    if gap_seconds <= _TIME_GAP_NOTICE_SECONDS:
        return gap_seconds, None
    return (
        gap_seconds,
        FrameworkContextBlock(
            id="restore-notice",
            kind=FrameworkContextKind.RESTORE_NOTICE,
            priority=150,
            content=f"Previous project session was restored after {gap_seconds} seconds without activity.",
        ),
    )


def _parse_timestamp(value: str) -> datetime | None:
    try:
        return _normalize_datetime(datetime.fromisoformat(value.replace("Z", "+00:00")))
    except ValueError:
        return None


def _timestamp_from_session_id(session_id: str) -> datetime | None:
    try:
        return _normalize_datetime(datetime.strptime(session_id[:15], "%Y%m%d-%H%M%S"))
    except ValueError:
        return None


def _is_expired(updated_at: datetime, reference_now: datetime, max_age_days: int) -> bool:
    age_seconds = (reference_now - updated_at).total_seconds()
    return age_seconds > max_age_days * 86_400


def _normalize_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _optional_string(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return str(value)
