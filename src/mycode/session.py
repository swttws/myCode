from __future__ import annotations

from mycode.agent import AgentLoop, AgentMode, ApprovalProvider
from mycode.permission.models import PermissionMode, RuleSource
from mycode.permission.service import PermissionService


class ChatSession:
    # Session 只保存会话模式并转发 AgentEvent，具体循环逻辑集中在 AgentLoop。
    def __init__(
        self,
        *,
        agent: AgentLoop,
        permissions: PermissionService,
        mode: AgentMode | None = None,
    ) -> None:
        self._agent = agent
        self._permissions = permissions
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

    def permission_mode(self) -> tuple[PermissionMode, RuleSource | None]:
        return self._permissions.effective_mode()

    def set_permission_mode(self, mode: PermissionMode) -> None:
        self._permissions.set_session_mode(mode)

    def clear(self) -> None:
        # 清空上下文时同步复位 plan-only，避免旧模式影响下一轮。
        self._agent.clear_memory()
        self._mode.reset()
        # 只清会话规则和档位；用户目录中的项目授权必须跨 /clear 保留。
        self._permissions.clear_session()
