from __future__ import annotations

import json
from dataclasses import dataclass

import httpx

from mycode.config import LLMConfig
from mycode.llm import (
    BaseLLM,
    ChatMessage,
    LLMError,
    StreamEvent,
    StreamEventType,
    UsageObservation,
)
from mycode.protocols.common import join_url, parse_json_object, raise_for_bad_status
from mycode.protocols.sse import parse_sse_events_async
from mycode.tool import ToolCall, ToolDefinition


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
        system, anthropic_messages = _messages_to_anthropic(messages)
        payload = {
            "model": self.config.model,
            "messages": anthropic_messages,
            "max_tokens": 4096,
            "stream": True,
        }
        if system:
            payload["system"] = system
        if tools:
            payload["tools"] = [_tool_to_anthropic_spec(tool) for tool in tools]
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
            pending_tool_uses: dict[int, _AnthropicToolUseState] = {}
            async for sse_event in parse_sse_events_async(response.aiter_lines()):
                payload = parse_json_object(sse_event.data)
                usage.observe(payload)
                event = _map_anthropic_event(
                    payload,
                    usage.to_observation(),
                    pending_tool_uses,
                )
                if event is not None:
                    yield event


def _messages_to_anthropic(
    messages: list[ChatMessage],
) -> tuple[str | None, list[dict[str, object]]]:
    system_parts: list[str] = []
    converted: list[dict[str, object]] = []
    for message in messages:
        if message.role == "system":
            system_parts.append(message.content)
            continue
        mapped = _message_to_dict(message)
        if (
            converted
            and converted[-1]["role"] == mapped["role"]
            and isinstance(converted[-1]["content"], list)
            and isinstance(mapped["content"], list)
        ):
            converted[-1]["content"].extend(mapped["content"])
            continue
        converted.append(mapped)
    system = "\n\n".join(system_parts) if system_parts else None
    return system, converted


def _message_to_dict(message: ChatMessage) -> dict[str, object]:
    if message.role == "assistant" and message.tool_call_id and message.tool_name:
        return {
            "role": "assistant",
            "content": [
                {
                    "type": "tool_use",
                    "id": message.tool_call_id,
                    "name": message.tool_name,
                    "input": _parse_tool_input(message.tool_arguments),
                }
            ],
        }
    if message.role == "tool" and message.tool_call_id:
        return {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": message.tool_call_id,
                    "content": message.content,
                }
            ],
        }
    return {
        "role": message.role,
        "content": [{"type": "text", "text": message.content}],
    }


def _tool_to_anthropic_spec(tool: ToolDefinition) -> dict[str, object]:
    return {
        "name": tool.name,
        "description": tool.description,
        "input_schema": tool.parameters,
    }


def _parse_tool_input(raw_arguments: str | None) -> dict[str, object]:
    if raw_arguments is None:
        return {}
    try:
        value = json.loads(raw_arguments)
    except json.JSONDecodeError:
        return {}
    if not isinstance(value, dict):
        return {}
    return value


def _map_anthropic_event(
    payload: dict[str, object],
    usage: UsageObservation | None,
    pending_tool_uses: dict[int, "_AnthropicToolUseState"],
) -> StreamEvent | None:
    event_type = payload.get("type")

    if event_type == "message_stop":
        if pending_tool_uses:
            raise LLMError("Anthropic tool_use stream ended with incomplete tool_use.")
        return StreamEvent(StreamEventType.DONE, usage=usage)

    if event_type == "content_block_start":
        _observe_content_block_start(payload, pending_tool_uses)
        return None

    if event_type == "content_block_stop":
        return _finish_content_block(payload, pending_tool_uses)

    if event_type != "content_block_delta":
        return None

    delta = payload.get("delta")
    if not isinstance(delta, dict):
        return None

    if delta.get("type") == "input_json_delta":
        index = _required_index(payload)
        state = pending_tool_uses.get(index)
        if state is None:
            raise LLMError("Anthropic input_json_delta referenced unknown tool_use.")
        state.raw_arguments += str(delta.get("partial_json", ""))
        return None
    if delta.get("type") == "text_delta":
        return StreamEvent(StreamEventType.TEXT_DELTA, str(delta.get("text", "")))
    if delta.get("type") == "thinking_delta":
        return StreamEvent(StreamEventType.THINKING_DELTA, str(delta.get("thinking", "")))
    return None


@dataclass
class _AnthropicToolUseState:
    id: str
    name: str
    raw_arguments: str


def _observe_content_block_start(
    payload: dict[str, object],
    pending_tool_uses: dict[int, _AnthropicToolUseState],
) -> None:
    content_block = payload.get("content_block")
    if not isinstance(content_block, dict) or content_block.get("type") != "tool_use":
        return
    index = _required_index(payload)
    if index in pending_tool_uses:
        raise LLMError("Anthropic stream repeated a tool_use content block index.")
    tool_call_id = content_block.get("id")
    name = content_block.get("name")
    if not isinstance(tool_call_id, str) or not tool_call_id:
        raise LLMError("Anthropic tool_use block is missing id.")
    if not isinstance(name, str) or not name:
        raise LLMError("Anthropic tool_use block is missing name.")
    initial_input = content_block.get("input")
    raw_arguments = (
        json.dumps(initial_input, separators=(",", ":"))
        if isinstance(initial_input, dict) and initial_input
        else ""
    )
    pending_tool_uses[index] = _AnthropicToolUseState(
        id=tool_call_id,
        name=name,
        raw_arguments=raw_arguments,
    )


def _finish_content_block(
    payload: dict[str, object],
    pending_tool_uses: dict[int, _AnthropicToolUseState],
) -> StreamEvent | None:
    index = _required_index(payload)
    state = pending_tool_uses.pop(index, None)
    if state is None:
        raise LLMError("Anthropic content_block_stop referenced unknown tool_use.")
    return StreamEvent(
        StreamEventType.TOOL_CALL,
        tool_call=ToolCall(
            id=state.id,
            name=state.name,
            arguments=_parse_stream_tool_arguments(state.raw_arguments),
            raw_arguments=state.raw_arguments,
        ),
    )


def _parse_stream_tool_arguments(raw_arguments: str) -> dict[str, object] | None:
    if not raw_arguments:
        return {}
    try:
        value = json.loads(raw_arguments)
    except json.JSONDecodeError:
        return None
    if not isinstance(value, dict):
        return None
    return value


def _required_index(payload: dict[str, object]) -> int:
    index = payload.get("index")
    if type(index) is not int:
        raise LLMError("Anthropic tool_use event is missing content block index.")
    return index


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
