from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from math import ceil

from mycode.compact.models import RequestSnapshot, TokenEstimate
from mycode.llm import ChatMessage, UsageObservation
from mycode.tool import ToolDefinition


class TokenEstimator:
    def __init__(self) -> None:
        self._usage_anchor: tuple[int, int] | None = None

    def snapshot(
        self,
        messages: Sequence[ChatMessage],
        tools: Sequence[ToolDefinition],
    ) -> RequestSnapshot:
        request_text = self._serialize_request(messages, tools)
        ascii_chars = sum(character.isascii() for character in request_text)
        non_ascii_chars = len(request_text) - ascii_chars
        return RequestSnapshot(
            ascii_chars=ascii_chars,
            non_ascii_chars=non_ascii_chars,
            fingerprint=hashlib.sha256(request_text.encode("utf-8")).hexdigest(),
        )

    def estimate_text(self, text: str) -> int:
        ascii_chars = sum(character.isascii() for character in text)
        non_ascii_chars = len(text) - ascii_chars
        return self._estimate_character_counts(ascii_chars, non_ascii_chars)

    def estimate(self, snapshot: RequestSnapshot) -> TokenEstimate:
        character_estimate = self._estimate_snapshot(snapshot)
        if self._usage_anchor is None:
            return TokenEstimate(
                tokens=character_estimate,
                source="full_chars",
                delta_tokens=character_estimate,
            )

        anchor_input_tokens, anchor_character_estimate = self._usage_anchor
        delta_tokens = character_estimate - anchor_character_estimate
        return TokenEstimate(
            tokens=max(0, anchor_input_tokens + delta_tokens),
            source="usage_delta",
            delta_tokens=delta_tokens,
            anchor_input_tokens=anchor_input_tokens,
        )

    def record_usage(self, snapshot: RequestSnapshot, usage: UsageObservation) -> None:
        input_tokens = usage.input_tokens
        if type(input_tokens) is not int or input_tokens < 0:
            return

        # 仅使用输入 token 作为锚点，避免输出和缓存 token 影响后续请求的上下文估算。
        self._usage_anchor = (input_tokens, self._estimate_snapshot(snapshot))

    def reset(self) -> None:
        self._usage_anchor = None

    def _serialize_request(
        self,
        messages: Sequence[ChatMessage],
        tools: Sequence[ToolDefinition],
    ) -> str:
        payload = {
            "messages": [self._message_payload(message) for message in messages],
            "tools": [self._tool_payload(tool) for tool in tools],
        }
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)

    @staticmethod
    def _estimate_snapshot(snapshot: RequestSnapshot) -> int:
        return TokenEstimator._estimate_character_counts(
            snapshot.ascii_chars,
            snapshot.non_ascii_chars,
        )

    @staticmethod
    def _estimate_character_counts(ascii_chars: int, non_ascii_chars: int) -> int:
        return ceil(ascii_chars / 4) + ceil(non_ascii_chars / 1.5)

    @staticmethod
    def _message_payload(message: ChatMessage) -> dict[str, object]:
        if message.role == "assistant" and message.tool_call_id and message.tool_name:
            return {
                "role": "assistant",
                "tool_calls": [
                    {
                        "id": message.tool_call_id,
                        "type": "function",
                        "function": {
                            "name": message.tool_name,
                            "arguments": message.tool_arguments or "{}",
                        },
                    }
                ],
            }
        if message.role == "tool" and message.tool_call_id:
            return {
                "role": "tool",
                "tool_call_id": message.tool_call_id,
                "content": message.content,
            }
        return {"role": message.role, "content": message.content}

    @staticmethod
    def _tool_payload(tool: ToolDefinition) -> dict[str, object]:
        return {
            "name": tool.name,
            "description": tool.description,
            "parameters": tool.parameters,
        }
