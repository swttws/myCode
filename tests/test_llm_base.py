from mycode.llm import BaseLLM, ChatMessage, StreamEvent, StreamEventType
from mycode.tool import ToolCall, ToolDefinition, ToolKind, ToolResult
from tests.helpers import collect_async


class EchoLLM(BaseLLM):
    def __init__(self):
        self.tools = None

    async def stream_chat(self, messages, tools=None):
        self.tools = tools
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


def test_stream_event_can_carry_tool_call_and_tool_result():
    call = ToolCall(id="call-1", name="read_file", arguments={"path": "README.md"})
    result = ToolResult(ok=True, tool_name="read_file", content={"text": "hello"})

    assert StreamEvent(StreamEventType.TOOL_CALL, tool_call=call).tool_call == call
    assert StreamEvent(StreamEventType.TOOL_RESULT, tool_result=result).tool_result == result


def test_chat_message_defaults_tool_history_fields_to_none():
    message = ChatMessage(role="user", content="hi")

    assert message.tool_call_id is None
    assert message.tool_name is None
    assert message.tool_arguments is None


def test_base_llm_stream_chat_accepts_optional_tool_definitions():
    llm = EchoLLM()
    tools = [
        ToolDefinition(
            name="read_file",
            description="Read file",
            parameters={"type": "object", "properties": {}, "required": []},
            kind=ToolKind.READ,
        )
    ]

    import asyncio

    asyncio.run(collect_async(llm.stream_chat([ChatMessage(role="user", content="hello")], tools=tools)))

    assert llm.tools == tools
