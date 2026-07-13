from mycode.llm import ChatMessage
from mycode.memory import ConversationMemory, InMemoryConversationMemory


def test_in_memory_conversation_memory_implements_memory_abstraction():
    memory = InMemoryConversationMemory()

    assert isinstance(memory, ConversationMemory)


def test_in_memory_conversation_memory_appends_and_returns_copy():
    memory = InMemoryConversationMemory()
    memory.append(ChatMessage(role="user", content="hello"))

    messages = memory.messages()
    messages.append(ChatMessage(role="assistant", content="mutated"))

    assert memory.messages() == [ChatMessage(role="user", content="hello")]


def test_in_memory_conversation_memory_clears_messages():
    memory = InMemoryConversationMemory()
    memory.append(ChatMessage(role="user", content="hello"))

    memory.clear()

    assert memory.messages() == []
