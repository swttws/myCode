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
            artifact = transaction.archive_text(kind="tool_result", text=message.content)
            preview = self._preview_content(message, artifact)
            compacted[index] = replace(
                message,
                content=preview,
                origin=MessageOrigin.COMPACT_PREVIEW,
            )
            artifacts.append(artifact)

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

    def _preview_content(self, message: ChatMessage, artifact: ArchivedArtifact) -> str:
        text = message.content
        max_side_chars = min(len(text) // 2, self._policy.preview_tokens)
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
