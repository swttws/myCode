from mycode.llm import BaseLLM, ChatMessage, LLMError, StreamEvent, StreamEventType
from mycode.memory import InMemoryConversationMemory
from mycode.session import ChatSession
from tests.helpers import collect_async


class ScriptedLLM(BaseLLM):
    def __init__(self, scripts):
        self.scripts = list(scripts)
        self.requests: list[list[ChatMessage]] = []

    async def stream_chat(self, messages):
        self.requests.append(list(messages))
        script = self.scripts.pop(0)
        if isinstance(script, Exception):
            raise script
        for event in script:
            yield event


def test_chat_session_appends_user_and_assistant_after_success():
    memory = InMemoryConversationMemory()
    llm = ScriptedLLM(
        [
            [
                StreamEvent(StreamEventType.TEXT_DELTA, "hi"),
                StreamEvent(StreamEventType.TEXT_DELTA, " there"),
                StreamEvent(StreamEventType.DONE),
            ]
        ]
    )
    session = ChatSession(llm=llm, memory=memory)

    import asyncio

    events = asyncio.run(collect_async(session.send("hello")))

    assert events == [
        StreamEvent(StreamEventType.TEXT_DELTA, "hi"),
        StreamEvent(StreamEventType.TEXT_DELTA, " there"),
        StreamEvent(StreamEventType.DONE),
    ]
    assert llm.requests[0] == [ChatMessage(role="user", content="hello")]
    assert memory.messages() == [
        ChatMessage(role="user", content="hello"),
        ChatMessage(role="assistant", content="hi there"),
    ]


def test_chat_session_sends_previous_turns_in_next_request():
    memory = InMemoryConversationMemory()
    llm = ScriptedLLM(
        [
            [StreamEvent(StreamEventType.TEXT_DELTA, "hi"), StreamEvent(StreamEventType.DONE)],
            [StreamEvent(StreamEventType.TEXT_DELTA, "again"), StreamEvent(StreamEventType.DONE)],
        ]
    )
    session = ChatSession(llm=llm, memory=memory)

    import asyncio

    asyncio.run(collect_async(session.send("hello")))
    asyncio.run(collect_async(session.send("second")))

    assert llm.requests[1] == [
        ChatMessage(role="user", content="hello"),
        ChatMessage(role="assistant", content="hi"),
        ChatMessage(role="user", content="second"),
    ]


def test_chat_session_does_not_store_thinking_as_assistant_text():
    memory = InMemoryConversationMemory()
    llm = ScriptedLLM(
        [
            [
                StreamEvent(StreamEventType.THINKING_DELTA, "hidden"),
                StreamEvent(StreamEventType.TEXT_DELTA, "visible"),
                StreamEvent(StreamEventType.DONE),
            ]
        ]
    )
    session = ChatSession(llm=llm, memory=memory)

    import asyncio

    asyncio.run(collect_async(session.send("hello")))

    assert memory.messages() == [
        ChatMessage(role="user", content="hello"),
        ChatMessage(role="assistant", content="visible"),
    ]


def test_chat_session_converts_llm_exception_to_error_event_without_assistant_reply():
    memory = InMemoryConversationMemory()
    llm = ScriptedLLM([LLMError("network failed")])
    session = ChatSession(llm=llm, memory=memory)

    import asyncio

    events = asyncio.run(collect_async(session.send("hello")))

    assert events == [StreamEvent(StreamEventType.ERROR, "network failed")]
    assert memory.messages() == [ChatMessage(role="user", content="hello")]
