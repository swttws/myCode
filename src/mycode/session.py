from __future__ import annotations

from mycode.llm import BaseLLM, ChatMessage, LLMError, StreamEvent, StreamEventType
from mycode.memory import ConversationMemory


class ChatSession:
    def __init__(self, *, llm: BaseLLM, memory: ConversationMemory) -> None:
        self._llm = llm
        self._memory = memory

    async def send(self, user_text: str):
        # 当前 user 消息先进入 memory，确保本轮请求能看到完整上下文。
        self._memory.append(ChatMessage(role="user", content=user_text))
        assistant_parts: list[str] = []

        try:
            async for event in self._llm.stream_chat(self._memory.messages()):
                if event.type == StreamEventType.TEXT_DELTA:
                    assistant_parts.append(event.content)
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
