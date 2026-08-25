from __future__ import annotations

import asyncio
import inspect
import logging
from pathlib import Path

from mycode.agent import AgentLoop, AgentMode, ApprovalProvider
from mycode.agent.events import AgentEvent, AgentEventType, AgentErrorCode
from mycode.hook.context import build_event_hook_context
from mycode.hook.models import HookEvent
from mycode.hook.runtime import NullHookRuntime
from mycode.log_context import use_log_identity
from mycode.permission.models import PermissionMode, RuleSource
from mycode.permission.service import PermissionService
from mycode.skill.models import SkillMode, SkillRunContext
from mycode.subagent.rendering import RenderEventBus, use_render_bus

logger = logging.getLogger(__name__)


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
        hook_runtime=None,
        workspace_root: Path | None = None,
        subagent_service=None,
        team_service=None,
        team_supervisor=None,
    ) -> None:
        self._agent = agent
        self._permissions = permissions
        self._mode = mode or AgentMode()
        self._skill_runtime = skill_runtime
        self._skill_executor = skill_executor
        self._hook_runtime = hook_runtime or NullHookRuntime()
        self._workspace_root = workspace_root or Path.cwd()
        self._subagent_service = subagent_service
        self._team_service = team_service
        self._team_supervisor = team_supervisor
        self._started = False
        self._closed = False

    async def send(
        self,
        user_text: str,
        *,
        approval_provider: ApprovalProvider | None = None,
    ):
        await self.start()
        if self._team_supervisor is not None and self._team_is_active():
            await self._team_supervisor.submit_user_goal(user_text)
            async for event in self._team_supervisor.events():
                yield event
                if event.type in {AgentEventType.FINAL_RESPONSE, AgentEventType.ERROR, AgentEventType.CANCELLED}:
                    break
            return
        with self._log_identity():
            async for event in self._agent.run(
                user_text,
                mode=self._mode,
                approval_provider=approval_provider,
            ):
                yield event

    async def render(
        self,
        user_text: str,
        *,
        approval_provider: ApprovalProvider | None = None,
        initial_skill_scope=None,
        initial_framework_blocks=(),
        isolated_depth: int = 0,
    ):
        await self.start()
        if self._team_supervisor is not None and self._team_is_active():
            await self._team_supervisor.submit_user_goal(user_text)
            async for event in self._team_supervisor.events():
                yield event
                if event.type in {AgentEventType.FINAL_RESPONSE, AgentEventType.ERROR, AgentEventType.CANCELLED}:
                    break
            return
        bus = RenderEventBus()
        task: asyncio.Task | None = None

        async def produce_parent() -> None:
            try:
                with self._log_identity():
                    async for event in self._agent.run(
                        user_text,
                        **self._agent_run_kwargs(
                            approval_provider=approval_provider,
                            initial_skill_scope=initial_skill_scope,
                            initial_framework_blocks=initial_framework_blocks,
                            isolated_depth=isolated_depth,
                        ),
                    ):
                        await bus.publish(event)
            finally:
                await bus.mark_producer_done()

        try:
            with use_render_bus(bus):
                task = asyncio.create_task(produce_parent())
                async for event in bus:
                    yield event
                if task is not None:
                    await task
        finally:
            if task is not None and not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    def _log_identity(self):
        role = "parent"
        team_name = None
        batch_id = None
        if self._team_service is not None:
            try:
                state = self._team_service.runtime_state()
            except Exception:
                state = None
            if state is not None:
                role = getattr(getattr(state, "role", None), "value", None) or role
                team_name = getattr(state, "team_name", None)
                batch_id = getattr(state, "batch_id", None)
        return use_log_identity(
            agent_role=role,
            team_name=team_name,
            batch_id=batch_id,
        )

    def _agent_run_kwargs(
        self,
        *,
        approval_provider: ApprovalProvider | None,
        initial_skill_scope,
        initial_framework_blocks,
        isolated_depth: int,
    ) -> dict[str, object]:
        kwargs: dict[str, object] = {
            "mode": self._mode,
            "approval_provider": approval_provider,
        }
        optional = {
            "initial_skill_scope": initial_skill_scope,
            "initial_framework_blocks": initial_framework_blocks,
            "isolated_depth": isolated_depth,
        }
        try:
            parameters = inspect.signature(self._agent.run).parameters
        except (TypeError, ValueError):
            kwargs.update(optional)
            return kwargs
        accepts_kwargs = any(
            parameter.kind is inspect.Parameter.VAR_KEYWORD
            for parameter in parameters.values()
        )
        if accepts_kwargs:
            kwargs.update(optional)
            return kwargs
        for name, value in optional.items():
            if name in parameters:
                kwargs[name] = value
        return kwargs

    async def send_skill(
        self,
        name: str,
        arguments: str = "",
        *,
        approval_provider: ApprovalProvider | None = None,
    ):
        await self.start()
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
            async for event in self.render(
                activation.rendered_instruction,
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

    async def resolve_team_request(self, request_id: str, resolution: str) -> None:
        if self._team_supervisor is None:
            raise RuntimeError("team_supervisor_unavailable")
        await self._team_supervisor.resolve_user_request(request_id, resolution)

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

    async def start(self) -> None:
        if self._started:
            return
        self._started = True
        self._closed = False
        if self._team_supervisor is not None:
            await self._team_supervisor.start()
        await self._trigger_session_hook(HookEvent.SESSION_START)

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        await self._close_subagent_service()
        await self._stop_team_supervisor()
        await self._close_team_service()
        await self._trigger_session_hook(HookEvent.SESSION_END)

    def clear(self):
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            asyncio.run(self.clear_async())
            return None
        return self.clear_async()

    async def clear_async(self) -> None:
        await self._trigger_session_hook(HookEvent.SESSION_CLEAR)
        await self._clear_subagent_service()
        await self._stop_team_supervisor()
        await self._clear_team_service()
        self._clear_state()

    async def detach_active_subagent(self):
        if self._subagent_service is None:
            return None
        return await self._subagent_service.detach_active()

    def list_subagent_tasks(self):
        if self._subagent_service is None:
            return ()
        return self._subagent_service.list_tasks()

    def get_subagent_task(self, task_id: str):
        if self._subagent_service is None:
            raise KeyError(f"subagent_service_unavailable: {task_id}")
        return self._subagent_service.get_task(task_id)

    def _clear_state(self) -> None:
        # 清空上下文时同步复位 plan-only，避免旧模式影响下一轮。
        self._agent.clear_memory()
        if self._skill_runtime is not None:
            self._skill_runtime.clear()
        self._mode.reset()
        # 只清会话规则和档位；用户目录中的项目授权必须跨 /clear 保留。
        self._permissions.clear_session()
        self._started = False
        self._closed = False

    async def _trigger_session_hook(self, event: HookEvent) -> None:
        try:
            await self._hook_runtime.trigger(
                build_event_hook_context(
                    event=event,
                    workspace_root=self._workspace_root,
                    plan_only=self._mode.plan_only,
                )
            )
        except Exception as exc:
            logger.warning(
                "Hook 会话事件异常：event=%s，reason=%s",
                event.value,
                str(exc) or exc.__class__.__name__,
            )

    async def _clear_subagent_service(self) -> None:
        if self._subagent_service is None:
            return
        try:
            await self._subagent_service.clear()
        except Exception as exc:
            logger.warning(
                "子 Agent 服务清理异常：%s",
                str(exc) or exc.__class__.__name__,
            )

    async def _close_subagent_service(self) -> None:
        if self._subagent_service is None:
            return
        try:
            await self._subagent_service.close()
        except Exception as exc:
            logger.warning(
                "子 Agent 服务关闭异常：%s",
                str(exc) or exc.__class__.__name__,
            )

    async def _clear_team_service(self) -> None:
        if self._team_service is None:
            return
        try:
            await self._team_service.clear_session()
        except Exception as exc:
            logger.warning(
                "Team service clear failed: %s",
                str(exc) or exc.__class__.__name__,
            )

    async def _stop_team_supervisor(self) -> None:
        if self._team_supervisor is None:
            return
        try:
            await self._team_supervisor.stop()
        except Exception as exc:
            logger.warning(
                "Team supervisor stop failed: %s",
                str(exc) or exc.__class__.__name__,
            )

    async def _close_team_service(self) -> None:
        if self._team_service is None:
            return
        try:
            await self._team_service.close()
        except Exception as exc:
            logger.warning(
                "Team service close failed: %s",
                str(exc) or exc.__class__.__name__,
            )

    def _team_is_active(self) -> bool:
        if self._team_service is None:
            return False
        try:
            state = self._team_service.runtime_state()
        except Exception:
            return False
        return getattr(getattr(state, "phase", None), "value", None) not in {None, "inactive"}
