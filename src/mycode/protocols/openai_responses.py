from __future__ import annotations

import httpx

from mycode.config import LLMConfig
from mycode.llm import BaseLLM, ChatMessage, StreamEvent, StreamEventType
from mycode.protocols.common import join_url, parse_json_object, raise_for_bad_status
from mycode.protocols.sse import parse_sse_events_async


class OpenAIResponsesLLM(BaseLLM):
    def __init__(self, config: LLMConfig, http_client: httpx.AsyncClient | None = None) -> None:
        self.config = config
        self._client = http_client or httpx.AsyncClient(timeout=None)

    async def stream_chat(self, messages: list[ChatMessage]):
        url = join_url(self.config.base_url, "/responses")
        payload = {
            "model": self.config.model,
            "input": [_message_to_dict(message) for message in messages],
            "stream": True,
        }
        headers = {
            "authorization": f"Bearer {self.config.api_key}",
            "accept": "text/event-stream",
        }

        # Responses API 的输出文本增量事件会被压平成统一 text_delta。
        async with self._client.stream("POST", url, headers=headers, json=payload) as response:
            raise_for_bad_status(response)
            async for sse_event in parse_sse_events_async(response.aiter_lines()):
                event = _map_openai_responses_event(sse_event.data)
                if event is not None:
                    yield event


def _message_to_dict(message: ChatMessage) -> dict[str, str]:
    return {"role": message.role, "content": message.content}


def _map_openai_responses_event(data: str) -> StreamEvent | None:
    payload = parse_json_object(data)
    event_type = payload.get("type")
    if event_type == "response.output_text.delta":
        return StreamEvent(StreamEventType.TEXT_DELTA, str(payload.get("delta", "")))
    if event_type == "response.completed":
        return StreamEvent(StreamEventType.DONE)
    if event_type == "response.failed":
        return StreamEvent(StreamEventType.ERROR, "OpenAI Responses request failed.")
    return None
