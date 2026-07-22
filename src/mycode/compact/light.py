from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import replace

from mycode.compact.archive import ArchiveTransaction
from mycode.compact.estimator import TokenEstimator
from mycode.compact.models import (
    ArchivedArtifact,
    CompactConfig,
    CompactPolicy,
    LightCompactResult,
)
from mycode.llm import ChatMessage, MessageOrigin


class ToolResultCompactor:
    def __init__(
        self,
        config: CompactConfig,
        *,
        policy: CompactPolicy | None = None,
        estimator: TokenEstimator | None = None,
    ) -> None:
        self._config = config
        self._policy = policy or CompactPolicy()
        self._estimator = estimator or TokenEstimator()

    def compact(
        self,
        history: Sequence[ChatMessage],
        transaction: ArchiveTransaction,
    ) -> LightCompactResult:
        compacted = list(history)
        artifacts: list[ArchivedArtifact] = []

        for index, message in enumerate(history):
            if not self._should_archive_single_result(message):
                continue
            artifact, compacted[index] = self._archive_message(
                message,
                transaction,
                max_preview_tokens=self._policy.preview_tokens,
            )
            artifacts.append(artifact)

        for batch in self._tool_result_batches(compacted):
            while self._batch_estimate(compacted, batch) > self._config.tool_batch_threshold_tokens:
                candidates = [
                    index
                    for index in batch
                    if self._is_unarchived_tool_result(compacted[index])
                ]
                if not candidates:
                    break
                candidate = min(
                    candidates,
                    key=lambda index: (
                        -self._estimator.estimate_text(compacted[index].content),
                        index,
                    ),
                )
                original_estimate = self._estimator.estimate_text(compacted[candidate].content)
                artifact, replacement = self._archive_message(
                    compacted[candidate],
                    transaction,
                    max_preview_tokens=min(
                        self._policy.preview_tokens,
                        max(1, original_estimate // 3),
                    ),
                )
                replacement_estimate = self._estimator.estimate_text(replacement.content)
                compacted[candidate] = replacement
                artifacts.append(artifact)
                if replacement_estimate >= original_estimate:
                    break

        return LightCompactResult(
            history=tuple(compacted),
            artifacts=tuple(artifacts),
            changed=bool(artifacts),
        )

    def _should_archive_single_result(self, message: ChatMessage) -> bool:
        if message.role != "tool" or not message.tool_call_id:
            return False
        if message.origin is MessageOrigin.COMPACT_PREVIEW:
            return False
        return self._estimator.estimate_text(message.content) > self._config.tool_result_threshold_tokens

    @staticmethod
    def _tool_result_batches(history: Sequence[ChatMessage]) -> tuple[tuple[int, ...], ...]:
        batches: list[tuple[int, ...]] = []
        current: list[int] = []
        for index, message in enumerate(history):
            if message.role == "tool":
                current.append(index)
                continue
            if current:
                batches.append(tuple(current))
                current = []
        if current:
            batches.append(tuple(current))
        return tuple(batches)

    def _batch_estimate(self, history: Sequence[ChatMessage], batch: tuple[int, ...]) -> int:
        return sum(self._estimator.estimate_text(history[index].content) for index in batch)

    @staticmethod
    def _is_unarchived_tool_result(message: ChatMessage) -> bool:
        return (
            message.role == "tool"
            and bool(message.tool_call_id)
            and message.origin is not MessageOrigin.COMPACT_PREVIEW
        )

    def _archive_message(
        self,
        message: ChatMessage,
        transaction: ArchiveTransaction,
        *,
        max_preview_tokens: int,
    ) -> tuple[ArchivedArtifact, ChatMessage]:
        artifact = transaction.archive_text(kind="tool_result", text=message.content)
        preview = self._preview_content(
            message,
            artifact,
            max_preview_tokens=max_preview_tokens,
        )
        return (
            artifact,
            replace(
                message,
                content=preview,
                origin=MessageOrigin.COMPACT_PREVIEW,
            ),
        )

    def _preview_content(
        self,
        message: ChatMessage,
        artifact: ArchivedArtifact,
        *,
        max_preview_tokens: int,
    ) -> str:
        text = message.content
        max_side_chars = min(len(text) // 2, max_preview_tokens)
        best_preview = self._preview_json(message, artifact, head="", tail="")
        low = 0
        high = max_side_chars
        while low <= high:
            side_chars = (low + high) // 2
            candidate = self._preview_json(
                message,
                artifact,
                head=text[:side_chars],
                tail=text[-side_chars:] if side_chars else "",
            )
            if self._estimator.estimate_text(candidate) <= self._policy.preview_tokens:
                best_preview = candidate
                low = side_chars + 1
            else:
                high = side_chars - 1
        return best_preview

    @staticmethod
    def _preview_json(
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
                "sha256": artifact.sha256,
                "tail": tail,
                "tool_call_id": message.tool_call_id,
                "truncated": True,
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
