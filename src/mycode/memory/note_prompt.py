from __future__ import annotations

import json

from mycode.llm import ChatMessage
from mycode.memory.models import (
    MemoryIndexBundle,
    MemoryKind,
    MemoryScope,
    NoteUpdateAction,
    NoteUpdateDecision,
)


class NoteUpdatePrompt:
    def build(
        self,
        *,
        user_message: ChatMessage,
        assistant_message: ChatMessage,
        user_index: MemoryIndexBundle,
        project_index: MemoryIndexBundle,
    ) -> ChatMessage:
        content = "\n".join(
            [
                "Review the latest exchange and decide whether durable project memory should change.",
                "",
                "Return a JSON object with a top-level decisions array. Do not call tools.",
                "Each decision action must be one of: create, merge, update, ignore.",
                "",
                "Memory kinds:",
                f"- {MemoryKind.USER_PREFERENCE.value}: stable user preference; defaults to user scope.",
                f"- {MemoryKind.CORRECTION.value}: user correction or feedback; defaults to user scope.",
                f"- {MemoryKind.PROJECT_KNOWLEDGE.value}: durable project fact; defaults to project scope.",
                f"- {MemoryKind.REFERENCE.value}: reusable project reference; defaults to project scope.",
                "",
                "Required write fields:",
                "- create: action, scope, kind, title, body, reason",
                "- merge/update: action, scope, kind, target_note_id, body, reason",
                "- ignore: action, reason",
                "",
                "Existing user memory index:",
                user_index.rendered_text or "(empty)",
                "",
                "Existing project memory index:",
                project_index.rendered_text or "(empty)",
                "",
                "Latest user message:",
                user_message.content,
                "",
                "Latest assistant message:",
                assistant_message.content,
            ]
        )
        return ChatMessage(role="user", content=content)

    def parse(self, text: str) -> tuple[NoteUpdateDecision, ...]:
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            return ()
        if not isinstance(payload, dict):
            return ()
        raw_decisions = payload.get("decisions")
        if not isinstance(raw_decisions, list):
            return ()

        decisions: list[NoteUpdateDecision] = []
        for raw_decision in raw_decisions:
            decision = _parse_decision(raw_decision)
            if decision is not None:
                decisions.append(decision)
        return tuple(decisions)


def _parse_decision(raw_decision: object) -> NoteUpdateDecision | None:
    if not isinstance(raw_decision, dict):
        return None
    try:
        action = NoteUpdateAction(raw_decision.get("action"))
    except ValueError:
        return None

    if action is NoteUpdateAction.IGNORE:
        return NoteUpdateDecision(action=action, reason=_string_or_empty(raw_decision.get("reason")))

    scope = _parse_scope(raw_decision.get("scope"))
    kind = _parse_kind(raw_decision.get("kind"))
    target_note_id = _optional_nonempty_string(raw_decision.get("target_note_id"))
    title = _optional_nonempty_string(raw_decision.get("title"))
    body = _optional_nonempty_string(raw_decision.get("body"))
    reason = _string_or_empty(raw_decision.get("reason"))

    if action is NoteUpdateAction.CREATE:
        if scope is None or kind is None or title is None or body is None:
            return None
    elif action in (NoteUpdateAction.MERGE, NoteUpdateAction.UPDATE):
        if scope is None or kind is None or target_note_id is None or body is None:
            return None

    return NoteUpdateDecision(
        action=action,
        scope=scope,
        kind=kind,
        target_note_id=target_note_id,
        title=title,
        body=body,
        reason=reason,
    )


def _parse_scope(value: object) -> MemoryScope | None:
    if not isinstance(value, str):
        return None
    try:
        return MemoryScope(value)
    except ValueError:
        return None


def _parse_kind(value: object) -> MemoryKind | None:
    if not isinstance(value, str):
        return None
    try:
        return MemoryKind(value)
    except ValueError:
        return None


def _optional_nonempty_string(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def _string_or_empty(value: object) -> str:
    if not isinstance(value, str):
        return ""
    return value
