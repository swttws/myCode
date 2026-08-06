from __future__ import annotations

import asyncio
import json
import os
import secrets
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from mycode.team import TeamError
from mycode.team.config import TeamConfig


@dataclass(frozen=True)
class FileLease:
    path: Path
    owner: str
    token: str
    acquired_at: datetime
    process_id: int

    def __post_init__(self) -> None:
        _require_absolute_path("path", self.path)
        _require_non_empty_string("owner", self.owner)
        _require_non_empty_string("token", self.token)
        _require_utc_datetime("acquired_at", self.acquired_at)
        _require_non_negative_int("process_id", self.process_id)

    @classmethod
    async def acquire(cls, path: Path, *, config: TeamConfig, owner: str) -> "FileLease":
        if not isinstance(config, TeamConfig):
            raise ValueError("config must be a TeamConfig")
        _require_absolute_path("path", path)
        _require_non_empty_string("owner", owner)
        path.parent.mkdir(parents=True, exist_ok=True)
        deadline = time.monotonic() + config.lock_timeout_seconds

        while True:
            token = secrets.token_urlsafe(16)
            lease = cls(
                path=path,
                owner=owner,
                token=token,
                acquired_at=datetime.now(timezone.utc),
                process_id=os.getpid(),
            )
            try:
                await asyncio.to_thread(_write_lock, lease)
                return lease
            except FileExistsError:
                if await asyncio.to_thread(_maybe_reclaim_stale_lock, path, config):
                    continue
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TeamError(
                        code="lock_timeout",
                        phase="acquire",
                        message="timed out waiting for lock",
                        path=path,
                    )
                await asyncio.sleep(min(config.lock_retry_interval_seconds, remaining))

    async def release(self) -> None:
        record = await asyncio.to_thread(_read_lock_record, self.path)
        if record is None or record["token"] != self.token or record["owner"] != self.owner:
            raise TeamError(
                code="lock_not_owned",
                phase="release",
                message="lock is not owned by this lease",
                path=self.path,
            )
        try:
            await asyncio.to_thread(self.path.unlink)
        except FileNotFoundError as exc:
            raise TeamError(
                code="lock_not_owned",
                phase="release",
                message="lock is not owned by this lease",
                path=self.path,
            ) from exc


def _write_lock(lease: FileLease) -> None:
    payload = {
        "owner": lease.owner,
        "token": lease.token,
        "created_at": lease.acquired_at.isoformat(),
        "process_id": lease.process_id,
    }
    with lease.path.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, ensure_ascii=False, sort_keys=True)
        handle.write("\n")


def _maybe_reclaim_stale_lock(path: Path, config: TeamConfig) -> bool:
    record = _read_lock_record(path)
    if record is None:
        return True
    created_at = record.get("created_at")
    if isinstance(created_at, datetime):
        age_seconds = (datetime.now(timezone.utc) - created_at).total_seconds()
    else:
        try:
            age_seconds = (datetime.now(timezone.utc) - datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)).total_seconds()
        except OSError:
            return True
    if age_seconds < config.lock_stale_after_seconds:
        return False
    process_id = record.get("process_id")
    if type(process_id) is int and _process_is_alive(process_id):
        return False
    try:
        path.unlink()
    except FileNotFoundError:
        return True
    except OSError:
        return False
    return True


def _read_lock_record(path: Path) -> dict[str, object] | None:
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    owner = data.get("owner")
    token = data.get("token")
    created_at_raw = data.get("created_at")
    process_id = data.get("process_id")
    if type(owner) is not str or type(token) is not str:
        return None
    created_at = _parse_datetime(created_at_raw)
    return {
        "owner": owner,
        "token": token,
        "created_at": created_at,
        "process_id": process_id if type(process_id) is int else -1,
    }


def _parse_datetime(value: object) -> datetime | None:
    if type(value) is not str:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc)


def _process_is_alive(process_id: int) -> bool:
    if process_id <= 0:
        return False
    if process_id == os.getpid():
        return True
    if os.name == "nt":
        try:
            import ctypes

            handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, process_id)
            if handle:
                ctypes.windll.kernel32.CloseHandle(handle)
                return True
            return False
        except Exception:
            return False
    try:
        os.kill(process_id, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _require_absolute_path(field_name: str, value: object) -> None:
    if not isinstance(value, Path):
        raise ValueError(f"{field_name} must be a Path")
    if not value.is_absolute():
        raise ValueError(f"{field_name} must be an absolute path")


def _require_non_empty_string(field_name: str, value: object) -> None:
    if type(value) is not str or not value:
        raise ValueError(f"{field_name} must be a non-empty string")


def _require_non_negative_int(field_name: str, value: object) -> None:
    if type(value) is not int or value < 0:
        raise ValueError(f"{field_name} must be a non-negative integer")


def _require_utc_datetime(field_name: str, value: object) -> None:
    if not isinstance(value, datetime):
        raise ValueError(f"{field_name} must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must include UTC timezone")
    if value.utcoffset() != timezone.utc.utcoffset(value):
        raise ValueError(f"{field_name} must use UTC timezone")
