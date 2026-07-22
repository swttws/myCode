from __future__ import annotations

from collections.abc import Sequence

from mycode.compact.estimator import TokenEstimator
from mycode.compact.models import CompactPolicy
from mycode.llm import ChatMessage


def select_recent_messages(
    history: Sequence[ChatMessage],
    *,
    keep_recent_tokens: int = CompactPolicy().keep_recent_tokens,
    min_recent_messages: int = CompactPolicy().min_recent_messages,
    estimator: TokenEstimator | None = None,
) -> tuple[ChatMessage, ...]:
    messages = tuple(history)
    if len(messages) <= min_recent_messages:
        return messages

    token_estimator = estimator or TokenEstimator()
    start = len(messages)
    selected_tokens = 0
    selected_count = 0
    for index in range(len(messages) - 1, -1, -1):
        message_tokens = token_estimator.estimate_text(messages[index].content)
        if (
            selected_count >= min_recent_messages
            and selected_tokens + message_tokens > keep_recent_tokens
        ):
            break
        start = index
        selected_tokens += message_tokens
        selected_count += 1

    start = _expand_tool_group_start(messages, start)
    return messages[start:]


def _expand_tool_group_start(messages: tuple[ChatMessage, ...], start: int) -> int:
    expanded = start
    for call_start, group_end in _tool_groups(messages):
        if call_start < expanded < group_end:
            expanded = call_start
    return expanded


def _tool_groups(messages: tuple[ChatMessage, ...]) -> tuple[tuple[int, int], ...]:
    groups: list[tuple[int, int]] = []
    index = 0
    while index < len(messages):
        if messages[index].role != "tool":
            index += 1
            continue

        result_start = index
        while index < len(messages) and messages[index].role == "tool":
            index += 1
        result_end = index

        call_start = result_start
        while call_start > 0 and _is_assistant_tool_call(messages[call_start - 1]):
            call_start -= 1
        groups.append((call_start, result_end))

    return tuple(groups)


def _is_assistant_tool_call(message: ChatMessage) -> bool:
    return message.role == "assistant" and bool(message.tool_call_id) and bool(message.tool_name)
