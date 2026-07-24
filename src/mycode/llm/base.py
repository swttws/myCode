from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import AsyncIterable

from mycode.tool import ToolCall, ToolDefinition, ToolResult


class LLMError(RuntimeError):
    """统一包装协议层或模型调用错误。"""


class StreamEventType(str, Enum):
    TEXT_DELTA = "text_delta"
    THINKING_DELTA = "thinking_delta"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    DONE = "done"
    ERROR = "error"


class MessageOrigin(str, Enum):
    CONVERSATION = "conversation"
    SYSTEM_INSTRUCTION = "system_instruction"
    SYSTEM_REMINDER = "system_reminder"
    ENVIRONMENT_CONTEXT = "environment_context"
    FRAMEWORK_CONTEXT = "framework_context"
    COMPACT_PREVIEW = "compact_preview"
    COMPACT_SUMMARY = "compact_summary"
    COMPACT_BOUNDARY = "compact_boundary"


@dataclass(frozen=True)
class ChatMessage:
    role: str
    content: str
    tool_call_id: str | None = None
    tool_name: str | None = None
    tool_arguments: str | None = None
    # 来源仅供内部构建和测试区分，协议层不能把它序列化给供应商。
    origin: MessageOrigin = MessageOrigin.CONVERSATION


@dataclass(frozen=True)
class UsageObservation:
    provider: str
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    cache_read_tokens: int | None = None
    cache_write_tokens: int | None = None
    request_id: str | None = None


@dataclass(frozen=True)
class StreamEvent:
    type: StreamEventType
    content: str = ""
    tool_call: ToolCall | None = None
    tool_result: ToolResult | None = None
    usage: UsageObservation | None = None


class BaseLLM(ABC):
    # TUI 只认识这个抽象方法，不认识任何供应商自己的流式事件格式。
    @abstractmethod
    def stream_chat(
        self,
        messages: list[ChatMessage],
        tools: list[ToolDefinition] | None = None,
    ) -> AsyncIterable[StreamEvent]:
        raise NotImplementedError
