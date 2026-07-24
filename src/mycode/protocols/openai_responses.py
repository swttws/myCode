from __future__ import annotations

import json
import httpx

from mycode.config import LLMConfig
from mycode.llm import BaseLLM, ChatMessage, StreamEvent, StreamEventType, UsageObservation
from mycode.tool import ToolCall, ToolDefinition
from mycode.protocols.common import join_url, parse_json_object, raise_for_bad_status
from mycode.protocols.sse import parse_sse_events_async


class OpenAIResponsesLLM(BaseLLM):
    def __init__(self, config: LLMConfig, http_client: httpx.AsyncClient | None = None) -> None:
        self.config = config
        self._client = http_client or httpx.AsyncClient(timeout=None)

    async def stream_chat(self, messages: list[ChatMessage], tools: list[ToolDefinition] | None = None):
        url = join_url(self.config.base_url, "/responses")
        payload = {
            "model": self.config.model,
            "input": [_message_to_dict(message) for message in messages],
            "stream": True,
        }
        if tools:
            payload["tools"] = [_tool_to_openai_spec(tool) for tool in tools]
            payload["parallel_tool_calls"] = False
        headers = {
            "authorization": f"Bearer {self.config.api_key}",
            "accept": "text/event-stream",
        }

        # Responses API 的输出文本增量事件会被压平成统一 text_delta。
        async with self._client.stream("POST", url, headers=headers, json=payload) as response:
            raise_for_bad_status(response)
            pending_tool_calls: dict[str, dict[str, str]] = {}
            async for sse_event in parse_sse_events_async(response.aiter_lines()):
                if sse_event.data.strip() == "[DONE]":
                    break
                payload = parse_json_object(sse_event.data)
                event = _map_openai_responses_tool_event(payload, pending_tool_calls)
                if event is not None:
                    yield event
                    continue
                event = _map_openai_responses_event(payload)
                if event is not None:
                    yield event


def _message_to_dict(message: ChatMessage) -> dict[str, str]:
    if message.role == "assistant" and message.tool_call_id and message.tool_name:
        return {
            "type": "function_call",
            "call_id": message.tool_call_id,
            "name": message.tool_name,
            "arguments": message.tool_arguments or "{}",
        }
    if message.role == "tool" and message.tool_call_id:
        return {
            "type": "function_call_output",
            "call_id": message.tool_call_id,
            "output": message.content,
        }
    return {"role": message.role, "content": message.content}


def _tool_to_openai_spec(tool: ToolDefinition) -> dict[str, object]:
    return {
        "type": "function",
        "name": tool.name,
        "description": tool.description,
        "parameters": tool.parameters,
        "strict": False,
    }


def _map_openai_responses_event(payload: dict[str, object]) -> StreamEvent | None:
    event_type = payload.get("type")
    if event_type == "response.output_text.delta":
        return StreamEvent(StreamEventType.TEXT_DELTA, str(payload.get("delta", "")))
    if event_type == "response.completed":
        return StreamEvent(StreamEventType.DONE, usage=_parse_openai_responses_usage(payload))
    if event_type == "response.failed":
        return StreamEvent(StreamEventType.ERROR, "OpenAI Responses request failed.")
    return None


def _parse_openai_responses_usage(payload: dict[str, object]) -> UsageObservation | None:
    response = payload.get("response")
    if not isinstance(response, dict):
        return None
    usage = response.get("usage")
    if not isinstance(usage, dict):
        return None
    input_tokens = _non_negative_int(usage.get("input_tokens"))
    output_tokens = _non_negative_int(usage.get("output_tokens"))
    total_tokens = _non_negative_int(usage.get("total_tokens"))
    details = usage.get("input_tokens_details")
    cache_read_tokens = (
        _non_negative_int(details.get("cached_tokens"))
        if isinstance(details, dict)
        else None
    )
    if all(value is None for value in (input_tokens, output_tokens, total_tokens, cache_read_tokens)):
        return None
    return UsageObservation(
        provider="openai_responses",
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=total_tokens,
        cache_read_tokens=cache_read_tokens,
    )


def _non_negative_int(value: object) -> int | None:
    if type(value) is int and value >= 0:
        return value
    return None


def _map_openai_responses_tool_event(
    payload: dict[str, object],
    pending_tool_calls: dict[str, dict[str, str]],
) -> StreamEvent | None:
    event_type = payload.get("type")
    output_key = str(payload.get("output_index", 0))

    if event_type == "response.output_item.added":
        item = payload.get("item")
        if not isinstance(item, dict) or item.get("type") != "function_call":
            return None
        pending_tool_calls[output_key] = {
            "id": str(item.get("call_id") or item.get("id") or output_key),
            "name": str(item.get("name") or ""),
            "arguments": str(item.get("arguments") or ""),
        }
        return None

    if event_type == "response.function_call_arguments.delta":
        state = pending_tool_calls.setdefault(output_key, {"id": output_key, "name": "", "arguments": ""})
        state["arguments"] += str(payload.get("delta", ""))
        return None

    if event_type != "response.function_call_arguments.done":
        return None

    state = pending_tool_calls.pop(output_key, {"id": output_key, "name": "", "arguments": ""})
    raw_arguments = str(payload.get("arguments") or state["arguments"])
    parsed_arguments = _parse_tool_arguments(raw_arguments)
    return StreamEvent(
        StreamEventType.TOOL_CALL,
        tool_call=ToolCall(
            id=state["id"],
            name=state["name"],
            arguments=parsed_arguments,
            raw_arguments=raw_arguments,
        ),
    )


def _parse_tool_arguments(raw_arguments: str) -> dict[str, object] | None:
    try:
        value = json.loads(raw_arguments)
    except json.JSONDecodeError:
        return None
    if not isinstance(value, dict):
        return None
    return value
