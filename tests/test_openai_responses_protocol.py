import json

import httpx

from mycode.config import LLMConfig
from mycode.llm import ChatMessage, StreamEvent, StreamEventType
from mycode.protocols.openai_responses import OpenAIResponsesLLM
from mycode.tool import ToolCall, ToolDefinition
from tests.helpers import collect_async


def test_openai_responses_maps_output_text_and_done_events():
    request_log: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        request_log.append(request)
        body = "\n".join(
            [
                "event: response.output_text.delta",
                'data: {"type":"response.output_text.delta","delta":"Hi"}',
                "",
                "event: response.completed",
                'data: {"type":"response.completed"}',
                "",
            ]
        )
        return httpx.Response(200, content=body.encode("utf-8"))

    config = LLMConfig(
        protocol="openai_responses",
        model="gpt-test",
        base_url="https://api.openai.test/v1",
        api_key="sk-test",
    )
    llm = OpenAIResponsesLLM(config, http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))

    import asyncio

    events = asyncio.run(collect_async(llm.stream_chat([ChatMessage(role="user", content="hello")])))

    assert events == [
        StreamEvent(StreamEventType.TEXT_DELTA, "Hi"),
        StreamEvent(StreamEventType.DONE),
    ]
    request = request_log[0]
    assert str(request.url) == "https://api.openai.test/v1/responses"
    assert request.headers["authorization"] == "Bearer sk-test"
    payload = json.loads(request.content)
    assert payload == {
        "model": "gpt-test",
        "input": [{"role": "user", "content": "hello"}],
        "stream": True,
    }


def test_openai_responses_includes_tools_when_provided():
    request_log: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        request_log.append(request)
        return httpx.Response(
            200,
            content='event: response.completed\ndata: {"type":"response.completed"}\n\n'.encode("utf-8"),
        )

    config = LLMConfig("openai_responses", "gpt-test", "https://api.openai.test/v1", "sk-test")
    llm = OpenAIResponsesLLM(config, http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    tool = ToolDefinition(
        name="read_file",
        description="Read file",
        parameters={"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
    )

    import asyncio

    asyncio.run(collect_async(llm.stream_chat([ChatMessage(role="user", content="hello")], tools=[tool])))

    payload = json.loads(request_log[0].content)
    assert payload["tools"] == [
        {
            "type": "function",
            "name": "read_file",
            "description": "Read file",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
            "strict": False,
        }
    ]
    assert payload["parallel_tool_calls"] is False


def test_openai_responses_omits_tools_when_none():
    request_log: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        request_log.append(request)
        return httpx.Response(
            200,
            content='event: response.completed\ndata: {"type":"response.completed"}\n\n'.encode("utf-8"),
        )

    config = LLMConfig("openai_responses", "gpt-test", "https://api.openai.test/v1", "sk-test")
    llm = OpenAIResponsesLLM(config, http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))

    import asyncio

    asyncio.run(collect_async(llm.stream_chat([ChatMessage(role="user", content="hello")])))

    payload = json.loads(request_log[0].content)
    assert "tools" not in payload
    assert "parallel_tool_calls" not in payload


def test_openai_responses_serializes_tool_call_history():
    request_log: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        request_log.append(request)
        return httpx.Response(
            200,
            content='event: response.completed\ndata: {"type":"response.completed"}\n\n'.encode("utf-8"),
        )

    config = LLMConfig("openai_responses", "gpt-test", "https://api.openai.test/v1", "sk-test")
    llm = OpenAIResponsesLLM(config, http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    messages = [
        ChatMessage(role="assistant", content="", tool_call_id="call-1", tool_name="read_file", tool_arguments='{"path":"README.md"}')
    ]

    import asyncio

    asyncio.run(collect_async(llm.stream_chat(messages)))

    payload = json.loads(request_log[0].content)
    assert payload["input"] == [
        {
            "type": "function_call",
            "call_id": "call-1",
            "name": "read_file",
            "arguments": '{"path":"README.md"}',
        }
    ]


def test_openai_responses_serializes_tool_result_history():
    request_log: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        request_log.append(request)
        return httpx.Response(
            200,
            content='event: response.completed\ndata: {"type":"response.completed"}\n\n'.encode("utf-8"),
        )

    config = LLMConfig("openai_responses", "gpt-test", "https://api.openai.test/v1", "sk-test")
    llm = OpenAIResponsesLLM(config, http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    messages = [ChatMessage(role="tool", content='{"ok":true}', tool_call_id="call-1")]

    import asyncio

    asyncio.run(collect_async(llm.stream_chat(messages)))

    payload = json.loads(request_log[0].content)
    assert payload["input"] == [
        {
            "type": "function_call_output",
            "call_id": "call-1",
            "output": '{"ok":true}',
        }
    ]


def test_openai_responses_streams_function_call_arguments_as_tool_call():
    async def handler(request: httpx.Request) -> httpx.Response:
        body = "\n".join(
            [
                "event: response.output_item.added",
                'data: {"type":"response.output_item.added","output_index":0,"item":{"type":"function_call","call_id":"call-1","name":"read_file"}}',
                "",
                "event: response.function_call_arguments.delta",
                'data: {"type":"response.function_call_arguments.delta","output_index":0,"delta":"{\\"path\\":\\""}',
                "",
                "event: response.function_call_arguments.delta",
                'data: {"type":"response.function_call_arguments.delta","output_index":0,"delta":"README.md\\"}"}',
                "",
                "event: response.function_call_arguments.done",
                'data: {"type":"response.function_call_arguments.done","output_index":0}',
                "",
                "event: response.completed",
                'data: {"type":"response.completed"}',
                "",
            ]
        )
        return httpx.Response(200, content=body.encode("utf-8"))

    config = LLMConfig("openai_responses", "gpt-test", "https://api.openai.test/v1", "sk-test")
    llm = OpenAIResponsesLLM(config, http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))

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


def test_openai_responses_preserves_invalid_function_arguments():
    async def handler(request: httpx.Request) -> httpx.Response:
        body = "\n".join(
            [
                "event: response.output_item.added",
                'data: {"type":"response.output_item.added","output_index":0,"item":{"type":"function_call","call_id":"call-1","name":"read_file"}}',
                "",
                "event: response.function_call_arguments.delta",
                'data: {"type":"response.function_call_arguments.delta","output_index":0,"delta":"{bad"}',
                "",
                "event: response.function_call_arguments.done",
                'data: {"type":"response.function_call_arguments.done","output_index":0}',
                "",
            ]
        )
        return httpx.Response(200, content=body.encode("utf-8"))

    config = LLMConfig("openai_responses", "gpt-test", "https://api.openai.test/v1", "sk-test")
    llm = OpenAIResponsesLLM(config, http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))

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
        )
    ]
