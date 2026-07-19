import json

import httpx

from mycode.config import LLMConfig
from mycode.llm import ChatMessage, StreamEvent, StreamEventType
from mycode.protocols.openai_chat import OpenAIChatLLM
from mycode.tool import ToolCall, ToolDefinition, ToolKind
from tests.helpers import ControlledAsyncByteStream, collect_async


def test_openai_chat_maps_delta_content_and_done_events():
    request_log: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        request_log.append(request)
        body = "\n".join(
            [
                'data: {"choices":[{"delta":{"content":"Hi"}}]}',
                "",
                "data: [DONE]",
                "",
            ]
        )
        return httpx.Response(200, content=body.encode("utf-8"))

    config = LLMConfig(
        protocol="openai_chat",
        model="gpt-test",
        base_url="https://api.openai.test/v1",
        api_key="sk-test",
    )
    llm = OpenAIChatLLM(config, http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))

    import asyncio

    events = asyncio.run(collect_async(llm.stream_chat([ChatMessage(role="user", content="hello")])))

    assert events == [
        StreamEvent(StreamEventType.TEXT_DELTA, "Hi"),
        StreamEvent(StreamEventType.DONE),
    ]
    request = request_log[0]
    assert str(request.url) == "https://api.openai.test/v1/chat/completions"
    assert request.headers["authorization"] == "Bearer sk-test"
    payload = json.loads(request.content)
    assert payload == {
        "model": "gpt-test",
        "messages": [{"role": "user", "content": "hello"}],
        "stream": True,
    }


def test_openai_chat_yields_first_delta_before_stream_finishes():
    async def run():
        stream = ControlledAsyncByteStream(
            b'data: {"choices":[{"delta":{"content":"first"}}]}\n\n',
            [
                b'data: {"choices":[{"delta":{"content":"second"}}]}\n\n',
                b"data: [DONE]\n\n",
            ],
        )

        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, stream=stream)

        config = LLMConfig(
            protocol="openai_chat",
            model="gpt-test",
            base_url="https://api.openai.test/v1",
            api_key="sk-test",
        )
        llm = OpenAIChatLLM(config, http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
        iterator = llm.stream_chat([ChatMessage(role="user", content="hello")]).__aiter__()

        import asyncio

        first_event = await asyncio.wait_for(anext(iterator), timeout=0.5)
        assert first_event == StreamEvent(StreamEventType.TEXT_DELTA, "first")

        stream.release_remaining.set()
        rest = []
        async for event in iterator:
            rest.append(event)
        assert rest == [
            StreamEvent(StreamEventType.TEXT_DELTA, "second"),
            StreamEvent(StreamEventType.DONE),
        ]

    import asyncio

    asyncio.run(run())


def test_openai_chat_includes_tools_when_provided():
    request_log: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        request_log.append(request)
        return httpx.Response(200, content=b"data: [DONE]\n\n")

    config = LLMConfig("openai_chat", "gpt-test", "https://api.openai.test/v1", "sk-test")
    llm = OpenAIChatLLM(config, http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    tool = ToolDefinition(
        name="read_file",
        description="Read file",
        parameters={"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
        kind=ToolKind.READ,
        grant_arguments=("path",),
    )

    import asyncio

    asyncio.run(collect_async(llm.stream_chat([ChatMessage(role="user", content="hello")], tools=[tool])))

    payload = json.loads(request_log[0].content)
    assert payload["tools"] == [
        {
            "type": "function",
            "function": {
                "name": "read_file",
                "description": "Read file",
                "parameters": {
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                    "required": ["path"],
                },
            },
        }
    ]
    assert "kind" not in payload["tools"][0]["function"]
    assert "grant_arguments" not in payload["tools"][0]["function"]
    assert payload["parallel_tool_calls"] is False


def test_openai_chat_omits_tools_when_none():
    request_log: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        request_log.append(request)
        return httpx.Response(200, content=b"data: [DONE]\n\n")

    config = LLMConfig("openai_chat", "gpt-test", "https://api.openai.test/v1", "sk-test")
    llm = OpenAIChatLLM(config, http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))

    import asyncio

    asyncio.run(collect_async(llm.stream_chat([ChatMessage(role="user", content="hello")])))

    payload = json.loads(request_log[0].content)
    assert "tools" not in payload
    assert "parallel_tool_calls" not in payload


def test_openai_chat_serializes_tool_call_history():
    request_log: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        request_log.append(request)
        return httpx.Response(200, content=b"data: [DONE]\n\n")

    config = LLMConfig("openai_chat", "gpt-test", "https://api.openai.test/v1", "sk-test")
    llm = OpenAIChatLLM(config, http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    messages = [
        ChatMessage(role="assistant", content="", tool_call_id="call-1", tool_name="read_file", tool_arguments='{"path":"README.md"}')
    ]

    import asyncio

    asyncio.run(collect_async(llm.stream_chat(messages)))

    payload = json.loads(request_log[0].content)
    assert payload["messages"] == [
        {
            "role": "assistant",
            "tool_calls": [
                {
                    "id": "call-1",
                    "type": "function",
                    "function": {"name": "read_file", "arguments": '{"path":"README.md"}'},
                }
            ],
        }
    ]


def test_openai_chat_serializes_tool_result_history():
    request_log: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        request_log.append(request)
        return httpx.Response(200, content=b"data: [DONE]\n\n")

    config = LLMConfig("openai_chat", "gpt-test", "https://api.openai.test/v1", "sk-test")
    llm = OpenAIChatLLM(config, http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    messages = [ChatMessage(role="tool", content='{"ok":true}', tool_call_id="call-1")]

    import asyncio

    asyncio.run(collect_async(llm.stream_chat(messages)))

    payload = json.loads(request_log[0].content)
    assert payload["messages"] == [
        {"role": "tool", "tool_call_id": "call-1", "content": '{"ok":true}'}
    ]


def test_openai_chat_streams_tool_call_arguments_as_tool_call():
    async def handler(request: httpx.Request) -> httpx.Response:
        chunks = [
            {
                "choices": [
                    {
                        "delta": {
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "id": "call-1",
                                    "type": "function",
                                    "function": {"name": "read_file", "arguments": '{"path":"'},
                                }
                            ]
                        }
                    }
                ]
            },
            {
                "choices": [
                    {
                        "delta": {
                            "tool_calls": [
                                {"index": 0, "function": {"arguments": 'README.md"}'}}
                            ]
                        }
                    }
                ]
            },
        ]
        body = "\n".join([f"data: {json.dumps(chunk)}\n" for chunk in chunks] + ["data: [DONE]\n"])
        return httpx.Response(200, content=body.encode("utf-8"))

    config = LLMConfig("openai_chat", "gpt-test", "https://api.openai.test/v1", "sk-test")
    llm = OpenAIChatLLM(config, http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))

    import asyncio

    events = asyncio.run(collect_async(llm.stream_chat([ChatMessage(role="user", content="hello")])))

    assert events == [
        StreamEvent(
            StreamEventType.TOOL_CALL,
            tool_call=ToolCall(
                id="call-1",
                name="read_file",
                arguments={"path": "README.md"},
                raw_arguments='{"path":"README.md"}',
            ),
        ),
        StreamEvent(StreamEventType.DONE),
    ]


def test_openai_chat_preserves_invalid_tool_call_arguments():
    async def handler(request: httpx.Request) -> httpx.Response:
        chunk = {
            "choices": [
                {
                    "delta": {
                        "tool_calls": [
                            {
                                "index": 0,
                                "id": "call-1",
                                "type": "function",
                                "function": {"name": "read_file", "arguments": "{bad"},
                            }
                        ]
                    }
                }
            ]
        }
        body = f"data: {json.dumps(chunk)}\n\ndata: [DONE]\n\n"
        return httpx.Response(200, content=body.encode("utf-8"))

    config = LLMConfig("openai_chat", "gpt-test", "https://api.openai.test/v1", "sk-test")
    llm = OpenAIChatLLM(config, http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))

    import asyncio

    events = asyncio.run(collect_async(llm.stream_chat([ChatMessage(role="user", content="hello")])))

    assert events == [
        StreamEvent(
            StreamEventType.TOOL_CALL,
            tool_call=ToolCall(
                id="call-1",
                name="read_file",
                arguments=None,
                raw_arguments="{bad",
            ),
        ),
        StreamEvent(StreamEventType.DONE),
    ]
