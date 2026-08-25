from __future__ import annotations

import asyncio
import hashlib
import inspect
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
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
from mycode.permission.models import PermissionMode
from mycode.permission.service import PermissionInterceptor
from mycode.prompt.models import PromptBuildMetadata, PromptBuildResult, PromptContextBlock
from mycode.subagent.catalog import AgentCatalog
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
from mycode.subagent.rendering import publish_current_render_event
from mycode.subagent.tooling import (
    SubAgentPermissionInterceptor,
    SubAgentToolPolicy,
    create_task_tool_runtime,
)
from mycode.tool import ToolRegistry
from mycode.workspace import WorkspaceContext, WorkspaceKind, WorkspaceLease
from mycode.worktree.service import WorktreeService


class SubAgentRuntime:
    def __init__(
        self,
        *,
        request: SubAgentLaunchRequest,
        agent_loop: AgentLoop,
        model_id: str,
        max_rounds: int,
        max_result_bytes: int,
        task_id: str | None = None,
        workspace_lease: WorkspaceLease | None = None,
        worktree_service: WorktreeService | None = None,
        cleanup=None,
    ) -> None:
        self._request = request
        self._agent_loop = agent_loop
        self._max_result_bytes = max_result_bytes
        self._cleanup = cleanup
        self._workspace_lease = workspace_lease
        self._worktree_service = worktree_service
        self.model_id = model_id
        self.max_rounds = max_rounds
        self._task_id = task_id

    async def run(
        self,
        cancel_event: asyncio.Event,
        *,
        event_sink: Callable[[AgentEvent], object] | None = None,
    ) -> SubAgentExecutionReport:
        sink = event_sink or publish_current_render_event
        consume_task = asyncio.create_task(self._consume_events(sink))
        cancel_task = asyncio.create_task(cancel_event.wait())
        report: SubAgentExecutionReport | None = None
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
            report = await consume_task
        except asyncio.CancelledError:
            consume_task.cancel()
            await asyncio.gather(consume_task, return_exceptions=True)
            report = _cancelled_report()
        finally:
            cancel_task.cancel()
            await asyncio.gather(cancel_task, return_exceptions=True)
            if self._cleanup is not None:
                self._cleanup()
        assert report is not None
        return await self._with_workspace_disposition(report)

    async def _with_workspace_disposition(
        self,
        report: SubAgentExecutionReport,
    ) -> SubAgentExecutionReport:
        if self._workspace_lease is None or self._worktree_service is None:
            return report
        try:
            disposition = await self._worktree_service.release(self._workspace_lease)
        except Exception as exc:
            return _failed_report(
                "workspace_release_error",
                _safe_error(exc),
                rounds=report.rounds,
                usage=report.usage,
            )
        if disposition is None:
            return report
        return replace(report, disposition=disposition)

    async def _consume_events(
        self,
        event_sink: Callable[[AgentEvent], object] | None = None,
    ) -> SubAgentExecutionReport:
        state = _ConsumptionState()

        await _emit_event(
            event_sink,
            AgentEvent(
                AgentEventType.SUBAGENT_TASK_STARTED,
                content="任务开始",
                agent_type="subagent",
                role_name=_display_role_name(self._request),
                task_id=self._task_id,
            ),
        )

        try:
            async for event in self._agent_loop.run(
                "",
                mode=AgentMode(plan_only=self._request.parent.plan_only),
                approval_provider=None,
                isolated_depth=1,
            ):
                state.rounds = max(state.rounds, event.round_index)
                if await self._record_consumed_event(event_sink, state, event):
                    break
        except asyncio.CancelledError:
            return await self._cancelled_from_state(event_sink, state, content="cancelled")
        except Exception as exc:
            return await self._runtime_error_from_state(event_sink, state, exc)

        usage = state.aggregate_usage()
        if state.cancelled_event is not None:
            return await self._cancelled_from_state(
                event_sink,
                state,
                content=state.cancelled_event.content or "cancelled",
                usage=usage,
            )
        if state.error_event is not None:
            error_code = _error_code(state.error_event)
            return await self._failed_from_state(
                event_sink,
                state,
                code=error_code,
                message=state.error_event.content or error_code,
                usage=usage,
            )
        if state.final_text is None or not state.final_text.strip():
            report = _failed_report(
                "empty_final_response",
                "子 Agent 没有返回最终文本。",
                rounds=state.rounds,
                usage=usage,
            )
            await _emit_event(
                event_sink,
                AgentEvent(
                    AgentEventType.SUBAGENT_TASK_FAILED,
                    content="empty_final_response",
                    agent_type="subagent",
                    role_name=_display_role_name(self._request),
                    task_id=self._task_id,
                ),
            )
            return report
        if self._max_result_bytes > 0:
            detail, detail_truncated = truncate_utf8_bytes(state.final_text, self._max_result_bytes)
            summary, summary_truncated = truncate_utf8_bytes(
                _summary_text(state.final_text),
                min(4096, self._max_result_bytes),
            )
        else:
            detail = state.final_text
            summary = _summary_text(state.final_text)
            detail_truncated = False
            summary_truncated = False
        result = SubAgentResult(
            detail=detail,
            summary=summary,
            detail_truncated=detail_truncated,
            summary_truncated=summary_truncated,
        )

        report = SubAgentExecutionReport(
            state=SubAgentTaskState.COMPLETED,
            rounds=state.rounds,
            result=result,
            error_code=None,
            error_message=None,
            usage=usage,
        )
        await _emit_event(
            event_sink,
            AgentEvent(
                AgentEventType.SUBAGENT_TASK_COMPLETED,
                content=result.summary,
                agent_type="subagent",
                role_name=_display_role_name(self._request),
                task_id=self._task_id,
            ),
        )
        return report

    async def _record_consumed_event(
        self,
        event_sink: Callable[[AgentEvent], object] | None,
        state: _ConsumptionState,
        event: AgentEvent,
    ) -> bool:
        if event.type is AgentEventType.USAGE and event.usage is not None:
            state.usages.append(event.usage)
            return False
        if event.type is AgentEventType.FINAL_RESPONSE:
            state.final_text = event.content
            return False
        if event.type is AgentEventType.TOOL_CALL_STARTED and event.tool_call is not None:
            await _emit_event(event_sink, _child_event(event, self._request, self._task_id))
            return False
        if event.type is AgentEventType.TOOL_RESULT and event.tool_result is not None:
            await _emit_event(event_sink, _child_event(event, self._request, self._task_id))
            return False
        if event.type is AgentEventType.ERROR:
            state.error_event = event
            await _emit_event(event_sink, _child_event(event, self._request, self._task_id))
            return True
        if event.type is AgentEventType.CANCELLED:
            state.cancelled_event = event
            await _emit_event(event_sink, _child_event(event, self._request, self._task_id))
            return True
        return False

    async def _runtime_error_from_state(
        self,
        event_sink: Callable[[AgentEvent], object] | None,
        state: _ConsumptionState,
        exc: BaseException,
    ) -> SubAgentExecutionReport:
        message = _safe_error(exc)
        await _emit_event(
            event_sink,
            AgentEvent(
                AgentEventType.ERROR,
                content=message,
                agent_type="subagent",
                role_name=_display_role_name(self._request),
                task_id=self._task_id,
            ),
        )
        return _failed_report(
            "runtime_error",
            message,
            rounds=state.rounds,
            usage=state.aggregate_usage(),
        )

    async def _cancelled_from_state(
        self,
        event_sink: Callable[[AgentEvent], object] | None,
        state: _ConsumptionState,
        *,
        content: str,
        usage: SubAgentUsage | None = None,
    ) -> SubAgentExecutionReport:
        await _emit_event(
            event_sink,
            AgentEvent(
                AgentEventType.SUBAGENT_TASK_CANCELLED,
                content=content,
                agent_type="subagent",
                role_name=_display_role_name(self._request),
                task_id=self._task_id,
            ),
        )
        return _cancelled_report(rounds=state.rounds, usage=usage or state.aggregate_usage())

    async def _failed_from_state(
        self,
        event_sink: Callable[[AgentEvent], object] | None,
        state: _ConsumptionState,
        *,
        code: str,
        message: str,
        usage: SubAgentUsage | None = None,
    ) -> SubAgentExecutionReport:
        await _emit_event(
            event_sink,
            AgentEvent(
                AgentEventType.SUBAGENT_TASK_FAILED,
                content=message,
                agent_type="subagent",
                role_name=_display_role_name(self._request),
                task_id=self._task_id,
            ),
        )
        return _failed_report(
            code,
            message,
            rounds=state.rounds,
            usage=usage or state.aggregate_usage(),
        )


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


@dataclass
class _ConsumptionState:
    rounds: int = 0
    final_text: str | None = None
    error_event: AgentEvent | None = None
    cancelled_event: AgentEvent | None = None
    usages: list[UsageObservation] = field(default_factory=list)

    def aggregate_usage(self) -> SubAgentUsage:
        return SubAgentUsage.aggregate(tuple(self.usages))


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


async def _emit_event(event_sink: Callable[[AgentEvent], object] | None, event: AgentEvent) -> None:
    if event_sink is None:
        return
    result = event_sink(event)
    if inspect.isawaitable(result):
        await result


def _child_event(event: AgentEvent, request: SubAgentLaunchRequest, task_id: str | None) -> AgentEvent:
    return replace(
        event,
        agent_type="subagent",
        role_name=_display_role_name(request),
        task_id=task_id,
    )


def _display_role_name(request: SubAgentLaunchRequest) -> str:
    return request.role_name or request.kind.value


def _shared_workspace_context(workspace_root: str | Path) -> WorkspaceContext:
    root = Path(workspace_root).resolve()
    return WorkspaceContext(
        kind=WorkspaceKind.SHARED,
        root=root,
        repository_root=root,
        repository_id="subagent-shared-workspace",
        task_identity=None,
        branch_name=None,
        hooks_path=None,
    )


def resolve_subagent_role(catalog: AgentCatalog, request: SubAgentLaunchRequest) -> AgentRoleDefinition | None:
    if request.kind is SubAgentKind.FORK:
        return None
    if not request.role_name:
        raise RuntimeError("subagent_role_required")
    return catalog.get(request.role_name)


def _subagent_model_id(
    *,
    config: SubAgentConfig,
    request: SubAgentLaunchRequest,
    role: AgentRoleDefinition | None,
) -> str:
    if request.kind is SubAgentKind.FORK or role is None:
        return request.parent.model_id
    tier = role.metadata.model
    if tier is AgentModelTier.INHERIT:
        return request.parent.model_id
    return config.model_map[tier]


def _initial_messages(
    *,
    request: SubAgentLaunchRequest,
    role: AgentRoleDefinition | None,
    workspace_environment: str,
    workspace_context: WorkspaceContext | None,
    project_instructions: Sequence[str],
) -> tuple[ChatMessage, ...]:
    if request.kind is SubAgentKind.FORK:
        return build_fork_prompt(request.parent, task=request.task).messages
    if role is None:
        raise RuntimeError("subagent_role_required")
    return build_defined_agent_messages(
        role=role,
        task=request.task,
        workspace_environment=workspace_environment,
        workspace_context=workspace_context,
        project_instructions=project_instructions,
    )


def create_subagent_runtime(
    *,
    request: SubAgentLaunchRequest,
    detached: bool,
    config: SubAgentConfig,
    llm_config: LLMConfig,
    llm_factory: Callable[[LLMConfig], object],
    catalog: AgentCatalog,
    parent_tool_registry: ToolRegistry,
    permission_factory: Callable[[PermissionMode], PermissionInterceptor],
    workspace_root: str | Path,
    workspace_environment: str,
    project_instructions: Sequence[str] = (),
    hook_runtime_factory: Callable[[], object] | None = None,
    worktree_service: WorktreeService | None = None,
    task_id: str | None = None,
    workspace_lease: WorkspaceLease | None = None,
    home: str | Path | None = None,
    skill_catalog_factory: Callable[[Callable[[], frozenset[str]], Path], object] | None = None,
    mcp_pool=None,
) -> SubAgentRuntime:
    role = resolve_subagent_role(catalog, request)
    model_id = _subagent_model_id(config=config, request=request, role=role)
    max_rounds = request.parent.max_rounds if request.kind is SubAgentKind.FORK else role.metadata.max_rounds
    workspace_context = workspace_lease.context if workspace_lease is not None else _shared_workspace_context(workspace_root)
    memory = InMemoryConversationMemory()
    for message in _initial_messages(
        request=request,
        role=role,
        workspace_environment=workspace_environment,
        workspace_context=workspace_lease.context if workspace_lease is not None else None,
        project_instructions=project_instructions,
    ):
        memory.append(message)

    llm = llm_factory(replace(llm_config, model=model_id))
    agent_config = AgentConfig(max_rounds=max_rounds)
    tool_policy = SubAgentToolPolicy(
        tool_definitions=tuple(parent_tool_registry.definitions()),
        background_allowed_tools=config.background_allowed_tools,
    )
    allowed_tool_names = tool_policy.visible_names(
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
        permission=permission_factory(effective_mode),
    )
    task_runtime = create_task_tool_runtime(
        workspace=workspace_context,
        parent_registry=parent_tool_registry,
        allowed_tool_names=allowed_tool_names,
        permission=permission,
        memory=memory,
        llm=llm,
        llm_config=llm_config,
        llm_factory=llm_factory,
        agent_config=agent_config,
        hook_runtime_factory=hook_runtime_factory,
        home=home,
        skill_catalog_factory=skill_catalog_factory,
        mcp_pool=mcp_pool,
        executor_timeout_seconds=getattr(llm_config, "tool_timeout_seconds", 10.0),
    )
    agent_loop = AgentLoop(
        llm=llm,
        memory=memory,
        tool_executor=task_runtime.executor,
        tool_registry=task_runtime.registry,
        permission=permission,
        context_manager=task_runtime.context_manager or _RuntimeContextManager(memory),
        config=agent_config,
        prompt_builder=_PassthroughPromptBuilder(),
        skill_runtime=task_runtime.skill_runtime,
        hook_runtime=task_runtime.hook_runtime or NullHookRuntime(),
        workspace=workspace_context,
    )
    return SubAgentRuntime(
        request=request,
        agent_loop=agent_loop,
        model_id=model_id,
        max_rounds=max_rounds,
        max_result_bytes=config.max_result_bytes,
        task_id=task_id,
        workspace_lease=workspace_lease,
        worktree_service=worktree_service,
        cleanup=task_runtime.close,
    )
