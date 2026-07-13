import json

import httpx

from mycode.config import LLMConfig
from mycode.llm import ChatMessage, StreamEvent, StreamEventType
from mycode.protocols.openai_responses import OpenAIResponsesLLM
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
