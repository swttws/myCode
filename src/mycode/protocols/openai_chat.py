from __future__ import annotations

import httpx

from mycode.config import LLMConfig
from mycode.llm import BaseLLM, ChatMessage, StreamEvent, StreamEventType
from mycode.protocols.common import join_url, parse_json_object, raise_for_bad_status
from mycode.protocols.sse import parse_sse_events_async


class OpenAIChatLLM(BaseLLM):
    def __init__(self, config: LLMConfig, http_client: httpx.AsyncClient | None = None) -> None:
        self.config = config
        self._client = http_client or httpx.AsyncClient(timeout=None)

    async def stream_chat(self, messages: list[ChatMessage]):
        url = join_url(self.config.base_url, "/chat/completions")
        payload = {
            "model": self.config.model,
            "messages": [_message_to_dict(message) for message in messages],
            "stream": True,
        }
        headers = {
            "authorization": f"Bearer {self.config.api_key}",
            "accept": "text/event-stream",
        }

        # Chat Completions 的每个 chunk 可能只有 role 或 finish_reason，只有 content 才输出。
        async with self._client.stream("POST", url, headers=headers, json=payload) as response:
            raise_for_bad_status(response)
            async for sse_event in parse_sse_events_async(response.aiter_lines()):
                if sse_event.data == "[DONE]":
                    yield StreamEvent(StreamEventType.DONE)
                    continue
                event = _map_openai_chat_event(sse_event.data)
                if event is not None:
                    yield event


def _message_to_dict(message: ChatMessage) -> dict[str, str]:
    return {"role": message.role, "content": message.content}


def _map_openai_chat_event(data: str) -> StreamEvent | None:
    payload = parse_json_object(data)
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        return None

    first_choice = choices[0]
    if not isinstance(first_choice, dict):
        return None
    delta = first_choice.get("delta")
    if not isinstance(delta, dict) or "content" not in delta:
        return None
    return StreamEvent(StreamEventType.TEXT_DELTA, str(delta["content"]))
