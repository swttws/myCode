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
                "请审查最新一轮用户和助手对话，判断是否需要更新可长期保存的项目记忆。",
                "",
                "返回一个 JSON 对象，顶层字段必须是 decisions 数组。不要调用工具。",
                "每个 decision 的 action 必须是以下之一：create、merge、update、ignore。",
                "",
                "记忆类型：",
                f"- {MemoryKind.USER_PREFERENCE.value}: 稳定的用户偏好；默认写入 user 作用域。",
                f"- {MemoryKind.CORRECTION.value}: 用户纠正或反馈；默认写入 user 作用域。",
                f"- {MemoryKind.PROJECT_KNOWLEDGE.value}: 可长期保存的项目事实；默认写入 project 作用域。",
                f"- {MemoryKind.REFERENCE.value}: 可复用的项目参考资料；默认写入 project 作用域。",
                "",
                "必填写入字段：",
                "- create: action、scope、kind、title、body、reason",
                "- merge/update: action、scope、kind、target_note_id、body、reason",
                "- ignore: action、reason",
                "",
                "现有用户记忆索引：",
                user_index.rendered_text or "（空）",
                "",
                "现有项目记忆索引：",
                project_index.rendered_text or "（空）",
                "",
                "最新用户消息：",
                user_message.content,
                "",
                "最新助手消息：",
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
