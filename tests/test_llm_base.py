from mycode.llm import BaseLLM, ChatMessage, StreamEvent, StreamEventType
from tests.helpers import collect_async


class EchoLLM(BaseLLM):
    async def stream_chat(self, messages):
        yield StreamEvent(StreamEventType.TEXT_DELTA, messages[-1].content)
        yield StreamEvent(StreamEventType.DONE)


def test_concrete_llm_subclass_streams_unified_events():
    llm = EchoLLM()

    import asyncio

    events = asyncio.run(collect_async(llm.stream_chat([ChatMessage(role="user", content="hello")])))

    assert events == [
        StreamEvent(StreamEventType.TEXT_DELTA, "hello"),
        StreamEvent(StreamEventType.DONE),
    ]


def test_chat_message_accepts_user_and_assistant_roles():
    assert ChatMessage(role="user", content="hi").role == "user"
    assert ChatMessage(role="assistant", content="hello").role == "assistant"
