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
                "输出格式要求：",
                "- 只输出一个 JSON 对象；不要使用 Markdown 代码块；不要添加前后说明文字。",
                "- 输出必须能直接传给 json.loads(text) 解析。",
                '- 无需保存记忆时，返回 {"decisions":[]}；如需说明原因，也可以返回 ignore 决策。',
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
                "- title 必须是具体、可执行的结论，直接写出偏好值或项目事实；不要使用 Method Naming Style 这类抽象分类标题。",
                "",
                "顶层 JSON 结构样例：",
                '{"decisions":[]}',
                "",
                "create 样例：",
                (
                    '{"decisions":[{"action":"create","scope":"user","kind":"user_preference",'
                    '"title":"Use pytest","body":"用户偏好使用 pytest 编写回归测试。","reason":"用户明确表达了稳定偏好。"}]}'
                ),
                "",
                "merge 样例：",
                (
                    '{"decisions":[{"action":"merge","scope":"project","kind":"project_knowledge",'
                    '"target_note_id":"api-contract-abcd1234","body":"补充：Responses SSE 可能包含 data: [DONE]。",'
                    '"reason":"新信息应合并到已有项目事实。"}]}'
                ),
                "",
                "update 样例：",
                (
                    '{"decisions":[{"action":"update","scope":"project","kind":"reference",'
                    '"target_note_id":"docs-11112222","body":"README 已更新 Stage 08 存储说明。",'
                    '"reason":"替换过期内容。"}]}'
                ),
                "",
                "ignore 样例：",
                '{"decisions":[{"action":"ignore","reason":"本轮对话没有可长期保存的用户偏好、纠正、项目事实或参考资料。"}]}',
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
        payload = _parse_json_payload(text)
        if payload is None:
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


def _parse_json_payload(text: str) -> dict[str, object] | None:
    for candidate in _json_candidates(text.strip()):
        try:
            value = json.loads(candidate)
        except json.JSONDecodeError:
            value = _decode_first_json_object(candidate)
        if isinstance(value, dict):
            return value
    return None


def _json_candidates(text: str):
    if text:
        yield text

    lines = text.splitlines()
    in_fence = False
    fenced_lines: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("```"):
            if in_fence:
                yield "\n".join(fenced_lines).strip()
                fenced_lines = []
                in_fence = False
            else:
                language = stripped[3:].strip().lower()
                in_fence = language in ("", "json")
            continue
        if in_fence:
            fenced_lines.append(line)


def _decode_first_json_object(text: str) -> dict[str, object] | None:
    decoder = json.JSONDecoder()
    for index, char in enumerate(text):
        if char != "{":
            continue
        try:
            value, _end = decoder.raw_decode(text[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    return None


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
