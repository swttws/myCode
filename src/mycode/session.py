from __future__ import annotations

from mycode.agent import AgentLoop, AgentMode, ApprovalProvider


class ChatSession:
    # Session 只保存会话模式并转发 AgentEvent，具体循环逻辑集中在 AgentLoop。
    def __init__(
        self,
        *,
        agent: AgentLoop,
        mode: AgentMode | None = None,
    ) -> None:
        self._agent = agent
        self._mode = mode or AgentMode()

    async def send(
        self,
        user_text: str,
        *,
        approval_provider: ApprovalProvider | None = None,
    ):
        async for event in self._agent.run(
            user_text,
            mode=self._mode,
            approval_provider=approval_provider,
        ):
            yield event

    def set_plan_only(self, enabled: bool) -> None:
        self._mode.plan_only = enabled

    def is_plan_only(self) -> bool:
        return self._mode.plan_only

    def clear(self) -> None:
        # 清空上下文时同步复位 plan-only，避免旧模式影响下一轮。
        self._agent.clear_memory()
        self._mode.reset()
