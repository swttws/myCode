from __future__ import annotations

import json
from pathlib import Path

import pytest

from mycode.llm import ChatMessage, MessageOrigin
from mycode.team import TeamError
from mycode.team.context import JsonConversationMemory


def _read_payload(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def test_context_json_conversation_memory_appends_replaces_and_clears(tmp_path: Path):
    path = tmp_path / "context.json"
    memory = JsonConversationMemory(path=path, max_bytes=4096)

    memory.append(ChatMessage(role="user", content="hello"))
    memory.append(
        ChatMessage(
            role="assistant",
            content="ready",
            tool_call_id="call-1",
            tool_name="tool",
            tool_arguments="{\"x\":1}",
            origin=MessageOrigin.SYSTEM_REMINDER,
        )
    )

    assert memory.messages() == [
        ChatMessage(role="user", content="hello"),
        ChatMessage(
            role="assistant",
            content="ready",
            tool_call_id="call-1",
            tool_name="tool",
            tool_arguments="{\"x\":1}",
            origin=MessageOrigin.SYSTEM_REMINDER,
        ),
    ]
    payload = _read_payload(path)
    assert payload["version"] == 1
    assert payload["schema_version"] == 1
    assert len(payload["messages"]) == 2
    assert payload["messages"][1]["origin"] == MessageOrigin.SYSTEM_REMINDER.value

    memory.replace([ChatMessage(role="user", content="replaced")])
    assert memory.messages() == [ChatMessage(role="user", content="replaced")]

    memory.clear()
    assert memory.messages() == []
    assert _read_payload(path)["messages"] == []


def test_context_json_conversation_memory_reload_picks_up_external_changes(tmp_path: Path):
    path = tmp_path / "context.json"
    memory = JsonConversationMemory(path=path, max_bytes=4096)
    memory.append(ChatMessage(role="user", content="initial"))

    path.write_text(
        json.dumps(
            {
                "version": 1,
                "schema_version": 1,
                "messages": [
                    {
                        "role": "assistant",
                        "content": "external",
                        "origin": "conversation",
                    }
                ],
            },
            ensure_ascii=False,
            sort_keys=True,
        ),
        encoding="utf-8",
    )

    memory.reload()

    assert memory.messages() == [ChatMessage(role="assistant", content="external")]


def test_context_json_conversation_memory_rejects_corrupt_files_without_overwriting(tmp_path: Path):
    path = tmp_path / "context.json"
    memory = JsonConversationMemory(path=path, max_bytes=4096)
    memory.append(ChatMessage(role="user", content="keep me"))
    original = path.read_text(encoding="utf-8")
    path.write_text("{not-json", encoding="utf-8")

    with pytest.raises(TeamError, match="corrupt"):
        memory.reload()

    assert memory.messages() == [ChatMessage(role="user", content="keep me")]
    assert path.read_text(encoding="utf-8") == "{not-json"
    assert original != path.read_text(encoding="utf-8")


def test_context_json_conversation_memory_enforces_size_limit(tmp_path: Path):
    path = tmp_path / "context.json"
    memory = JsonConversationMemory(path=path, max_bytes=220)
    memory.append(ChatMessage(role="user", content="small"))

    with pytest.raises(TeamError, match="exceeds"):
        memory.append(ChatMessage(role="assistant", content="x" * 300))

    assert memory.messages() == [ChatMessage(role="user", content="small")]
    assert len(path.read_text(encoding="utf-8").encode("utf-8")) <= 220
