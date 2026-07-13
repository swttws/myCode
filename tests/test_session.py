import json

from mycode.llm import BaseLLM, ChatMessage, LLMError, StreamEvent, StreamEventType
from mycode.memory import InMemoryConversationMemory
from mycode.session import ChatSession
from mycode.tool import ToolCall, ToolDefinition, ToolExecutor, ToolRegistry, ToolResult
from tests.helpers import collect_async


class ScriptedLLM(BaseLLM):
    def __init__(self, scripts):
        self.scripts = list(scripts)
        self.requests: list[list[ChatMessage]] = []
        self.tool_requests = []

    async def stream_chat(self, messages, tools=None):
        self.requests.append(list(messages))
        self.tool_requests.append(tools)
        script = self.scripts.pop(0)
        if isinstance(script, Exception):
            raise script
        for event in script:
            yield event


class EchoTool:
    @property
    def definition(self):
        return ToolDefinition(
            name="echo",
            description="Echo text.",
            parameters={"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]},
        )

    def execute(self, arguments):
        return ToolResult(ok=True, tool_name="echo", content={"text": arguments["text"]})


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


def test_chat_session_passes_tool_definitions_to_llm():
    memory = InMemoryConversationMemory()
    llm = ScriptedLLM([[StreamEvent(StreamEventType.DONE)]])
    executor = ToolExecutor(ToolRegistry([EchoTool()]))
    session = ChatSession(llm=llm, memory=memory, tool_executor=executor)

    import asyncio

    asyncio.run(collect_async(session.send("hello")))

    assert llm.tool_requests[0] == [EchoTool().definition]


def test_chat_session_executes_tool_call_once_and_stores_tool_history():
    memory = InMemoryConversationMemory()
    llm = ScriptedLLM(
        [
            [
                StreamEvent(
                    StreamEventType.TOOL_CALL,
                    tool_call=ToolCall(
                        id="call-1",
                        name="echo",
                        arguments={"text": "hi"},
                        raw_arguments='{"text":"hi"}',
                    ),
                )
            ]
        ]
    )
    executor = ToolExecutor(ToolRegistry([EchoTool()]))
    session = ChatSession(llm=llm, memory=memory, tool_executor=executor)

    import asyncio

    events = asyncio.run(collect_async(session.send("hello")))

    assert len(llm.requests) == 1
    assert events[0].type == StreamEventType.TOOL_RESULT
    assert events[0].tool_result == ToolResult(ok=True, tool_name="echo", content={"text": "hi"})
    messages = memory.messages()
    assert messages[:2] == [
        ChatMessage(role="user", content="hello"),
        ChatMessage(
            role="assistant",
            content="",
            tool_call_id="call-1",
            tool_name="echo",
            tool_arguments='{"text":"hi"}',
        ),
    ]
    assert messages[2].role == "tool"
    assert messages[2].tool_call_id == "call-1"
    assert json.loads(messages[2].content) == {
        "ok": True,
        "tool_name": "echo",
        "content": {"text": "hi"},
        "error": None,
    }


def test_chat_session_returns_error_when_tool_call_has_no_executor():
    memory = InMemoryConversationMemory()
    llm = ScriptedLLM(
        [
            [
                StreamEvent(
                    StreamEventType.TOOL_CALL,
                    tool_call=ToolCall(id="call-1", name="echo", arguments={}, raw_arguments="{}"),
                )
            ]
        ]
    )
    session = ChatSession(llm=llm, memory=memory)

    import asyncio

    events = asyncio.run(collect_async(session.send("hello")))

    assert events == [StreamEvent(StreamEventType.ERROR, "tool call received but tools are not configured")]
