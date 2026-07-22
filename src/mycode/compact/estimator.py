from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from math import ceil

from mycode.compact.models import RequestSnapshot
from mycode.llm import ChatMessage
from mycode.tool import ToolDefinition


class TokenEstimator:
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
        return ceil(ascii_chars / 4) + ceil(non_ascii_chars / 1.5)

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
    def _message_payload(message: ChatMessage) -> dict[str, str | None]:
        return {
            "role": message.role,
            "content": message.content,
            "tool_call_id": message.tool_call_id,
            "tool_name": message.tool_name,
            "tool_arguments": message.tool_arguments,
        }

    @staticmethod
    def _tool_payload(tool: ToolDefinition) -> dict[str, object]:
        return {
            "name": tool.name,
            "description": tool.description,
            "parameters": tool.parameters,
        }
