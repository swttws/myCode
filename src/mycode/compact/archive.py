from __future__ import annotations

import json
import shutil
import time
import uuid
from hashlib import sha256
from pathlib import Path
from typing import Callable, IO


STALE_AFTER_SECONDS = 86_400


class ArchiveSession:
    """Own the filesystem location and activity lock for one archive session."""

    def __init__(
        self,
        workspace: str | Path,
        *,
        home: str | Path | None = None,
        session_id: str | None = None,
        clock: Callable[[], float] = time.time,
        stale_after_seconds: float = STALE_AFTER_SECONDS,
    ) -> None:
        self.workspace = Path(workspace).resolve()
        self.workspace_hash = sha256(str(self.workspace).encode("utf-8")).hexdigest()
        self.session_id = session_id or str(uuid.uuid4())
        self._clock = clock
        self._stale_after_seconds = stale_after_seconds

        cache_home = Path.home() if home is None else Path(home)
        self.context_dir = cache_home / ".mycode" / "projects" / self.workspace_hash / "context"
        self._remove_stale_sessions()

        self.session_dir = self.context_dir / self.session_id
        self.session_dir.mkdir(parents=True, exist_ok=False)
        self._write_registration()
        self._lock = _ActivityLock(self.session_dir / "session.lock")
        if not self._lock.acquire():
            raise RuntimeError(f"archive session is already active: {self.session_dir}")

    def close(self) -> None:
        self._lock.release()

    def __enter__(self) -> ArchiveSession:
        return self

    def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> None:
        self.close()

    def _remove_stale_sessions(self) -> None:
        if not self.context_dir.is_dir():
            return

        for session_dir in self.context_dir.iterdir():
            if not session_dir.is_dir() or not self._is_stale(session_dir):
                continue
            lock = _ActivityLock(session_dir / "session.lock")
            if lock.acquire():
                lock.release()
                shutil.rmtree(session_dir)

    def _is_stale(self, session_dir: Path) -> bool:
        return self._clock() - self._registered_at(session_dir) > self._stale_after_seconds

    @staticmethod
    def _registered_at(session_dir: Path) -> float:
        registration = session_dir / "session.json"
        try:
            value = json.loads(registration.read_text(encoding="utf-8")).get("created_at")
        except (OSError, ValueError, json.JSONDecodeError):
            value = None
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return float(value)
        return session_dir.stat().st_mtime

    def _write_registration(self) -> None:
        registration = {"created_at": self._clock(), "session_id": self.session_id}
        (self.session_dir / "session.json").write_text(
            json.dumps(registration, sort_keys=True),
            encoding="utf-8",
        )


class _ActivityLock:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._file: IO[bytes] | None = None

    def acquire(self) -> bool:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        try:
            file = self._path.open("a+b")
        except OSError:
            return False
        try:
            file.seek(0)
            if not file.read(1):
                file.seek(0)
                file.write(b"\0")
                file.flush()
            if _is_windows():
                import msvcrt

                file.seek(0)
                msvcrt.locking(file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            file.close()
            return False

        self._file = file
        return True

    def release(self) -> None:
        if self._file is None:
            return
        try:
            if _is_windows():
                import msvcrt

                self._file.seek(0)
                msvcrt.locking(self._file.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(self._file.fileno(), fcntl.LOCK_UN)
        finally:
            self._file.close()
            self._file = None


def _is_windows() -> bool:
    return __import__("os").name == "nt"
