from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence

from mycode.llm import ChatMessage


class ConversationMemory(ABC):
    # 记忆层只暴露会话语义，后续可以替换成文件或数据库实现。
    @abstractmethod
    def append(self, message: ChatMessage) -> None:
        raise NotImplementedError

    @abstractmethod
    def messages(self) -> list[ChatMessage]:
        raise NotImplementedError

    @abstractmethod
    def replace(self, messages: Sequence[ChatMessage]) -> None:
        raise NotImplementedError

    @abstractmethod
    def clear(self) -> None:
        raise NotImplementedError
