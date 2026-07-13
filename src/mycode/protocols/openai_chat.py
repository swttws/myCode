from __future__ import annotations

import json
import httpx

from mycode.config import LLMConfig
from mycode.llm import BaseLLM, ChatMessage, StreamEvent, StreamEventType
from mycode.tool import ToolCall, ToolDefinition
from mycode.protocols.common import join_url, parse_json_object, raise_for_bad_status
from mycode.protocols.sse import parse_sse_events_async


class OpenAIChatLLM(BaseLLM):
    def __init__(self, config: LLMConfig, http_client: httpx.AsyncClient | None = None) -> None:
        self.config = config
        self._client = http_client or httpx.AsyncClient(timeout=None)

    async def stream_chat(self, messages: list[ChatMessage], tools: list[ToolDefinition] | None = None):
        url = join_url(self.config.base_url, "/chat/completions")
        payload = {
            "model": self.config.model,
            "messages": [_message_to_dict(message) for message in messages],
            "stream": True,
        }
        if tools:
            payload["tools"] = [_tool_to_openai_spec(tool) for tool in tools]
            payload["parallel_tool_calls"] = False
        headers = {
            "authorization": f"Bearer {self.config.api_key}",
            "accept": "text/event-stream",
        }

        # Chat Completions 的每个 chunk 可能只有 role 或 finish_reason，只有 content 才输出。
        async with self._client.stream("POST", url, headers=headers, json=payload) as response:
            raise_for_bad_status(response)
            pending_tool_calls: dict[str, dict[str, str]] = {}
            async for sse_event in parse_sse_events_async(response.aiter_lines()):
                if sse_event.data == "[DONE]":
                    for event in _flush_openai_chat_tool_calls(pending_tool_calls):
                        yield event
                    yield StreamEvent(StreamEventType.DONE)
                    continue
                payload = parse_json_object(sse_event.data)
                _accumulate_openai_chat_tool_calls(payload, pending_tool_calls)
                event = _map_openai_chat_event(payload)
                if event is not None:
                    yield event


def _message_to_dict(message: ChatMessage) -> dict[str, object]:
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
        return {"role": "tool", "tool_call_id": message.tool_call_id, "content": message.content}
    return {"role": message.role, "content": message.content}


def _tool_to_openai_spec(tool: ToolDefinition) -> dict[str, object]:
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description,
            "parameters": tool.parameters,
        },
    }


def _map_openai_chat_event(payload: dict[str, object]) -> StreamEvent | None:
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


def _accumulate_openai_chat_tool_calls(
    payload: dict[str, object],
    pending_tool_calls: dict[str, dict[str, str]],
) -> None:
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        return
    first_choice = choices[0]
    if not isinstance(first_choice, dict):
        return
    delta = first_choice.get("delta")
    if not isinstance(delta, dict):
        return
    tool_calls = delta.get("tool_calls")
    if not isinstance(tool_calls, list):
        return

    for tool_call_delta in tool_calls:
        if not isinstance(tool_call_delta, dict):
            continue
        index = str(tool_call_delta.get("index", 0))
        state = pending_tool_calls.setdefault(index, {"id": index, "name": "", "arguments": ""})
        if tool_call_delta.get("id"):
            state["id"] = str(tool_call_delta["id"])
        function = tool_call_delta.get("function")
        if not isinstance(function, dict):
            continue
        if function.get("name"):
            state["name"] = str(function["name"])
        if function.get("arguments") is not None:
            state["arguments"] += str(function["arguments"])


def _flush_openai_chat_tool_calls(
    pending_tool_calls: dict[str, dict[str, str]]
) -> list[StreamEvent]:
    events: list[StreamEvent] = []
    for index in sorted(pending_tool_calls, key=int):
        state = pending_tool_calls[index]
        raw_arguments = state["arguments"]
        events.append(
            StreamEvent(
                StreamEventType.TOOL_CALL,
                tool_call=ToolCall(
                    id=state["id"],
                    name=state["name"],
                    arguments=_parse_tool_arguments(raw_arguments),
                    raw_arguments=raw_arguments,
                ),
            )
        )
    pending_tool_calls.clear()
    return events


def _parse_tool_arguments(raw_arguments: str) -> dict[str, object] | None:
    try:
        value = json.loads(raw_arguments)
    except json.JSONDecodeError:
        return None
    if not isinstance(value, dict):
        return None
    return value
