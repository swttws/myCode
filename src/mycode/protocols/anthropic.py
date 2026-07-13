from __future__ import annotations

import httpx

from mycode.config import LLMConfig
from mycode.llm import BaseLLM, ChatMessage, StreamEvent, StreamEventType
from mycode.protocols.common import join_url, parse_json_object, raise_for_bad_status
from mycode.protocols.sse import parse_sse_events_async


class AnthropicLLM(BaseLLM):
    def __init__(self, config: LLMConfig, http_client: httpx.AsyncClient | None = None) -> None:
        self.config = config
        self._client = http_client or httpx.AsyncClient(timeout=None)

    async def stream_chat(self, messages: list[ChatMessage]):
        url = join_url(self.config.base_url, "/v1/messages")
        payload = {
            "model": self.config.model,
            "messages": [_message_to_dict(message) for message in messages],
            "max_tokens": 4096,
            "stream": True,
        }
        if self.config.thinking.enabled:
            payload["thinking"] = {
                "type": "enabled",
                "budget_tokens": self.config.thinking.budget_tokens or 1024,
            }

        headers = {
            "x-api-key": self.config.api_key,
            "anthropic-version": "2023-06-01",
            "accept": "text/event-stream",
        }

        # Anthropic 的 content_block_delta 里同时承载正文和 thinking，这里统一拆成内部事件。
        async with self._client.stream("POST", url, headers=headers, json=payload) as response:
            raise_for_bad_status(response)
            async for sse_event in parse_sse_events_async(response.aiter_lines()):
                event = _map_anthropic_event(sse_event.data)
                if event is not None:
                    yield event


def _message_to_dict(message: ChatMessage) -> dict[str, str]:
    return {"role": message.role, "content": message.content}


def _map_anthropic_event(data: str) -> StreamEvent | None:
    payload = parse_json_object(data)
    event_type = payload.get("type")

    if event_type == "message_stop":
        return StreamEvent(StreamEventType.DONE)

    if event_type != "content_block_delta":
        return None

    delta = payload.get("delta")
    if not isinstance(delta, dict):
        return None

    if delta.get("type") == "text_delta":
        return StreamEvent(StreamEventType.TEXT_DELTA, str(delta.get("text", "")))
    if delta.get("type") == "thinking_delta":
        return StreamEvent(StreamEventType.THINKING_DELTA, str(delta.get("thinking", "")))
    return None
