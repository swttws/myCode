import json

import httpx
import pytest

from mycode.compact.models import CompactConfig
from mycode.config import LLMConfig, ThinkingConfig
from mycode.llm import (
    ChatMessage,
    LLMError,
    StreamEvent,
    StreamEventType,
    UsageObservation,
)
from mycode.protocols.anthropic import AnthropicLLM
from mycode.tool import ToolCall, ToolDefinition, ToolKind
from tests.helpers import collect_async


TEST_COMPACT_CONFIG = CompactConfig(context_window_tokens=128_000)


def make_response(body: str, request_log: list[httpx.Request]):
    async def handler(request: httpx.Request) -> httpx.Response:
        request_log.append(request)
        return httpx.Response(200, content=body.encode("utf-8"))

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def test_anthropic_maps_text_thinking_and_done_events():
    request_log: list[httpx.Request] = []
    body = "\n".join(
        [
            "event: content_block_delta",
            'data: {"type":"content_block_delta","delta":{"type":"text_delta","text":"Hel"}}',
            "",
            "event: content_block_delta",
            'data: {"type":"content_block_delta","delta":{"type":"thinking_delta","thinking":"hmm"}}',
            "",
            "event: message_stop",
            'data: {"type":"message_stop"}',
            "",
        ]
    )
    config = LLMConfig(
        protocol="anthropic",
        model="claude-test",
        base_url="https://api.anthropic.test",
        api_key="sk-test",
        compact=TEST_COMPACT_CONFIG,
        thinking=ThinkingConfig(enabled=True, budget_tokens=2048),
    )
    llm = AnthropicLLM(config, http_client=make_response(body, request_log))

    import asyncio

    events = asyncio.run(collect_async(llm.stream_chat([ChatMessage(role="user", content="hello")])))

    assert events == [
        StreamEvent(StreamEventType.TEXT_DELTA, "Hel"),
        StreamEvent(StreamEventType.THINKING_DELTA, "hmm"),
        StreamEvent(StreamEventType.DONE),
    ]
    request = request_log[0]
    assert str(request.url) == "https://api.anthropic.test/v1/messages"
    assert request.headers["x-api-key"] == "sk-test"
    payload = json.loads(request.content)
    assert payload["model"] == "claude-test"
    assert payload["stream"] is True
    assert payload["messages"] == [
        {"role": "user", "content": [{"type": "text", "text": "hello"}]}
    ]
    assert payload["thinking"] == {"type": "enabled", "budget_tokens": 2048}


def test_anthropic_sends_tools_schema_in_stable_order():
    request_log: list[httpx.Request] = []
    body = "\n".join(
        [
            "event: message_stop",
            'data: {"type":"message_stop"}',
            "",
        ]
    )
    config = LLMConfig(
        protocol="anthropic",
        model="claude-test",
        base_url="https://api.anthropic.test",
        api_key="sk-test",
        compact=TEST_COMPACT_CONFIG,
    )
    llm = AnthropicLLM(config, http_client=make_response(body, request_log))
    tools = [
        ToolDefinition(
            name="zeta",
            description="Zeta tool.",
            parameters={"type": "object", "properties": {"value": {"type": "string"}}},
            kind=ToolKind.READ,
        ),
        ToolDefinition(
            name="alpha",
            description="Alpha tool.",
            parameters={"type": "object", "properties": {}},
            kind=ToolKind.WRITE,
        ),
    ]

    import asyncio

    events = asyncio.run(collect_async(llm.stream_chat([ChatMessage(role="user", content="hello")], tools=tools)))

    assert events == [StreamEvent(StreamEventType.DONE)]
    payload = json.loads(request_log[0].content)
    assert payload["tools"] == [
        {
            "name": "zeta",
            "description": "Zeta tool.",
            "input_schema": {"type": "object", "properties": {"value": {"type": "string"}}},
        },
        {
            "name": "alpha",
            "description": "Alpha tool.",
            "input_schema": {"type": "object", "properties": {}},
        },
    ]


def test_anthropic_moves_system_messages_to_top_level_system():
    request_log: list[httpx.Request] = []
    body = "event: message_stop\ndata: {\"type\":\"message_stop\"}\n"
    config = LLMConfig(
        protocol="anthropic",
        model="claude-test",
        base_url="https://api.anthropic.test",
        api_key="sk-test",
        compact=TEST_COMPACT_CONFIG,
    )
    llm = AnthropicLLM(config, http_client=make_response(body, request_log))

    import asyncio

    asyncio.run(
        collect_async(
            llm.stream_chat(
                [
                    ChatMessage(role="system", content="第一条系统规则"),
                    ChatMessage(role="user", content="hello"),
                    ChatMessage(role="system", content="第二条系统规则"),
                ]
            )
        )
    )

    payload = json.loads(request_log[0].content)
    assert payload["system"] == "第一条系统规则\n\n第二条系统规则"
    assert payload["messages"] == [
        {"role": "user", "content": [{"type": "text", "text": "hello"}]}
    ]


def test_anthropic_serializes_tool_use_and_tool_result_history():
    request_log: list[httpx.Request] = []
    body = "event: message_stop\ndata: {\"type\":\"message_stop\"}\n"
    config = LLMConfig(
        protocol="anthropic",
        model="claude-test",
        base_url="https://api.anthropic.test",
        api_key="sk-test",
        compact=TEST_COMPACT_CONFIG,
    )
    llm = AnthropicLLM(config, http_client=make_response(body, request_log))

    import asyncio

    asyncio.run(
        collect_async(
            llm.stream_chat(
                [
                    ChatMessage(role="assistant", content="", tool_call_id="call-1", tool_name="read_file", tool_arguments='{"path":"a.py"}'),
                    ChatMessage(role="assistant", content="", tool_call_id="call-2", tool_name="search_code", tool_arguments="{bad"),
                    ChatMessage(role="tool", content='{"text":"hello"}', tool_call_id="call-1", tool_name="read_file"),
                    ChatMessage(role="tool", content='{"matches":[]}', tool_call_id="call-2", tool_name="search_code"),
                    ChatMessage(role="assistant", content="done"),
                ]
            )
        )
    )

    payload = json.loads(request_log[0].content)
    assert payload["messages"] == [
        {
            "role": "assistant",
            "content": [
                {
                    "type": "tool_use",
                    "id": "call-1",
                    "name": "read_file",
                    "input": {"path": "a.py"},
                },
                {
                    "type": "tool_use",
                    "id": "call-2",
                    "name": "search_code",
                    "input": {},
                },
            ],
        },
        {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": "call-1", "content": '{"text":"hello"}'},
                {"type": "tool_result", "tool_use_id": "call-2", "content": '{"matches":[]}'},
            ],
        },
        {"role": "assistant", "content": [{"type": "text", "text": "done"}]},
    ]


def test_anthropic_accumulates_usage_on_done():
    request_log: list[httpx.Request] = []
    body = "\n".join(
        [
            "event: message_start",
            (
                'data: {"type":"message_start","message":{"usage":{'
                '"input_tokens":11,"cache_read_input_tokens":2,'
                '"cache_creation_input_tokens":3}}}'
            ),
            "",
            "event: content_block_delta",
            'data: {"type":"content_block_delta","delta":{"type":"text_delta","text":"Hi"}}',
            "",
            "event: message_delta",
            'data: {"type":"message_delta","usage":{"output_tokens":5}}',
            "",
            "event: message_stop",
            'data: {"type":"message_stop"}',
            "",
        ]
    )
    config = LLMConfig(
        protocol="anthropic",
        model="claude-test",
        base_url="https://api.anthropic.test",
        api_key="sk-test",
        compact=TEST_COMPACT_CONFIG,
    )
    llm = AnthropicLLM(config, http_client=make_response(body, request_log))

    import asyncio

    events = asyncio.run(collect_async(llm.stream_chat([ChatMessage(role="user", content="hello")])))

    assert events == [
        StreamEvent(StreamEventType.TEXT_DELTA, "Hi"),
        StreamEvent(
            StreamEventType.DONE,
            usage=UsageObservation(
                provider="anthropic",
                input_tokens=11,
                output_tokens=5,
                cache_read_tokens=2,
                cache_write_tokens=3,
            ),
        ),
    ]


def test_anthropic_ignores_invalid_usage_and_preserves_text_and_thinking():
    request_log: list[httpx.Request] = []
    body = "\n".join(
        [
            "event: message_start",
            'data: {"type":"message_start","message":{"usage":{"input_tokens":"bad"}}}',
            "",
            "event: content_block_delta",
            'data: {"type":"content_block_delta","delta":{"type":"thinking_delta","thinking":"hmm"}}',
            "",
            "event: content_block_delta",
            'data: {"type":"content_block_delta","delta":{"type":"text_delta","text":"Visible"}}',
            "",
            "event: message_delta",
            'data: {"type":"message_delta","usage":{"output_tokens":"bad"}}',
            "",
            "event: message_stop",
            'data: {"type":"message_stop"}',
            "",
        ]
    )
    config = LLMConfig(
        protocol="anthropic",
        model="claude-test",
        base_url="https://api.anthropic.test",
        api_key="sk-test",
        compact=TEST_COMPACT_CONFIG,
    )
    llm = AnthropicLLM(config, http_client=make_response(body, request_log))

    import asyncio

    events = asyncio.run(collect_async(llm.stream_chat([ChatMessage(role="user", content="hello")])))

    assert events == [
        StreamEvent(StreamEventType.THINKING_DELTA, "hmm"),
        StreamEvent(StreamEventType.TEXT_DELTA, "Visible"),
        StreamEvent(StreamEventType.DONE),
    ]


def test_anthropic_streams_tool_use_input_json_delta_as_tool_call():
    request_log: list[httpx.Request] = []
    body = "\n".join(
        [
            "event: content_block_start",
            (
                'data: {"type":"content_block_start","index":0,'
                '"content_block":{"type":"tool_use","id":"call-1","name":"read_file","input":{}}}'
            ),
            "",
            "event: content_block_delta",
            (
                'data: {"type":"content_block_delta","index":0,'
                '"delta":{"type":"input_json_delta","partial_json":"{\\"path\\""}}'
            ),
            "",
            "event: content_block_delta",
            (
                'data: {"type":"content_block_delta","index":0,'
                '"delta":{"type":"input_json_delta","partial_json":":\\"a.py\\"}"}}'
            ),
            "",
            "event: content_block_stop",
            'data: {"type":"content_block_stop","index":0}',
            "",
            "event: message_stop",
            'data: {"type":"message_stop"}',
            "",
        ]
    )
    config = LLMConfig(
        protocol="anthropic",
        model="claude-test",
        base_url="https://api.anthropic.test",
        api_key="sk-test",
        compact=TEST_COMPACT_CONFIG,
    )
    llm = AnthropicLLM(config, http_client=make_response(body, request_log))

    import asyncio

    events = asyncio.run(collect_async(llm.stream_chat([ChatMessage(role="user", content="hello")])))

    assert events == [
        StreamEvent(
            StreamEventType.TOOL_CALL,
            tool_call=ToolCall(
                id="call-1",
                name="read_file",
                arguments={"path": "a.py"},
                raw_arguments='{"path":"a.py"}',
            ),
        ),
        StreamEvent(StreamEventType.DONE),
    ]


def test_anthropic_streams_parallel_tool_use_blocks_independently_with_usage():
    request_log: list[httpx.Request] = []
    body = "\n".join(
        [
            "event: message_start",
            'data: {"type":"message_start","message":{"usage":{"input_tokens":7}}}',
            "",
            "event: content_block_delta",
            'data: {"type":"content_block_delta","delta":{"type":"text_delta","text":"A"}}',
            "",
            "event: content_block_start",
            (
                'data: {"type":"content_block_start","index":1,'
                '"content_block":{"type":"tool_use","id":"call-a","name":"alpha","input":{}}}'
            ),
            "",
            "event: content_block_start",
            (
                'data: {"type":"content_block_start","index":2,'
                '"content_block":{"type":"tool_use","id":"call-b","name":"beta","input":{}}}'
            ),
            "",
            "event: content_block_delta",
            (
                'data: {"type":"content_block_delta","index":2,'
                '"delta":{"type":"input_json_delta","partial_json":"{\\"b\\":2}"}}'
            ),
            "",
            "event: content_block_delta",
            (
                'data: {"type":"content_block_delta","index":1,'
                '"delta":{"type":"input_json_delta","partial_json":"{\\"a\\":1}"}}'
            ),
            "",
            "event: content_block_stop",
            'data: {"type":"content_block_stop","index":1}',
            "",
            "event: content_block_delta",
            'data: {"type":"content_block_delta","delta":{"type":"thinking_delta","thinking":"h"}}',
            "",
            "event: content_block_stop",
            'data: {"type":"content_block_stop","index":2}',
            "",
            "event: message_delta",
            'data: {"type":"message_delta","usage":{"output_tokens":3}}',
            "",
            "event: message_stop",
            'data: {"type":"message_stop"}',
            "",
        ]
    )
    config = LLMConfig(
        protocol="anthropic",
        model="claude-test",
        base_url="https://api.anthropic.test",
        api_key="sk-test",
        compact=TEST_COMPACT_CONFIG,
    )
    llm = AnthropicLLM(config, http_client=make_response(body, request_log))

    import asyncio

    events = asyncio.run(collect_async(llm.stream_chat([ChatMessage(role="user", content="hello")])))

    assert events == [
        StreamEvent(StreamEventType.TEXT_DELTA, "A"),
        StreamEvent(
            StreamEventType.TOOL_CALL,
            tool_call=ToolCall("call-a", "alpha", {"a": 1}, '{"a":1}'),
        ),
        StreamEvent(StreamEventType.THINKING_DELTA, "h"),
        StreamEvent(
            StreamEventType.TOOL_CALL,
            tool_call=ToolCall("call-b", "beta", {"b": 2}, '{"b":2}'),
        ),
        StreamEvent(
            StreamEventType.DONE,
            usage=UsageObservation(
                provider="anthropic",
                input_tokens=7,
                output_tokens=3,
            ),
        ),
    ]


def test_anthropic_streams_invalid_tool_json_as_tool_call_with_raw_arguments():
    request_log: list[httpx.Request] = []
    body = "\n".join(
        [
            "event: content_block_start",
            (
                'data: {"type":"content_block_start","index":0,'
                '"content_block":{"type":"tool_use","id":"call-1","name":"read_file","input":{}}}'
            ),
            "",
            "event: content_block_delta",
            (
                'data: {"type":"content_block_delta","index":0,'
                '"delta":{"type":"input_json_delta","partial_json":"{bad"}}'
            ),
            "",
            "event: content_block_stop",
            'data: {"type":"content_block_stop","index":0}',
            "",
            "event: message_stop",
            'data: {"type":"message_stop"}',
            "",
        ]
    )
    config = LLMConfig(
        protocol="anthropic",
        model="claude-test",
        base_url="https://api.anthropic.test",
        api_key="sk-test",
        compact=TEST_COMPACT_CONFIG,
    )
    llm = AnthropicLLM(config, http_client=make_response(body, request_log))

    import asyncio

    events = asyncio.run(collect_async(llm.stream_chat([ChatMessage(role="user", content="hello")])))

    assert events[0] == StreamEvent(
        StreamEventType.TOOL_CALL,
        tool_call=ToolCall("call-1", "read_file", None, "{bad"),
    )


def test_anthropic_streaming_tool_use_state_fails_closed():
    config = LLMConfig(
        protocol="anthropic",
        model="claude-test",
        base_url="https://api.anthropic.test",
        api_key="sk-test",
        compact=TEST_COMPACT_CONFIG,
    )

    import asyncio

    bad_bodies = [
        "\n".join(
            [
                "event: content_block_start",
                'data: {"type":"content_block_start","index":0,"content_block":{"type":"tool_use","id":"call-1"}}',
                "",
            ]
        ),
        "\n".join(
            [
                "event: content_block_start",
                'data: {"type":"content_block_start","index":0,"content_block":{"type":"tool_use","id":"call-1","name":"a"}}',
                "",
                "event: content_block_start",
                'data: {"type":"content_block_start","index":0,"content_block":{"type":"tool_use","id":"call-2","name":"b"}}',
                "",
            ]
        ),
        "\n".join(
            [
                "event: content_block_stop",
                'data: {"type":"content_block_stop","index":99}',
                "",
            ]
        ),
    ]

    for body in bad_bodies:
        llm = AnthropicLLM(config, http_client=make_response(body, []))
        with pytest.raises(LLMError, match="tool_use"):
            asyncio.run(collect_async(llm.stream_chat([ChatMessage(role="user", content="hello")])))
