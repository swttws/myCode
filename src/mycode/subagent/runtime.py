from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from pathlib import Path

from mycode.agent import AgentConfig, AgentLoop, AgentMode
from mycode.agent.events import AgentErrorCode, AgentEvent, AgentEventType
from mycode.compact.models import (
    CompactAction,
    CompactReport,
    CompactStatus,
    PreparedContext,
    RequestSnapshot,
    TokenEstimate,
)
from mycode.config import LLMConfig
from mycode.hook.runtime import NullHookRuntime
from mycode.llm import ChatMessage, MessageOrigin, UsageObservation
from mycode.memory import InMemoryConversationMemory
from mycode.prompt.models import PromptBuildMetadata, PromptBuildResult, PromptContextBlock
from mycode.subagent.context import build_defined_agent_messages, build_fork_prompt
from mycode.subagent.models import (
    AgentModelTier,
    AgentRoleDefinition,
    SubAgentConfig,
    SubAgentExecutionReport,
    SubAgentKind,
    SubAgentLaunchRequest,
    SubAgentResult,
    SubAgentTaskState,
    SubAgentUsage,
    truncate_utf8_bytes,
)
from mycode.subagent.tooling import SubAgentPermissionInterceptor, SubAgentToolPolicy
from mycode.tool import ToolRegistry


class SubAgentRuntime:
    def __init__(
        self,
        *,
        request: SubAgentLaunchRequest,
        agent_loop: AgentLoop,
        model_id: str,
        max_rounds: int,
        max_result_bytes: int,
    ) -> None:
        self._request = request
        self._agent_loop = agent_loop
        self._max_result_bytes = max_result_bytes
        self.model_id = model_id
        self.max_rounds = max_rounds

    async def run(self, cancel_event: asyncio.Event) -> SubAgentExecutionReport:
        consume_task = asyncio.create_task(self._consume_events())
        cancel_task = asyncio.create_task(cancel_event.wait())
        try:
            done, pending = await asyncio.wait(
                (consume_task, cancel_task),
                return_when=asyncio.FIRST_COMPLETED,
            )
            if cancel_task in done and not consume_task.done():
                consume_task.cancel()
            for task in pending:
                if task is not consume_task:
                    task.cancel()
            return await consume_task
        except asyncio.CancelledError:
            consume_task.cancel()
            await asyncio.gather(consume_task, return_exceptions=True)
            return _cancelled_report()
        finally:
            cancel_task.cancel()
            await asyncio.gather(cancel_task, return_exceptions=True)

    async def _consume_events(self) -> SubAgentExecutionReport:
        final_text: str | None = None
        error_event: AgentEvent | None = None
        cancelled_event: AgentEvent | None = None
        rounds = 0
        usages: list[UsageObservation] = []

        try:
            async for event in self._agent_loop.run(
                "",
                mode=AgentMode(),
                approval_provider=None,
                isolated_depth=1,
            ):
                rounds = max(rounds, event.round_index)
                if event.type is AgentEventType.USAGE and event.usage is not None:
                    usages.append(event.usage)
                elif event.type is AgentEventType.FINAL_RESPONSE:
                    final_text = event.content
                elif event.type is AgentEventType.ERROR:
                    error_event = event
                    break
                elif event.type is AgentEventType.CANCELLED:
                    cancelled_event = event
                    break
        except asyncio.CancelledError:
            return _cancelled_report(rounds=rounds, usage=SubAgentUsage.aggregate(tuple(usages)))
        except Exception as exc:
            return _failed_report(
                "runtime_error",
                _safe_error(exc),
                rounds=rounds,
                usage=SubAgentUsage.aggregate(tuple(usages)),
            )

        usage = SubAgentUsage.aggregate(tuple(usages))
        if cancelled_event is not None:
            return _cancelled_report(rounds=rounds, usage=usage)
        if error_event is not None:
            return _failed_report(
                _error_code(error_event),
                error_event.content or _error_code(error_event),
                rounds=rounds,
                usage=usage,
            )
        if final_text is None or not final_text.strip():
            return _failed_report(
                "empty_final_response",
                "子 Agent 没有返回最终文本。",
                rounds=rounds,
                usage=usage,
            )

        detail, detail_truncated = truncate_utf8_bytes(final_text, self._max_result_bytes)
        summary, summary_truncated = truncate_utf8_bytes(
            _summary_text(final_text),
            min(4096, self._max_result_bytes),
        )
        return SubAgentExecutionReport(
            state=SubAgentTaskState.COMPLETED,
            rounds=rounds,
            result=SubAgentResult(
                detail=detail,
                summary=summary,
                detail_truncated=detail_truncated,
                summary_truncated=summary_truncated,
            ),
            error_code=None,
            error_message=None,
            usage=usage,
        )


class SubAgentRuntimeFactory:
    def __init__(
        self,
        *,
        config: SubAgentConfig,
        llm_config: LLMConfig,
        llm_factory: Callable[[LLMConfig], object],
        catalog,
        parent_tool_registry: ToolRegistry,
        task_tool_registry_factory,
        permission_factory: Callable[[object], object],
        workspace_root: str | Path,
        workspace_environment: str,
        project_instructions: Sequence[str] = (),
        hook_runtime_factory: Callable[[], object] | None = None,
    ) -> None:
        self._config = config
        self._llm_config = llm_config
        self._llm_factory = llm_factory
        self._catalog = catalog
        self._parent_tool_registry = parent_tool_registry
        self._task_tool_registry_factory = task_tool_registry_factory
        self._permission_factory = permission_factory
        self._workspace_root = Path(workspace_root)
        self._workspace_environment = workspace_environment
        self._project_instructions = tuple(project_instructions)
        self._hook_runtime_factory = hook_runtime_factory or NullHookRuntime

    def create(self, request: SubAgentLaunchRequest, *, detached: bool) -> SubAgentRuntime:
        role = self._role_for(request)
        model_id = self._model_id(request, role)
        max_rounds = request.parent.max_rounds if request.kind is SubAgentKind.FORK else role.metadata.max_rounds
        task_runtime = self._task_tool_registry_factory.create(self._parent_tool_registry)
        tool_policy = SubAgentToolPolicy(
            tool_definitions=tuple(self._parent_tool_registry.definitions()),
            background_allowed_tools=self._config.background_allowed_tools,
        )
        visible_names = tool_policy.visible_names(
            request=request,
            role=role,
            detached=detached,
        )
        effective_mode = tool_policy.effective_permission_mode(
            request.parent.permission_mode,
            role,
        )
        permission = SubAgentPermissionInterceptor(
            tool_policy=tool_policy,
            request=request,
            role=role,
            detached=detached,
            permission=self._permission_factory(effective_mode),
        )
        memory = InMemoryConversationMemory()
        for message in self._initial_messages(request, role):
            memory.append(message)
        llm = self._llm_factory(replace(self._llm_config, model=model_id))
        agent_loop = AgentLoop(
            llm=llm,
            memory=memory,
            tool_executor=task_runtime.executor,
            tool_registry=task_runtime.registry,
            permission=permission,
            context_manager=_RuntimeContextManager(memory),
            config=AgentConfig(max_rounds=max_rounds),
            prompt_builder=_PassthroughPromptBuilder(),
            skill_runtime=_VisibleToolRuntime(visible_names),
            hook_runtime=self._hook_runtime_factory(),
        )
        return SubAgentRuntime(
            request=request,
            agent_loop=agent_loop,
            model_id=model_id,
            max_rounds=max_rounds,
            max_result_bytes=self._config.max_result_bytes,
        )

    def _role_for(self, request: SubAgentLaunchRequest) -> AgentRoleDefinition | None:
        if request.kind is SubAgentKind.FORK:
            return None
        if not request.role_name:
            raise RuntimeError("subagent_role_required")
        return self._catalog.get(request.role_name)

    def _model_id(
        self,
        request: SubAgentLaunchRequest,
        role: AgentRoleDefinition | None,
    ) -> str:
        if request.kind is SubAgentKind.FORK or role is None:
            return request.parent.model_id
        tier = role.metadata.model
        if tier is AgentModelTier.INHERIT:
            return request.parent.model_id
        return self._config.model_map[tier]

    def _initial_messages(
        self,
        request: SubAgentLaunchRequest,
        role: AgentRoleDefinition | None,
    ) -> tuple[ChatMessage, ...]:
        if request.kind is SubAgentKind.FORK:
            return build_fork_prompt(request.parent, task=request.task).messages
        if role is None:
            raise RuntimeError("subagent_role_required")
        return build_defined_agent_messages(
            role=role,
            task=request.task,
            workspace_environment=self._workspace_environment,
            project_instructions=self._project_instructions,
        )


@dataclass(frozen=True)
class _PassthroughTurn:
    framework_blocks: tuple[PromptContextBlock, ...]


class _PassthroughPromptBuilder:
    def begin_turn(
        self,
        *,
        turn_id: int,
        plan_only: bool,
        reminders=(),
        framework_blocks=(),
    ) -> _PassthroughTurn:
        return _PassthroughTurn(tuple(framework_blocks))

    def build(
        self,
        *,
        history,
        tools,
        turn: _PassthroughTurn,
        round_index: int,
    ) -> PromptBuildResult:
        sorted_tools = tuple(sorted(tools, key=lambda definition: definition.name))
        messages = [*history]
        framework = _render_framework_blocks(turn.framework_blocks)
        if framework is not None:
            messages.append(
                ChatMessage(
                    role="user",
                    content=framework,
                    origin=MessageOrigin.FRAMEWORK_CONTEXT,
                )
            )
        stable_text = "\n".join(message.content for message in messages if message.role == "system")
        return PromptBuildResult(
            messages=tuple(messages),
            tools=sorted_tools,
            metadata=PromptBuildMetadata(
                enabled_module_ids=("subagent-passthrough",),
                stable_prompt_sha256=hashlib.sha256(stable_text.encode("utf-8")).hexdigest(),
                diagnostics=(),
            ),
        )


class _RuntimeContextManager:
    def __init__(self, memory) -> None:
        self._memory = memory
        self._usage: list[tuple[RequestSnapshot, UsageObservation]] = []

    async def prepare_auto(self, *, build_request, run_deadline):
        request = build_request(tuple(self._memory.messages()))
        snapshot = RequestSnapshot(
            ascii_chars=0,
            non_ascii_chars=0,
            fingerprint="subagent-runtime",
        )
        return PreparedContext(
            request=request,
            snapshot=snapshot,
            estimate=TokenEstimate(tokens=0, source="subagent", delta_tokens=0),
            report=CompactReport(
                status=CompactStatus.SAFE,
                actions=(CompactAction.NONE,),
                before_tokens=0,
                after_tokens=0,
                archived_count=0,
                attempts=0,
                circuit_open=False,
            ),
        )

    def record_usage(self, snapshot, usage):
        self._usage.append((snapshot, usage))

    def clear(self):
        self._memory.clear()


class _VisibleToolRuntime:
    LOAD_TOOL_NAME = "load_skill"

    def __init__(self, visible_names: frozenset[str]) -> None:
        self._visible_names = visible_names

    def refresh(self) -> None:
        return None

    def set_current_run_context(self, **kwargs) -> None:
        return None

    def prompt_blocks(self) -> tuple[PromptContextBlock, ...]:
        return ()

    def visible_tool_names(self) -> frozenset[str]:
        return self._visible_names

    def allows_tool(self, name: str) -> bool:
        return name in self._visible_names

    def clear_current_scope(self) -> None:
        return None


def _render_framework_blocks(blocks: tuple[PromptContextBlock, ...]) -> str | None:
    if not blocks:
        return None
    lines = ["<framework-context>"]
    for block in sorted(blocks, key=lambda item: (item.priority, item.id)):
        lines.extend(
            [
                f'<block id="{block.id}" kind="{block.kind}">',
                block.content,
                "</block>",
            ]
        )
    lines.append("</framework-context>")
    return "\n".join(lines)


def _error_code(event: AgentEvent) -> str:
    if event.error_code is None:
        return "agent_error"
    if isinstance(event.error_code, AgentErrorCode):
        return event.error_code.value
    return str(event.error_code)


def _failed_report(
    code: str,
    message: str,
    *,
    rounds: int = 0,
    usage: SubAgentUsage | None = None,
) -> SubAgentExecutionReport:
    return SubAgentExecutionReport(
        state=SubAgentTaskState.FAILED,
        rounds=rounds,
        result=None,
        error_code=code,
        error_message=message,
        usage=usage or SubAgentUsage(),
    )


def _cancelled_report(
    *,
    rounds: int = 0,
    usage: SubAgentUsage | None = None,
) -> SubAgentExecutionReport:
    return SubAgentExecutionReport(
        state=SubAgentTaskState.CANCELLED,
        rounds=rounds,
        result=None,
        error_code="cancelled",
        error_message="子 Agent 任务已取消。",
        usage=usage or SubAgentUsage(),
    )


def _summary_text(text: str) -> str:
    first_line = next((line.strip() for line in text.splitlines() if line.strip()), "")
    return first_line or text


def _safe_error(exc: BaseException) -> str:
    message = str(exc)
    return message or exc.__class__.__name__
