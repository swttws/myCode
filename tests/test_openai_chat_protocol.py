import json

import httpx

from mycode.config import LLMConfig
from mycode.llm import ChatMessage, StreamEvent, StreamEventType
from mycode.protocols.openai_chat import OpenAIChatLLM
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
