from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Sequence
from dataclasses import dataclass, replace

from mycode.compact.archive import ArchiveTransaction
from mycode.compact.estimator import TokenEstimator
from mycode.compact.models import (
    ArchivedArtifact,
    CompactAction,
    CompactConfig,
    CompactError,
    CompactFailureCode,
    CompactPolicy,
    CompactReport,
    CompactStatus,
    HeavyCompactResult,
)
from mycode.compact.summary_prompt import build_summary_prompt, parse_summary_output
from mycode.llm import BaseLLM, ChatMessage, LLMError, MessageOrigin, StreamEventType


@dataclass(frozen=True)
class ForcedSummaryResult:
    message: ChatMessage
    artifact: ArchivedArtifact
    temporary_summaries: tuple[str, ...]


class ConversationCompactor:
    def __init__(
        self,
        llm: BaseLLM,
        config: CompactConfig,
        *,
        policy: CompactPolicy | None = None,
        estimator: TokenEstimator | None = None,
        model_timeout_seconds: float | None = None,
    ) -> None:
        self._llm = llm
        self._config = config
        self._policy = policy or CompactPolicy()
        self._estimator = estimator or TokenEstimator()
        self._model_timeout_seconds = model_timeout_seconds

    async def compact(
        self,
        history: Sequence[ChatMessage],
        *,
        mode: str,
        build_request,
        transaction: ArchiveTransaction,
        run_deadline: float | None,
    ) -> HeavyCompactResult:
        recent_messages = select_recent_messages(
            history,
            keep_recent_tokens=self._policy.keep_recent_tokens,
            min_recent_messages=self._policy.min_recent_messages,
            estimator=self._estimator,
        )
        summary_messages = list(summary_input_messages(history, recent_messages))
        summary_messages, forced_artifacts, forced = await self._shrink_summary_messages(
            summary_messages,
            transaction=transaction,
            run_deadline=run_deadline,
        )
        summary = await collect_summary(
            self._llm,
            summary_messages,
            estimator=self._estimator,
            policy=self._policy,
            model_timeout_seconds=self._model_timeout_seconds,
            run_deadline=run_deadline,
        )
        compacted = build_compacted_history(
            history,
            recent_messages=recent_messages,
            summary=summary,
            transaction=transaction,
            estimator=self._estimator,
        )
        actions = (CompactAction.HEAVY, CompactAction.FORCE) if forced else (CompactAction.HEAVY,)
        return HeavyCompactResult(
            history=compacted.history,
            artifacts=(*forced_artifacts, *compacted.artifacts),
            actions=actions,
            summary=summary,
        )

    def emergency(
        self,
        history: Sequence[ChatMessage],
        *,
        build_request,
        transaction: ArchiveTransaction,
    ) -> HeavyCompactResult:
        try:
            return self._emergency_or_raise(
                history,
                build_request=build_request,
                transaction=transaction,
            )
        except OSError as exc:
            _rollback_quietly(transaction)
            raise _summary_error(
                CompactFailureCode.ARCHIVE_ERROR,
                "应急压缩归档写入失败。",
            ) from exc

    def _emergency_or_raise(
        self,
        history: Sequence[ChatMessage],
        *,
        build_request,
        transaction: ArchiveTransaction,
    ) -> HeavyCompactResult:
        original_history = tuple(history)
        recent_messages = select_recent_messages(
            original_history,
            keep_recent_tokens=self._policy.keep_recent_tokens,
            min_recent_messages=self._policy.min_recent_messages,
            estimator=self._estimator,
        )
        old_messages = _old_messages(original_history, tuple(recent_messages))
        compacted: list[ChatMessage] = []
        artifacts: list[ArchivedArtifact] = []

        if old_messages:
            archive_text = _archive_history_json(old_messages)
            artifact = transaction.archive_text(kind="history", text=archive_text)
            artifacts.append(artifact)
            compacted.append(_emergency_index_message(old_messages, artifact))

        compacted.append(_boundary_message())
        recent_start = len(compacted)
        compacted.extend(recent_messages)
        recent_artifacts = self._emergency_reduce_recent_until_safe(
            compacted,
            recent_start=recent_start,
            build_request=build_request,
            transaction=transaction,
        )
        artifacts.extend(recent_artifacts)
        return HeavyCompactResult(
            history=tuple(compacted),
            artifacts=tuple(artifacts),
            actions=(CompactAction.EMERGENCY,),
            summary="",
        )

    def _emergency_reduce_recent_until_safe(
        self,
        compacted: list[ChatMessage],
        *,
        recent_start: int,
        build_request,
        transaction: ArchiveTransaction,
    ) -> tuple[ArchivedArtifact, ...]:
        artifacts: list[ArchivedArtifact] = []
        safety_line = self._config.context_window_tokens - self._policy.auto_reserve_tokens
        while _built_request_tokens(self._estimator, build_request, compacted) >= safety_line:
            replacement_index = _first_unarchived_recent(compacted, recent_start)
            if replacement_index is None:
                raise _summary_error(
                    CompactFailureCode.BUDGET_NOT_RECOVERED,
                    "应急压缩后仍超过自动安全线。",
                )
            artifact, replacement = _archive_message_preview(
                compacted[replacement_index],
                transaction=transaction,
                estimator=self._estimator,
                max_preview_tokens=80,
            )
            compacted[replacement_index] = replacement
            artifacts.append(artifact)
        return tuple(artifacts)

    async def _shrink_summary_messages(
        self,
        messages: list[ChatMessage],
        *,
        transaction: ArchiveTransaction,
        run_deadline: float | None,
    ) -> tuple[list[ChatMessage], tuple[ArchivedArtifact, ...], bool]:
        forced_artifacts: list[ArchivedArtifact] = []
        forced = False
        budget = _summary_budget(self._config, self._policy)

        while _summary_request_tokens(messages) > budget:
            before_tokens = _summary_request_tokens(messages)
            oversized_index = _first_oversized_summary_message(messages, budget)
            if oversized_index is not None:
                forced_message = await summarize_oversized_message(
                    self._llm,
                    messages[oversized_index],
                    config=self._config,
                    transaction=transaction,
                    estimator=self._estimator,
                    policy=self._policy,
                    model_timeout_seconds=self._model_timeout_seconds,
                    run_deadline=run_deadline,
                )
                messages[oversized_index] = forced_message.message
                forced_artifacts.append(forced_message.artifact)
            else:
                start, end = _earliest_containable_block(messages, budget)
                temporary_summary = await collect_summary(
                    self._llm,
                    messages[start:end],
                    estimator=self._estimator,
                    policy=self._policy,
                    model_timeout_seconds=self._model_timeout_seconds,
                    run_deadline=run_deadline,
                )
                messages[start:end] = [
                    ChatMessage(
                        role="assistant",
                        content=_temporary_summary_content(
                            temporary_summary,
                            message_count=end - start,
                        ),
                        origin=MessageOrigin.COMPACT_SUMMARY,
                    )
                ]

            forced = True
            after_tokens = _summary_request_tokens(messages)
            if after_tokens >= before_tokens:
                # 每轮递归必须严格降低工作副本预算，防止临时摘要反而放大导致死循环。
                raise _summary_error(
                    CompactFailureCode.BUDGET_NOT_RECOVERED,
                    "递归摘要未降低预算。",
                )

        return messages, tuple(forced_artifacts), forced


async def collect_summary(
    llm: BaseLLM,
    messages: Sequence[ChatMessage],
    *,
    estimator: TokenEstimator | None = None,
    policy: CompactPolicy = CompactPolicy(),
    model_timeout_seconds: float | None = None,
    run_deadline: float | None = None,
) -> str:
    token_estimator = estimator or TokenEstimator()
    request = [ChatMessage(role="user", content=build_summary_prompt(messages))]
    snapshot = token_estimator.snapshot(request, [])
    text_parts: list[str] = []
    done = False
    stream = None

    try:
        stream = llm.stream_chat(request, tools=[])
        while True:
            wait_timeout = _summary_wait_timeout(model_timeout_seconds, run_deadline)
            if wait_timeout is not None and wait_timeout <= 0:
                raise _summary_error(CompactFailureCode.TIMEOUT, "摘要调用超时。")
            try:
                event = (
                    await asyncio.wait_for(anext(stream), timeout=wait_timeout)
                    if wait_timeout is not None
                    else await anext(stream)
                )
            except StopAsyncIteration:
                break
            except asyncio.TimeoutError as exc:
                raise _summary_error(CompactFailureCode.TIMEOUT, "摘要调用超时。") from exc

            if event.type == StreamEventType.TEXT_DELTA:
                text_parts.append(event.content)
            elif event.type == StreamEventType.THINKING_DELTA:
                continue
            elif event.type == StreamEventType.TOOL_CALL:
                raise _summary_error(CompactFailureCode.TOOL_ATTEMPT, "摘要模型尝试调用工具。")
            elif event.type == StreamEventType.ERROR:
                raise _summary_error(CompactFailureCode.LLM_ERROR, "摘要模型返回错误。")
            elif event.type == StreamEventType.DONE:
                done = True
                if event.usage is not None:
                    token_estimator.record_usage(snapshot, event.usage)
                break

        if not done:
            raise _summary_error(CompactFailureCode.INVALID_FORMAT, "摘要流缺少完成事件。")

        try:
            summary = parse_summary_output("".join(text_parts))
        except ValueError as exc:
            raise _summary_error(CompactFailureCode.INVALID_FORMAT, "摘要格式不符合要求。") from exc

        if token_estimator.estimate_text(summary) > policy.manual_reserve_tokens:
            raise _summary_error(CompactFailureCode.SUMMARY_TOO_LARGE, "正式摘要超过 3K 预留上限。")
        return summary
    except CompactError:
        raise
    except asyncio.CancelledError as exc:
        raise _summary_error(CompactFailureCode.CANCELLED, "摘要调用已取消。") from exc
    except LLMError as exc:
        raise _summary_error(CompactFailureCode.LLM_ERROR, "摘要模型调用失败。") from exc
    finally:
        if stream is not None:
            close = getattr(stream, "aclose", None)
            if close is not None:
                await close()


async def summarize_oversized_message(
    llm: BaseLLM,
    message: ChatMessage,
    *,
    config: CompactConfig,
    transaction: ArchiveTransaction,
    estimator: TokenEstimator | None = None,
    policy: CompactPolicy = CompactPolicy(),
    model_timeout_seconds: float | None = None,
    run_deadline: float | None = None,
) -> ForcedSummaryResult:
    token_estimator = estimator or TokenEstimator()
    artifact = transaction.archive_text(
        kind=_artifact_kind_for_message(message),
        text=message.content,
    )
    chunks = _split_message_for_summary_budget(
        message,
        config=config,
        policy=policy,
    )
    temporary_summaries = []
    for chunk in chunks:
        temporary_summaries.append(
            await collect_summary(
                llm,
                [replace(message, content=chunk)],
                estimator=token_estimator,
                policy=policy,
                model_timeout_seconds=model_timeout_seconds,
                run_deadline=run_deadline,
            )
        )

    replacement_content = _forced_summary_preview(
        message,
        artifact,
        temporary_summaries=tuple(temporary_summaries),
    )
    if token_estimator.estimate_text(replacement_content) >= token_estimator.estimate_text(message.content):
        raise _summary_error(
            CompactFailureCode.BUDGET_NOT_RECOVERED,
            "单条消息压缩后未降低预算。",
        )

    return ForcedSummaryResult(
        message=replace(
            message,
            content=replacement_content,
            origin=MessageOrigin.COMPACT_PREVIEW,
        ),
        artifact=artifact,
        temporary_summaries=tuple(temporary_summaries),
    )


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
        content=_boundary_message().content,
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


def _artifact_kind_for_message(message: ChatMessage):
    if message.role == "user":
        return "user_message"
    if message.role == "tool":
        return "tool_result"
    return "history"


def _archive_history_json(messages: Sequence[ChatMessage]) -> str:
    return json.dumps(
        [_archive_message_payload(message) for message in messages],
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _archive_message_payload(message: ChatMessage) -> dict[str, object]:
    return {
        "content": message.content,
        "role": message.role,
        "tool_arguments": message.tool_arguments,
        "tool_call_id": message.tool_call_id,
        "tool_name": message.tool_name,
    }


def _emergency_index_message(
    messages: Sequence[ChatMessage],
    artifact: ArchivedArtifact,
) -> ChatMessage:
    return ChatMessage(
        role="assistant",
        content=json.dumps(
            {
                "estimated_tokens": artifact.estimated_tokens,
                "kind": "emergency_history_index",
                "message_count": len(messages),
                "path": artifact.path,
                "preview": [
                    {
                        "content_head": message.content[:80],
                        "role": message.role,
                        "tool_call_id": message.tool_call_id,
                    }
                    for message in messages
                ],
                "sha256": artifact.sha256,
                "truncated": True,
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ),
        origin=MessageOrigin.COMPACT_SUMMARY,
    )


def _boundary_message() -> ChatMessage:
    return ChatMessage(
        role="user",
        content=(
            "以上摘要不包含完整文件、工具输出或超长用户原文细节；"
            "需要具体内容时必须重新读取归档路径，不得依据摘要猜测。"
        ),
        origin=MessageOrigin.COMPACT_BOUNDARY,
    )


def _built_request_tokens(
    estimator: TokenEstimator,
    build_request,
    history: Sequence[ChatMessage],
) -> int:
    request = build_request(tuple(history))
    return estimator.estimate(estimator.snapshot(request.messages, request.tools)).tokens


def _first_unarchived_recent(
    messages: Sequence[ChatMessage],
    recent_start: int,
) -> int | None:
    for index in range(recent_start, len(messages)):
        if messages[index].origin is not MessageOrigin.COMPACT_PREVIEW:
            return index
    return None


def _archive_message_preview(
    message: ChatMessage,
    *,
    transaction: ArchiveTransaction,
    estimator: TokenEstimator,
    max_preview_tokens: int,
) -> tuple[ArchivedArtifact, ChatMessage]:
    artifact = transaction.archive_text(
        kind=_artifact_kind_for_message(message),
        text=message.content,
    )
    return (
        artifact,
        replace(
            message,
            content=_message_preview_content(
                message,
                artifact,
                estimator=estimator,
                max_preview_tokens=max_preview_tokens,
            ),
            origin=MessageOrigin.COMPACT_PREVIEW,
        ),
    )


def _message_preview_content(
    message: ChatMessage,
    artifact: ArchivedArtifact,
    *,
    estimator: TokenEstimator,
    max_preview_tokens: int,
) -> str:
    text = message.content
    best_preview = _message_preview_json(message, artifact, head="", tail="")
    low = 0
    high = min(len(text) // 2, max_preview_tokens)
    while low <= high:
        side_chars = (low + high) // 2
        candidate = _message_preview_json(
            message,
            artifact,
            head=text[:side_chars],
            tail=text[-side_chars:] if side_chars else "",
        )
        if estimator.estimate_text(candidate) <= max_preview_tokens:
            best_preview = candidate
            low = side_chars + 1
        else:
            high = side_chars - 1
    return best_preview


def _message_preview_json(
    message: ChatMessage,
    artifact: ArchivedArtifact,
    *,
    head: str,
    tail: str,
) -> str:
    return json.dumps(
        {
            "estimated_tokens": artifact.estimated_tokens,
            "head": head,
            "kind": artifact.kind,
            "original_chars": artifact.original_chars,
            "path": artifact.path,
            "role": message.role,
            "sha256": artifact.sha256,
            "tail": tail,
            "tool_call_id": message.tool_call_id,
            "truncated": True,
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _rollback_quietly(transaction: ArchiveTransaction) -> None:
    try:
        transaction.rollback()
    except Exception:
        return


def _summary_budget(config: CompactConfig, policy: CompactPolicy) -> int:
    return config.context_window_tokens - policy.manual_reserve_tokens


def _first_oversized_summary_message(
    messages: Sequence[ChatMessage],
    budget: int,
) -> int | None:
    for index, message in enumerate(messages):
        if _summary_request_tokens((message,)) > budget:
            return index
    return None


def _earliest_containable_block(
    messages: Sequence[ChatMessage],
    budget: int,
) -> tuple[int, int]:
    end = 0
    for candidate_end in range(1, len(messages) + 1):
        if _summary_request_tokens(messages[:candidate_end]) > budget:
            break
        end = candidate_end
    if end == 0:
        raise _summary_error(
            CompactFailureCode.BUDGET_NOT_RECOVERED,
            "没有可容纳的递归摘要块。",
        )
    return 0, end


def _temporary_summary_content(summary: str, *, message_count: int) -> str:
    return json.dumps(
        {
            "kind": "temporary_summary",
            "message_count": message_count,
            "summary": summary,
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _split_message_for_summary_budget(
    message: ChatMessage,
    *,
    config: CompactConfig,
    policy: CompactPolicy,
) -> tuple[str, ...]:
    budget = config.context_window_tokens - policy.manual_reserve_tokens
    if budget <= 0:
        raise _summary_error(
            CompactFailureCode.BUDGET_NOT_RECOVERED,
            "摘要请求预算不足。",
        )

    chunks: list[str] = []
    offset = 0
    text = message.content
    while offset < len(text):
        end = _summary_chunk_end(message, offset=offset, budget=budget)
        if end <= offset:
            raise _summary_error(
                CompactFailureCode.BUDGET_NOT_RECOVERED,
                "单条消息无法切出可摘要分片。",
            )
        chunks.append(text[offset:end])
        offset = end
    return tuple(chunks or ("",))


def _summary_chunk_end(message: ChatMessage, *, offset: int, budget: int) -> int:
    text = message.content
    low = offset + 1
    high = len(text)
    best = offset
    while low <= high:
        mid = (low + high) // 2
        candidate = replace(message, content=text[offset:mid])
        if _summary_request_tokens((candidate,)) <= budget:
            best = mid
            low = mid + 1
        else:
            high = mid - 1
    return best


def _summary_request_tokens(messages: Sequence[ChatMessage]) -> int:
    request_estimator = TokenEstimator()
    request = [ChatMessage(role="user", content=build_summary_prompt(messages))]
    return request_estimator.estimate(request_estimator.snapshot(request, [])).tokens


def _forced_summary_preview(
    message: ChatMessage,
    artifact: ArchivedArtifact,
    *,
    temporary_summaries: tuple[str, ...],
) -> str:
    return json.dumps(
        {
            "estimated_tokens": artifact.estimated_tokens,
            "kind": artifact.kind,
            "original_chars": artifact.original_chars,
            "original_role": message.role,
            "path": artifact.path,
            "sha256": artifact.sha256,
            "temporary_summaries": list(temporary_summaries),
            "truncated": True,
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _summary_wait_timeout(
    model_timeout_seconds: float | None,
    run_deadline: float | None,
) -> float | None:
    # 摘要子调用同时受模型静默超时和整次运行截止时间约束，实际等待取更早到达的一侧。
    timeouts = []
    if model_timeout_seconds is not None:
        timeouts.append(model_timeout_seconds)
    if run_deadline is not None:
        timeouts.append(run_deadline - time.monotonic())
    if not timeouts:
        return None
    return min(timeouts)


def _summary_error(code: CompactFailureCode, message: str) -> CompactError:
    return CompactError(
        CompactReport(
            status=CompactStatus.FAILED,
            actions=(CompactAction.HEAVY,),
            before_tokens=0,
            after_tokens=0,
            archived_count=0,
            attempts=1,
            circuit_open=False,
            failure_code=code,
            message_zh=message,
        )
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
