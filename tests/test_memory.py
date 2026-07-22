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
    memory.replace([ChatMessage(role="user", content="hello")])
    memory.append(ChatMessage(role="assistant", content="hi"))

    memory.clear()

    assert memory.messages() == []


def test_in_memory_conversation_memory_replaces_complete_history():
    memory = InMemoryConversationMemory()
    memory.append(ChatMessage(role="user", content="old message"))
    replacement = [
        ChatMessage(role="user", content="new question"),
        ChatMessage(role="assistant", content="new response"),
    ]

    memory.replace(replacement)

    assert memory.messages() == replacement


def test_in_memory_conversation_memory_replace_copies_input_sequence():
    memory = InMemoryConversationMemory()
    replacement = [ChatMessage(role="user", content="hello")]

    memory.replace(replacement)
    replacement.append(ChatMessage(role="assistant", content="mutated"))

    assert memory.messages() == [ChatMessage(role="user", content="hello")]
