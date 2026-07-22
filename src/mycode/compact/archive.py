from __future__ import annotations

import json
import os
import shutil
import time
import uuid
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Callable, IO, Literal

from mycode.compact.estimator import TokenEstimator
from mycode.compact.models import PREVIEW_ALLOWANCE_TOKENS, ArchivedArtifact, ArtifactSlice
from mycode.tool import ToolArguments, ToolDefinition, ToolKind, ToolResult


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
        self._estimator = TokenEstimator()
        self._allowed_artifacts: dict[Path, str] = {}

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

    @property
    def allowed_paths(self) -> frozenset[Path]:
        return frozenset(self._allowed_artifacts)

    def begin(self) -> ArchiveTransaction:
        return ArchiveTransaction(self)

    def read(
        self,
        path: str | Path,
        *,
        offset: int = 0,
        max_tokens: int = PREVIEW_ALLOWANCE_TOKENS,
    ) -> ArtifactSlice:
        _validate_offset(offset)
        _validate_max_tokens(max_tokens)
        artifact_path, registered_sha256 = _resolve_registered_artifact(
            path,
            self._allowed_artifacts,
        )
        text = _read_verified_text(artifact_path, registered_sha256=registered_sha256)
        if offset >= len(text):
            return ArtifactSlice(
                path=str(artifact_path),
                text="",
                next_offset=len(text),
                eof=True,
            )

        end = _slice_end_for_budget(
            text,
            offset=offset,
            max_tokens=max_tokens,
            estimator=self._estimator,
        )
        return ArtifactSlice(
            path=str(artifact_path),
            text=text[offset:end],
            next_offset=end,
            eof=end >= len(text),
        )

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

    def _register_paths(self, artifacts: tuple[ArchivedArtifact, ...]) -> None:
        self._allowed_artifacts.update(
            (Path(artifact.path).resolve(strict=True), artifact.sha256)
            for artifact in artifacts
        )


class ArtifactStore(ArchiveSession):
    """Plan-facing archive store name; ArchiveSession keeps T7 compatibility."""


@dataclass(frozen=True)
class _PendingArtifact:
    temp_path: Path
    final_path: Path
    artifact: ArchivedArtifact


class ArchiveTransaction:
    def __init__(self, session: ArchiveSession) -> None:
        self._session = session
        self._pending: list[_PendingArtifact] = []
        self._finished = False

    def archive_text(
        self,
        *,
        kind: Literal["tool_result", "user_message", "history"],
        text: str,
    ) -> ArchivedArtifact:
        self._ensure_open()
        artifact_dir = self._session.session_dir / "artifacts"
        temp_dir = self._session.session_dir / "tmp"
        artifact_id = uuid.uuid4().hex[:16]
        final_path = artifact_dir / f"{artifact_id}.json"
        temp_path = temp_dir / f"{artifact_id}.tmp"
        text_bytes = text.encode("utf-8")
        text_sha256 = sha256(text_bytes).hexdigest()
        estimated_tokens = self._session._estimator.estimate_text(text)
        envelope = {
            "estimated_tokens": estimated_tokens,
            "kind": kind,
            "original_chars": len(text),
            "sha256": text_sha256,
            "text": text,
        }
        artifact = ArchivedArtifact(
            path=str(final_path),
            kind=kind,
            original_chars=len(text),
            estimated_tokens=estimated_tokens,
            sha256=text_sha256,
        )
        try:
            self._write_envelope(temp_path, envelope)
        except Exception:
            _unlink_if_exists(temp_path)
            raise

        self._pending.append(
            _PendingArtifact(
                temp_path=temp_path,
                final_path=final_path,
                artifact=artifact,
            )
        )
        return artifact

    def commit(self) -> None:
        self._ensure_open()
        committed: list[_PendingArtifact] = []
        try:
            for pending in self._pending:
                pending.final_path.parent.mkdir(parents=True, exist_ok=True)
                pending.temp_path.replace(pending.final_path)
                committed.append(pending)
        except Exception:
            for pending in committed:
                _unlink_if_exists(pending.final_path)
            self.rollback()
            raise

        # 先把文件原子提交到磁盘，再登记可读路径；后续历史替换必须发生在这个顺序之后。
        self._session._register_paths(tuple(pending.artifact for pending in self._pending))
        self._pending.clear()
        self._finished = True

    def rollback(self) -> None:
        if self._finished:
            return
        for pending in self._pending:
            _unlink_if_exists(pending.temp_path)
            _unlink_if_exists(pending.final_path)
        self._pending.clear()
        self._finished = True

    @staticmethod
    def _write_envelope(temp_path: Path, envelope: dict[str, object]) -> None:
        temp_path.parent.mkdir(parents=True, exist_ok=True)
        with temp_path.open("w", encoding="utf-8") as file:
            json.dump(envelope, file, ensure_ascii=False, sort_keys=True)
            file.flush()
            os.fsync(file.fileno())

    def _ensure_open(self) -> None:
        if self._finished:
            raise RuntimeError("archive transaction is already closed")


class ReadCompactArtifactTool:
    def __init__(self, session: ArchiveSession) -> None:
        self._session = session

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="read_compact_artifact",
            description="分段读取当前会话已登记的上下文归档正文。",
            parameters={
                "type": "object",
                "description": "读取上下文归档所需参数。",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "上下文管理器提供的已登记归档路径。",
                    },
                    "offset": {
                        "type": "integer",
                        "description": "从正文字符偏移开始读取。",
                        "minimum": 0,
                    },
                    "max_tokens": {
                        "type": "integer",
                        "description": "本次最多返回的估算 token 数，最大 2000。",
                        "minimum": 1,
                        "maximum": PREVIEW_ALLOWANCE_TOKENS,
                    },
                },
                "required": ["path"],
            },
            kind=ToolKind.READ,
            grant_arguments=(),
        )

    def execute(self, arguments: ToolArguments) -> ToolResult:
        try:
            path = _required_str(arguments, "path")
            offset = arguments.get("offset", 0)
            max_tokens = arguments.get("max_tokens", PREVIEW_ALLOWANCE_TOKENS)
            artifact_slice = self._session.read(
                path,
                offset=offset,
                max_tokens=max_tokens,
            )
            return ToolResult(
                ok=True,
                tool_name=self.definition.name,
                content={
                    "path": artifact_slice.path,
                    "text": artifact_slice.text,
                    "next_offset": artifact_slice.next_offset,
                    "eof": artifact_slice.eof,
                },
            )
        except Exception as exc:
            return ToolResult(
                ok=False,
                tool_name=self.definition.name,
                content={"path": arguments.get("path")},
                error=str(exc),
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
    return os.name == "nt"


def _validate_offset(offset: int) -> None:
    if type(offset) is not int or offset < 0:
        raise ValueError("offset must be a non-negative integer")


def _validate_max_tokens(max_tokens: int) -> None:
    if (
        type(max_tokens) is not int
        or max_tokens <= 0
        or max_tokens > PREVIEW_ALLOWANCE_TOKENS
    ):
        raise ValueError(f"max_tokens must be an integer from 1 to {PREVIEW_ALLOWANCE_TOKENS}")


def _resolve_registered_artifact(
    path: str | Path,
    allowed_artifacts: dict[Path, str],
) -> tuple[Path, str]:
    candidate = Path(path)
    if any(part == ".." for part in candidate.parts):
        raise ValueError("归档读取拒绝路径穿越语法")
    if candidate.is_symlink():
        raise ValueError("归档读取拒绝符号链接路径")
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        raise ValueError("归档路径未登记或不存在") from exc
    expected_sha256 = allowed_artifacts.get(resolved)
    if expected_sha256 is None:
        raise ValueError("归档路径未登记")
    # 这里不能复用工作区 PathGuard：归档位于用户缓存目录，只能按当前会话登记表精确授权。
    if resolved.is_symlink():
        raise ValueError("归档读取拒绝符号链接路径")
    return resolved, expected_sha256


def _read_verified_text(path: Path, *, registered_sha256: str) -> str:
    try:
        envelope = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("归档读取失败") from exc
    if not isinstance(envelope, dict):
        raise ValueError("归档格式无效")

    text = envelope.get("text")
    envelope_sha256 = envelope.get("sha256")
    original_chars = envelope.get("original_chars")
    if not isinstance(text, str) or not isinstance(envelope_sha256, str):
        raise ValueError("归档格式无效")
    if original_chars != len(text):
        raise ValueError("归档完整性校验失败")
    actual_sha256 = sha256(text.encode("utf-8")).hexdigest()
    if actual_sha256 != envelope_sha256 or actual_sha256 != registered_sha256:
        raise ValueError("归档完整性校验失败")
    return text


def _slice_end_for_budget(
    text: str,
    *,
    offset: int,
    max_tokens: int,
    estimator: TokenEstimator,
) -> int:
    remaining = text[offset:]
    if estimator.estimate_text(remaining) <= max_tokens:
        return len(text)

    low = offset + 1
    high = len(text)
    best = offset
    while low <= high:
        mid = (low + high) // 2
        if estimator.estimate_text(text[offset:mid]) <= max_tokens:
            best = mid
            low = mid + 1
        else:
            high = mid - 1
    return max(best, offset + 1)


def _required_str(arguments: ToolArguments, name: str) -> str:
    value = arguments.get(name)
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a string")
    return value


def _unlink_if_exists(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        return
