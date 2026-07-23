from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import replace

from mycode.compact.archive import ArchiveTransaction
from mycode.compact.estimator import TokenEstimator
from mycode.compact.models import (
    ArchivedArtifact,
    CompactAction,
    CompactPolicy,
    HeavyCompactResult,
)
from mycode.llm import ChatMessage, MessageOrigin


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


def summary_input_messages(
    history: Sequence[ChatMessage],
    recent_messages: Sequence[ChatMessage],
) -> tuple[ChatMessage, ...]:
    old_messages = _old_messages(tuple(history), tuple(recent_messages))
    return tuple(message for message in old_messages if message.role != "user")


def build_compacted_history(
    history: Sequence[ChatMessage],
    *,
    recent_messages: Sequence[ChatMessage],
    summary: str,
    transaction: ArchiveTransaction,
    preserve_user_tokens: int | None = None,
    estimator: TokenEstimator | None = None,
) -> HeavyCompactResult:
    token_estimator = estimator or TokenEstimator()
    old_messages = _old_messages(tuple(history), tuple(recent_messages))
    user_messages, artifacts = _preserve_or_archive_old_users(
        old_messages,
        transaction=transaction,
        preserve_user_tokens=preserve_user_tokens,
        estimator=token_estimator,
    )
    summary_message = ChatMessage(
        role="assistant",
        content=summary,
        origin=MessageOrigin.COMPACT_SUMMARY,
    )
    boundary_message = ChatMessage(
        role="user",
        content=(
            "以上摘要不包含完整文件、工具输出或超长用户原文细节；"
            "需要具体内容时必须重新读取归档路径，不得依据摘要猜测。"
        ),
        origin=MessageOrigin.COMPACT_BOUNDARY,
    )
    return HeavyCompactResult(
        history=(
            *user_messages,
            summary_message,
            boundary_message,
            *tuple(recent_messages),
        ),
        artifacts=artifacts,
        actions=(CompactAction.HEAVY,),
        summary=summary,
    )


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


def _old_messages(
    history: tuple[ChatMessage, ...],
    recent_messages: tuple[ChatMessage, ...],
) -> tuple[ChatMessage, ...]:
    if not recent_messages:
        return history
    if len(recent_messages) > len(history) or history[-len(recent_messages) :] != recent_messages:
        raise ValueError("recent_messages must be a suffix of history")
    return history[: -len(recent_messages)]


def _preserve_or_archive_old_users(
    messages: tuple[ChatMessage, ...],
    *,
    transaction: ArchiveTransaction,
    preserve_user_tokens: int | None,
    estimator: TokenEstimator,
) -> tuple[tuple[ChatMessage, ...], tuple[ArchivedArtifact, ...]]:
    preserved: list[ChatMessage] = []
    artifacts: list[ArchivedArtifact] = []
    used_tokens = 0
    for message in messages:
        if message.role != "user":
            continue
        message_tokens = estimator.estimate_text(message.content)
        if preserve_user_tokens is not None and used_tokens + message_tokens > preserve_user_tokens:
            artifact = transaction.archive_text(kind="user_message", text=message.content)
            artifacts.append(artifact)
            preserved.append(
                replace(
                    message,
                    content=_user_preview_content(message, artifact),
                    origin=MessageOrigin.COMPACT_PREVIEW,
                )
            )
            continue
        preserved.append(message)
        used_tokens += message_tokens
    return tuple(preserved), tuple(artifacts)


def _user_preview_content(message: ChatMessage, artifact: ArchivedArtifact) -> str:
    text = message.content
    head = text[:120]
    tail = text[-120:] if len(text) > 120 else ""
    return json.dumps(
        {
            "estimated_tokens": artifact.estimated_tokens,
            "head": head,
            "kind": artifact.kind,
            "original_chars": artifact.original_chars,
            "path": artifact.path,
            "sha256": artifact.sha256,
            "tail": tail,
            "truncated": True,
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
