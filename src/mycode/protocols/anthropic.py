from __future__ import annotations

import httpx

from mycode.config import LLMConfig
from mycode.llm import BaseLLM, ChatMessage, StreamEvent, StreamEventType, UsageObservation
from mycode.protocols.common import join_url, parse_json_object, raise_for_bad_status
from mycode.protocols.sse import parse_sse_events_async
from mycode.tool import ToolDefinition


class AnthropicLLM(BaseLLM):
    def __init__(self, config: LLMConfig, http_client: httpx.AsyncClient | None = None) -> None:
        self.config = config
        self._client = http_client or httpx.AsyncClient(timeout=None)

    async def stream_chat(
        self,
        messages: list[ChatMessage],
        tools: list[ToolDefinition] | None = None,
    ):
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
            usage = _AnthropicUsageAccumulator()
            async for sse_event in parse_sse_events_async(response.aiter_lines()):
                payload = parse_json_object(sse_event.data)
                usage.observe(payload)
                event = _map_anthropic_event(payload, usage.to_observation())
                if event is not None:
                    yield event


def _message_to_dict(message: ChatMessage) -> dict[str, str]:
    return {"role": message.role, "content": message.content}


def _map_anthropic_event(
    payload: dict[str, object],
    usage: UsageObservation | None,
) -> StreamEvent | None:
    event_type = payload.get("type")

    if event_type == "message_stop":
        return StreamEvent(StreamEventType.DONE, usage=usage)

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


class _AnthropicUsageAccumulator:
    def __init__(self) -> None:
        self.input_tokens: int | None = None
        self.output_tokens: int | None = None
        self.cache_read_tokens: int | None = None
        self.cache_write_tokens: int | None = None

    def observe(self, payload: dict[str, object]) -> None:
        event_type = payload.get("type")
        if event_type == "message_start":
            message = payload.get("message")
            usage = message.get("usage") if isinstance(message, dict) else None
            if isinstance(usage, dict):
                self.input_tokens = _coalesce_int(self.input_tokens, usage.get("input_tokens"))
                self.cache_read_tokens = _coalesce_int(
                    self.cache_read_tokens,
                    usage.get("cache_read_input_tokens"),
                )
                self.cache_write_tokens = _coalesce_int(
                    self.cache_write_tokens,
                    usage.get("cache_creation_input_tokens"),
                )
            return
        if event_type == "message_delta":
            usage = payload.get("usage")
            if isinstance(usage, dict):
                self.output_tokens = _coalesce_int(self.output_tokens, usage.get("output_tokens"))

    def to_observation(self) -> UsageObservation | None:
        if all(
            value is None
            for value in (
                self.input_tokens,
                self.output_tokens,
                self.cache_read_tokens,
                self.cache_write_tokens,
            )
        ):
            return None
        return UsageObservation(
            provider="anthropic",
            input_tokens=self.input_tokens,
            output_tokens=self.output_tokens,
            cache_read_tokens=self.cache_read_tokens,
            cache_write_tokens=self.cache_write_tokens,
        )


def _coalesce_int(current: int | None, value: object) -> int | None:
    parsed = _non_negative_int(value)
    return current if parsed is None else parsed


def _non_negative_int(value: object) -> int | None:
    if type(value) is int and value >= 0:
        return value
    return None
