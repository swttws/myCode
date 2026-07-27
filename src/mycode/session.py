from __future__ import annotations

import asyncio

from mycode.agent import AgentLoop, AgentMode, ApprovalProvider
from mycode.agent.events import AgentEvent, AgentEventType, AgentErrorCode
from mycode.permission.models import PermissionMode, RuleSource
from mycode.permission.service import PermissionService
from mycode.skill.models import SkillMode, SkillRunContext


class ChatSession:
    # Session 只保存会话模式并转发 AgentEvent，具体循环逻辑集中在 AgentLoop。
    def __init__(
        self,
        *,
        agent: AgentLoop,
        permissions: PermissionService,
        mode: AgentMode | None = None,
        skill_runtime=None,
        skill_executor=None,
    ) -> None:
        self._agent = agent
        self._permissions = permissions
        self._mode = mode or AgentMode()
        self._skill_runtime = skill_runtime
        self._skill_executor = skill_executor

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

    async def send_skill(
        self,
        name: str,
        arguments: str = "",
        *,
        approval_provider: ApprovalProvider | None = None,
    ):
        if self._skill_runtime is None:
            yield AgentEvent(
                AgentEventType.ERROR,
                content="skill_runtime_unavailable",
                error_code=AgentErrorCode.TOOL_ERROR,
            )
            return
        self._skill_runtime.refresh()
        try:
            definition = self._skill_runtime.definition(name)
        except KeyError:
            yield AgentEvent(
                AgentEventType.ERROR,
                content=f"unknown skill: {name}",
                error_code=AgentErrorCode.UNKNOWN_TOOL,
            )
            return
        activation = self._skill_runtime.activate(name, arguments)
        if definition.metadata.mode is SkillMode.SHARED:
            scope = self._skill_runtime.set_current_scope(name)
            async for event in self._agent.run(
                activation.rendered_instruction,
                mode=self._mode,
                approval_provider=approval_provider,
                initial_skill_scope=scope,
            ):
                yield event
            return
        if self._skill_executor is None:
            yield AgentEvent(
                AgentEventType.ERROR,
                content="skill_executor_unavailable",
                error_code=AgentErrorCode.TOOL_ERROR,
            )
            return
        run_context = SkillRunContext(
            history=self._agent.history_snapshot(),
            framework_blocks=tuple(self._skill_runtime.prompt_blocks()),
            approval_provider=approval_provider,
            scope=None,
            isolated_depth=0,
        )
        result = await self._skill_executor.execute_isolated(
            definition,
            arguments,
            run_context=run_context,
            mode=self._mode,
        )
        user_text = f"/{name} {arguments}".rstrip()
        yield AgentEvent(AgentEventType.USER_MESSAGE, content=user_text)
        if result.ok:
            self._agent.record_external_exchange(user_text, result.summary)
            yield AgentEvent(AgentEventType.FINAL_RESPONSE, content=result.summary)
        else:
            yield AgentEvent(
                AgentEventType.ERROR,
                content=result.summary,
                error_code=AgentErrorCode.TOOL_ERROR,
            )

    async def compact(self):
        async for event in self._agent.compact(mode=self._mode):
            yield event

    async def token_status(self):
        return await asyncio.to_thread(self._agent.context_token_status, mode=self._mode)

    async def session_status(self):
        return await asyncio.to_thread(self._agent.session_status)

    async def memory_status(self):
        return await asyncio.to_thread(self._agent.memory_status)

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
        if self._skill_runtime is not None:
            self._skill_runtime.clear()
        self._mode.reset()
        # 只清会话规则和档位；用户目录中的项目授权必须跨 /clear 保留。
        self._permissions.clear_session()
