from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import AsyncIterable


class LLMError(RuntimeError):
    """统一包装协议层或模型调用错误。"""


class StreamEventType(str, Enum):
    TEXT_DELTA = "text_delta"
    THINKING_DELTA = "thinking_delta"
    DONE = "done"
    ERROR = "error"


@dataclass(frozen=True)
class ChatMessage:
    role: str
    content: str


@dataclass(frozen=True)
class StreamEvent:
    type: StreamEventType
    content: str = ""


class BaseLLM(ABC):
    # TUI 只认识这个抽象方法，不认识任何供应商自己的流式事件格式。
    @abstractmethod
    def stream_chat(self, messages: list[ChatMessage]) -> AsyncIterable[StreamEvent]:
        raise NotImplementedError
