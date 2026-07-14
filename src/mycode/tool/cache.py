from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from threading import RLock


@dataclass(frozen=True)
class CachedText:
    text: str
    mtime_ns: int
    size: int


class FileTextCache:
    def __init__(self) -> None:
        # 读、写、改共享同一把锁，避免同一进程内的缓存和磁盘状态打架。
        self._lock = RLock()
        self._cache: dict[Path, CachedText] = {}

    def read_text(self, path: Path) -> str:
        resolved = path.resolve()
        with self._lock:
            stat = resolved.stat()
            cached = self._cache.get(resolved)
            if cached is not None and cached.mtime_ns == stat.st_mtime_ns and cached.size == stat.st_size:
                return cached.text

            text = resolved.read_text(encoding="utf-8")
            self._cache[resolved] = CachedText(
                text=text,
                mtime_ns=stat.st_mtime_ns,
                size=stat.st_size,
            )
            return text

    def write_text(self, path: Path, text: str) -> None:
        resolved = path.resolve()
        with self._lock:
            resolved.parent.mkdir(parents=True, exist_ok=True)
            resolved.write_text(text, encoding="utf-8")
            self._cache[resolved] = _cached_text_for(resolved, text)

    def edit_text(self, path: Path, old_text: str, new_text: str) -> tuple[int, str | None]:
        resolved = path.resolve()
        with self._lock:
            # 先统计原文命中数，再决定是否写回，保证只做唯一匹配替换。
            text = self.read_text(resolved)
            match_count = text.count(old_text)
            if match_count != 1:
                return match_count, None

            updated_text = text.replace(old_text, new_text, 1)
            resolved.write_text(updated_text, encoding="utf-8")
            self._cache[resolved] = _cached_text_for(resolved, updated_text)
            return match_count, updated_text

    def invalidate(self, path: Path) -> None:
        with self._lock:
            self._cache.pop(path.resolve(), None)


def _cached_text_for(path: Path, text: str) -> CachedText:
    stat = path.stat()
    return CachedText(text=text, mtime_ns=stat.st_mtime_ns, size=stat.st_size)
