import json

import httpx

from mycode.config import LLMConfig, ThinkingConfig
from mycode.llm import ChatMessage, StreamEvent, StreamEventType
from mycode.protocols.anthropic import AnthropicLLM
from tests.helpers import collect_async


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
    assert payload["messages"] == [{"role": "user", "content": "hello"}]
    assert payload["thinking"] == {"type": "enabled", "budget_tokens": 2048}
