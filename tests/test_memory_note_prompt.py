from __future__ import annotations

import json

from mycode.llm import ChatMessage
from mycode.memory.models import (
    MemoryIndexBundle,
    MemoryKind,
    MemoryScope,
    NoteUpdateAction,
)
from mycode.memory.note_prompt import NoteUpdatePrompt


def _bundle(scope: MemoryScope, text: str) -> MemoryIndexBundle:
    entries = tuple(text.splitlines())
    return MemoryIndexBundle(
        scope=scope,
        entries=entries,
        rendered_text=text,
        line_count=len(entries),
        byte_count=len(text.encode("utf-8")),
        truncated=False,
    )


def test_note_update_prompt_builds_context_rich_json_instruction():
    prompt = NoteUpdatePrompt()
    message = prompt.build(
        user_message=ChatMessage(role="user", content="Please remember I prefer pytest."),
        assistant_message=ChatMessage(role="assistant", content="I'll use pytest for this project."),
        user_index=_bundle(MemoryScope.USER, "- Existing user preference"),
        project_index=_bundle(MemoryScope.PROJECT, "- Existing project API note"),
    )

    assert message.role == "user"
    assert "Please remember I prefer pytest." in message.content
    assert "I'll use pytest for this project." in message.content
    assert "- Existing user preference" in message.content
    assert "- Existing project API note" in message.content
    assert MemoryKind.USER_PREFERENCE.value in message.content
    assert MemoryKind.CORRECTION.value in message.content
    assert MemoryKind.PROJECT_KNOWLEDGE.value in message.content
    assert MemoryKind.REFERENCE.value in message.content
    assert "decisions" in message.content
    assert "返回一个 JSON 对象" in message.content
    assert "不要调用工具" in message.content
    assert "只输出一个 JSON 对象" in message.content
    assert "不要使用 Markdown 代码块" in message.content
    assert "不要添加前后说明文字" in message.content
    assert "json.loads" in message.content
    assert '{"decisions":[]}' in message.content
    assert '"action":"create"' in message.content
    assert '"action":"merge"' in message.content
    assert '"action":"update"' in message.content
    assert '"action":"ignore"' in message.content
    assert "现有用户记忆索引" in message.content
    assert "最新助手消息" in message.content


def test_note_update_prompt_parses_valid_decisions():
    prompt = NoteUpdatePrompt()
    payload = {
        "decisions": [
            {
                "action": "create",
                "scope": "user",
                "kind": "user_preference",
                "title": "Use pytest",
                "body": "The user prefers pytest for regression coverage.",
                "reason": "Explicit preference.",
            },
            {
                "action": "merge",
                "scope": "project",
                "kind": "project_knowledge",
                "target_note_id": "api-contract-abcd1234",
                "body": "The CLI wires shared memory into AgentLoop.",
                "reason": "Related project detail.",
            },
            {
                "action": "update",
                "scope": "project",
                "kind": "reference",
                "target_note_id": "docs-11112222",
                "title": "Docs",
                "body": "README documents Stage 08 storage.",
                "reason": "Replace stale wording.",
            },
            {"action": "ignore", "reason": "Ephemeral status update."},
        ]
    }

    decisions = prompt.parse(json.dumps(payload))

    assert [decision.action for decision in decisions] == [
        NoteUpdateAction.CREATE,
        NoteUpdateAction.MERGE,
        NoteUpdateAction.UPDATE,
        NoteUpdateAction.IGNORE,
    ]
    assert decisions[0].scope is MemoryScope.USER
    assert decisions[0].kind is MemoryKind.USER_PREFERENCE
    assert decisions[1].target_note_id == "api-contract-abcd1234"
    assert decisions[2].title == "Docs"
    assert decisions[3].reason == "Ephemeral status update."


def test_note_update_prompt_parses_json_from_markdown_response():
    prompt = NoteUpdatePrompt()
    text = """
The JSON you've provided is valid.

```json
{"decisions":[{"action":"create","scope":"project","kind":"project_knowledge","title":"API","body":"网关返回 Responses SSE。","reason":"用户明确要求记住。"}]}
```

### Explanation
This should not prevent parsing.
"""

    decisions = prompt.parse(text)

    assert len(decisions) == 1
    assert decisions[0].action is NoteUpdateAction.CREATE
    assert decisions[0].scope is MemoryScope.PROJECT
    assert decisions[0].kind is MemoryKind.PROJECT_KNOWLEDGE
    assert decisions[0].title == "API"


def test_note_update_prompt_ignores_invalid_payloads_and_keeps_valid_ignore():
    prompt = NoteUpdatePrompt()

    assert prompt.parse("not json") == ()
    assert prompt.parse(json.dumps({"decisions": {}})) == ()

    decisions = prompt.parse(
        json.dumps(
            {
                "decisions": [
                    {
                        "action": "create",
                        "scope": "user",
                        "kind": "user_preference",
                        "title": "Missing body",
                    },
                    {
                        "action": "create",
                        "scope": "invalid",
                        "kind": "user_preference",
                        "title": "Bad scope",
                        "body": "No write.",
                    },
                    {"action": "ignore", "reason": "Still valid."},
                    {"action": "unknown", "reason": "Bad action."},
                ]
            }
        )
    )

    assert len(decisions) == 1
    assert decisions[0].action is NoteUpdateAction.IGNORE
    assert decisions[0].reason == "Still valid."
