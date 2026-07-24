from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

import mycode.memory.sessions as sessions_module
from mycode.llm import ChatMessage, MessageOrigin
from mycode.memory.models import FrameworkContextKind
from mycode.memory.paths import MemoryPaths
from mycode.memory.sessions import SessionArchiveStore


def _dt(year: int, month: int, day: int, hour: int = 0, minute: int = 0, second: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, second, tzinfo=timezone.utc)


def _make_paths(tmp_path: Path) -> tuple[MemoryPaths, Path, Path]:
    workspace = tmp_path / "workspace"
    home = tmp_path / "home"
    workspace.mkdir()
    home.mkdir()
    return MemoryPaths(workspace_root=workspace, home=home), workspace, home


def _read_jsonl(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_session_archive_store_generates_timestamped_unique_session_ids(tmp_path, monkeypatch):
    paths, _workspace, _home = _make_paths(tmp_path)
    suffixes = iter(["abcd", "ef01"])
    monkeypatch.setattr(sessions_module.secrets, "token_hex", lambda size: next(suffixes))

    store = SessionArchiveStore(paths=paths, now=lambda: _dt(2026, 7, 23, 10, 0, 0))
    first_session_id = store.current_session_id
    store.start_new_session()
    second_session_id = store.current_session_id

    assert re.fullmatch(r"\d{8}-\d{6}-[0-9a-f]{4}", first_session_id)
    assert re.fullmatch(r"\d{8}-\d{6}-[0-9a-f]{4}", second_session_id)
    assert first_session_id != second_session_id
    assert first_session_id.endswith("-abcd")
    assert second_session_id.endswith("-ef01")
    assert (paths.sessions_dir / f"{first_session_id}.jsonl").exists()
    assert (paths.sessions_dir / f"{second_session_id}.jsonl").exists()


def test_session_archive_store_appends_messages_and_lists_metadata(tmp_path, monkeypatch):
    paths, _workspace, _home = _make_paths(tmp_path)
    suffixes = iter(["abcd"])
    monkeypatch.setattr(sessions_module.secrets, "token_hex", lambda size: next(suffixes))
    now_values = iter(
        [
            _dt(2026, 7, 23, 10, 0, 0),
            _dt(2026, 7, 23, 10, 0, 1),
            _dt(2026, 7, 23, 10, 0, 2),
            _dt(2026, 7, 23, 10, 0, 3),
        ]
    )
    store = SessionArchiveStore(paths=paths, now=lambda: next(now_values))

    store.append_messages(
        [
            ChatMessage(role="user", content="hello there\nsecond line"),
            ChatMessage(role="assistant", content="response one"),
        ]
    )
    store.append_message(
        ChatMessage(
            role="tool",
            content=json.dumps({"ok": True}, ensure_ascii=False),
            tool_call_id="call-1",
            tool_name="read_file",
        )
    )

    session_path = paths.sessions_dir / f"{store.current_session_id}.jsonl"
    records = _read_jsonl(session_path)
    summary = store.list_sessions()[0]

    assert len(records) == 3
    assert records[0]["role"] == "user"
    assert records[1]["role"] == "assistant"
    assert records[2]["tool_call_id"] == "call-1"
    assert all(path.suffix == ".jsonl" for path in paths.sessions_dir.iterdir())
    assert summary.session_id == store.current_session_id
    assert summary.path == str(session_path)
    assert summary.title == "hello there"
    assert summary.message_count == 3
    assert summary.updated_at == _dt(2026, 7, 23, 10, 0, 3).isoformat()
    assert summary.recoverable is True


def test_session_archive_store_restores_valid_history_skips_bad_lines_and_downgrades_unknown_origin(
    tmp_path, monkeypatch
):
    paths, _workspace, _home = _make_paths(tmp_path)
    suffixes = iter(["abcd"])
    monkeypatch.setattr(sessions_module.secrets, "token_hex", lambda size: next(suffixes))
    store = SessionArchiveStore(paths=paths, now=lambda: _dt(2026, 7, 23, 10, 0, 0))
    session_path = paths.sessions_dir / f"{store.current_session_id}.jsonl"
    session_path.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "type": "message",
                        "timestamp": _dt(2026, 7, 23, 10, 0, 1).isoformat(),
                        "role": "user",
                        "content": "hello",
                        "origin": "conversation",
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                "not json",
                json.dumps(
                    {
                        "type": "message",
                        "timestamp": _dt(2026, 7, 23, 10, 0, 2).isoformat(),
                        "role": "assistant",
                        "content": "reply",
                        "origin": "mystery",
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                ),
            ]
        ),
        encoding="utf-8",
    )

    result = store.restore_latest()

    assert result.summary is not None
    assert result.summary.session_id == store.current_session_id
    assert result.history == (
        ChatMessage(role="user", content="hello"),
        ChatMessage(role="assistant", content="reply", origin=MessageOrigin.CONVERSATION),
    )
    assert result.skipped_lines == 1
    assert result.truncated_at_boundary is False
    assert result.diagnostics
    assert result.time_gap_seconds is None
    assert result.time_gap_block is None


def test_session_archive_store_truncates_incomplete_tool_boundary(tmp_path, monkeypatch):
    paths, _workspace, _home = _make_paths(tmp_path)
    suffixes = iter(["abcd"])
    monkeypatch.setattr(sessions_module.secrets, "token_hex", lambda size: next(suffixes))
    store = SessionArchiveStore(paths=paths, now=lambda: _dt(2026, 7, 23, 10, 0, 0))
    session_path = paths.sessions_dir / f"{store.current_session_id}.jsonl"
    session_path.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "type": "message",
                        "timestamp": _dt(2026, 7, 23, 10, 0, 1).isoformat(),
                        "role": "user",
                        "content": "hello",
                        "origin": "conversation",
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                json.dumps(
                    {
                        "type": "message",
                        "timestamp": _dt(2026, 7, 23, 10, 0, 2).isoformat(),
                        "role": "assistant",
                        "content": "",
                        "tool_call_id": "call-1",
                        "tool_name": "read_file",
                        "tool_arguments": "{\"path\":\"README.md\"}",
                        "origin": "conversation",
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                json.dumps(
                    {
                        "type": "message",
                        "timestamp": _dt(2026, 7, 23, 10, 0, 3).isoformat(),
                        "role": "user",
                        "content": "after boundary",
                        "origin": "conversation",
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                ),
            ]
        ),
        encoding="utf-8",
    )

    result = store.restore_latest()

    assert result.summary is not None
    assert result.history == (ChatMessage(role="user", content="hello"),)
    assert result.skipped_lines == 2
    assert result.truncated_at_boundary is True
    assert result.diagnostics
    assert result.time_gap_block is None


def test_session_archive_store_selects_latest_recoverable_session_and_reports_time_gap(
    tmp_path, monkeypatch
):
    paths, _workspace, _home = _make_paths(tmp_path)
    suffixes = iter(["aaaa", "bbbb"])
    monkeypatch.setattr(sessions_module.secrets, "token_hex", lambda size: next(suffixes))
    now_values = iter(
        [
            _dt(2026, 7, 1, 9, 0, 0),
            _dt(2026, 7, 1, 9, 0, 1),
            _dt(2026, 7, 2, 9, 0, 0),
            _dt(2026, 7, 2, 9, 0, 1),
            _dt(2026, 7, 5, 9, 0, 0),
        ]
    )
    store = SessionArchiveStore(paths=paths, now=lambda: next(now_values))
    store.append_message(ChatMessage(role="user", content="first session"))
    store.start_new_session()
    store.append_message(ChatMessage(role="user", content="second session"))

    latest = store.latest_recoverable_session()
    result = store.restore_latest()

    assert latest is not None
    assert latest.session_id == store.current_session_id
    assert result.summary is not None
    assert result.summary.session_id == store.current_session_id
    assert result.time_gap_seconds is not None
    assert result.time_gap_seconds > 86400
    assert result.time_gap_block is not None
    assert result.time_gap_block.kind is FrameworkContextKind.RESTORE_NOTICE
    assert result.time_gap_block.content == "上一个项目会话在无活动 259199 秒后已恢复。"


def test_session_archive_store_cleans_up_expired_sessions_without_deleting_current(
    tmp_path, monkeypatch
):
    paths, _workspace, _home = _make_paths(tmp_path)
    suffixes = iter(["1111", "2222"])
    monkeypatch.setattr(sessions_module.secrets, "token_hex", lambda size: next(suffixes))
    now_values = iter(
        [
            _dt(2026, 6, 1, 9, 0, 0),
            _dt(2026, 6, 1, 9, 0, 1),
            _dt(2026, 6, 2, 9, 0, 0),
            _dt(2026, 6, 2, 9, 0, 1),
            _dt(2026, 7, 23, 9, 0, 0),
        ]
    )
    store = SessionArchiveStore(paths=paths, now=lambda: next(now_values))
    store.append_message(ChatMessage(role="user", content="old session"))
    old_session_path = paths.sessions_dir / f"{store.current_session_id}.jsonl"
    store.start_new_session()
    store.append_message(ChatMessage(role="user", content="current session"))
    current_session_path = paths.sessions_dir / f"{store.current_session_id}.jsonl"

    diagnostics = store.cleanup_expired()

    assert diagnostics == ()
    assert not old_session_path.exists()
    assert current_session_path.exists()
    assert store.current_session_id == current_session_path.stem
