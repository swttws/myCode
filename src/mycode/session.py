from __future__ import annotations

import json

from mycode.llm import BaseLLM, ChatMessage, LLMError, StreamEvent, StreamEventType
from mycode.memory import ConversationMemory
from mycode.tool import ToolExecutor, ToolResult


class ChatSession:
    def __init__(
        self,
        *,
        llm: BaseLLM,
        memory: ConversationMemory,
        tool_executor: ToolExecutor | None = None,
    ) -> None:
        self._llm = llm
        self._memory = memory
        self._tool_executor = tool_executor

    async def send(self, user_text: str):
        # 当前 user 消息先进入 memory，确保本轮请求能看到完整上下文。
        self._memory.append(ChatMessage(role="user", content=user_text))
        assistant_parts: list[str] = []

        try:
            stream = (
                self._llm.stream_chat(self._memory.messages(), tools=self._tool_executor.definitions())
                if self._tool_executor is not None
                else self._llm.stream_chat(self._memory.messages())
            )
            async for event in stream:
                if event.type == StreamEventType.TEXT_DELTA:
                    assistant_parts.append(event.content)
                elif event.type == StreamEventType.TOOL_CALL:
                    if event.tool_call is None:
                        yield StreamEvent(StreamEventType.ERROR, "tool call event is missing tool_call")
                        return
                    if self._tool_executor is None:
                        yield StreamEvent(
                            StreamEventType.ERROR,
                            "tool call received but tools are not configured",
                        )
                        return

                    self._memory.append(
                        ChatMessage(
                            role="assistant",
                            content="",
                            tool_call_id=event.tool_call.id,
                            tool_name=event.tool_call.name,
                            tool_arguments=event.tool_call.raw_arguments,
                        )
                    )
                    tool_result = await self._tool_executor.execute(event.tool_call)
                    yield StreamEvent(StreamEventType.TOOL_RESULT, tool_result=tool_result)
                    self._memory.append(
                        ChatMessage(
                            role="tool",
                            content=_serialize_tool_result(tool_result),
                            tool_call_id=event.tool_call.id,
                        )
                    )
                    return
                # thinking 是模型内部推理展示，不属于普通 assistant 回复历史。
                yield event
        except LLMError as exc:
            yield StreamEvent(StreamEventType.ERROR, str(exc))
            return

        assistant_text = "".join(assistant_parts)
        if assistant_text:
            self._memory.append(ChatMessage(role="assistant", content=assistant_text))

    def clear(self) -> None:
        self._memory.clear()


def _serialize_tool_result(result: ToolResult) -> str:
    return json.dumps(
        {
            "ok": result.ok,
            "tool_name": result.tool_name,
            "content": result.content,
            "error": result.error,
        },
        ensure_ascii=False,
    )
