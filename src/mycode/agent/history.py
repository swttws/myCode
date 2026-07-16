from __future__ import annotations

import json

from mycode.llm import ChatMessage, MessageOrigin
from mycode.tool import ToolCall, ToolResult


def make_system_message(prompt: str) -> ChatMessage:
    return ChatMessage(role="system", content=prompt, origin=MessageOrigin.SYSTEM_INSTRUCTION)


def make_user_message(text: str) -> ChatMessage:
    return ChatMessage(role="user", content=text)


def make_assistant_text_message(text: str) -> ChatMessage:
    return ChatMessage(role="assistant", content=text)


def make_assistant_tool_call_message(call: ToolCall) -> ChatMessage:
    # tool-call 历史必须保留调用 ID、工具名和参数文本，协议层才能还原供应商格式。
    return ChatMessage(
        role="assistant",
        content="",
        tool_call_id=call.id,
        tool_name=call.name,
        tool_arguments=_tool_arguments_text(call),
    )


def make_tool_result_message(call: ToolCall, result: ToolResult) -> ChatMessage:
    # tool result 统一序列化为 JSON 字符串，兼容 OpenAI Responses 和 Chat 历史转换。
    return ChatMessage(
        role="tool",
        content=serialize_tool_result(result),
        tool_call_id=call.id,
    )


def serialize_tool_result(result: ToolResult) -> str:
    return json.dumps(
        {
            "ok": result.ok,
            "tool_name": result.tool_name,
            "content": result.content,
            "error": result.error,
        },
        ensure_ascii=False,
    )


def _tool_arguments_text(call: ToolCall) -> str:
    # 优先保留模型原始参数文本，避免重新序列化改变参数格式。
    if call.raw_arguments:
        return call.raw_arguments
    return json.dumps(call.arguments or {}, ensure_ascii=False)
