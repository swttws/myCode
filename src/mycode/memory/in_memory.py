from __future__ import annotations

from collections.abc import Sequence

from mycode.llm import ChatMessage
from mycode.memory.base import ConversationMemory


class InMemoryConversationMemory(ConversationMemory):
    def __init__(self) -> None:
        self._messages: list[ChatMessage] = []

    def append(self, message: ChatMessage) -> None:
        self._messages.append(message)

    def messages(self) -> list[ChatMessage]:
        # 返回副本，避免 TUI 或测试代码意外修改内部历史。
        return list(self._messages)

    def replace(self, messages: Sequence[ChatMessage]) -> None:
        self._messages = list(messages)

    def clear(self) -> None:
        self._messages.clear()
