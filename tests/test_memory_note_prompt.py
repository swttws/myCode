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
    assert "JSON object" in message.content
    assert "Do not call tools" in message.content


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
