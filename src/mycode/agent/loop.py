from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterable, AsyncIterator, Sequence
from dataclasses import dataclass, field, is_dataclass, replace
from pathlib import Path
from typing import Any

from mycode.agent.config import AgentConfig
from mycode.agent.events import AgentErrorCode, AgentEvent, AgentEventType
from mycode.agent.history import (
    make_assistant_text_message,
    make_assistant_tool_call_message,
    make_tool_result_message,
    make_user_message,
)
from mycode.agent.scheduler import ToolScheduleError, build_tool_batches
from mycode.agent.state import AgentMode
from mycode.compact.models import CompactAction, CompactError, ContextTokenStatus
from mycode.hook.context import (
    build_error_hook_context,
    build_event_hook_context,
    build_message_hook_context,
)
from mycode.hook.models import HookEvent
from mycode.hook.models import HookContext
from mycode.hook.runtime import NullHookRuntime
from mycode.llm import BaseLLM, ChatMessage, LLMError, StreamEventType
from mycode.memory import ConversationMemory
from mycode.memory.models import FrameworkContext, MemoryStatusSnapshot, SessionStatusSnapshot
from mycode.prompt import (
    PromptBuildError,
    PromptConfigurationError,
    PromptBuilder,
    create_default_prompt_builder,
)
from mycode.prompt.models import PromptContextBlock, SystemReminder
from mycode.permission.models import (
    ApprovalDecision,
    ApprovalDecisionType,
    ApprovalOutcome,
    ApprovalProvider,
    PermissionEffect,
)
from mycode.permission.service import PermissionInterceptor
from mycode.tool import ToolCall, ToolExecutor, ToolKind, ToolRegistry, ToolResult


@dataclass
class _RunState:
    turn_id: int
    user_text: str
    mode: AgentMode
    approval_provider: ApprovalProvider | None
    run_deadline: float | None
    framework_context: FrameworkContext
    base_framework_blocks: tuple[Any, ...]
    turn_context: Any
    current_user_message: ChatMessage
    hook_finished: bool = False
    finished: bool = False


@dataclass(frozen=True)
class _StartRunResult:
    state: _RunState | None = None
    error_event: AgentEvent | None = None


@dataclass(frozen=True)
class _PreparedRound:
    prepared_context: Any | None = None
    events: tuple[AgentEvent, ...] = ()
    should_stop: bool = False


@dataclass
class _RoundState:
    assistant_parts: list[str] = field(default_factory=list)
    tool_calls: list[ToolCall] = field(default_factory=list)

    @property
    def assistant_text(self) -> str:
        return "".join(self.assistant_parts)


@dataclass
class _ToolPermissionOutcome:
    executable: bool = False


@dataclass(frozen=True)
class _ToolBatchExecution:
    results: tuple[ToolResult, ...] = ()
    error_event: AgentEvent | None = None


class AgentLoop:
    # Stage 03 的主循环边界：上层只消费 AgentEvent，不直接处理 LLM 事件或工具执行。
    def __init__(
        self,
        *,
        llm: BaseLLM,
        memory: ConversationMemory,
        tool_executor: ToolExecutor,
        tool_registry: ToolRegistry,
        permission: PermissionInterceptor,
        context_manager,
        config: AgentConfig | None = None,
        prompt_builder: PromptBuilder | None = None,
        project_memory: Any | None = None,
        skill_runtime: Any | None = None,
        hook_runtime: Any | None = None,
    ) -> None:
        self._llm = llm
        self._memory = memory
        self._tool_executor = tool_executor
        self._tool_registry = tool_registry
        self.config = config or AgentConfig()
        self._permission = permission
        self._prompt_builder = prompt_builder or create_default_prompt_builder(Path.cwd(), self.config.prompt)
        self._context_manager = context_manager
        self._project_memory = project_memory
        self._skill_runtime = skill_runtime
        self._hook_runtime = hook_runtime or NullHookRuntime()
        self._next_turn_id = 0
        self._latest_framework_context = _empty_framework_context()

    def clear_memory(self) -> None:
        # /clear 通过 ContextManager 同步清理历史、usage 锚点和归档会话；不重建模型、工具注册中心或运行配置。
        self._context_manager.clear()
        if self._project_memory is not None:
            self._project_memory.clear_session_state()
        self._latest_framework_context = _empty_framework_context()

    def context_token_status(self, *, mode: AgentMode) -> ContextTokenStatus:
        turn_context = self._prompt_builder.begin_turn(
            turn_id=self._next_turn_id + 1,
            plan_only=mode.plan_only,
            framework_blocks=self._framework_blocks(getattr(self._latest_framework_context, "blocks", ())),
        )

        def build_request(history):
            deferred_reminder = _make_deferred_tool_reminder(
                self._deferred_summaries()
            )
            round_turn_context = (
                _replace_turn_context(
                    turn_context,
                    reminders=turn_context.reminders + (deferred_reminder,),
                )
                if deferred_reminder is not None
                else turn_context
            )
            round_turn_context = _replace_turn_context(
                round_turn_context,
                framework_blocks=self._framework_blocks(getattr(self._latest_framework_context, "blocks", ())),
            )
            return self._prompt_builder.build(
                history=history,
                tools=self._model_definitions(),
                turn=round_turn_context,
                round_index=1,
            )

        return self._context_manager.estimate_current(build_request=build_request)

    def session_status(self) -> SessionStatusSnapshot:
        if self._project_memory is None:
            raise RuntimeError("project memory unavailable")
        return self._project_memory.session_status()

    def memory_status(self) -> MemoryStatusSnapshot:
        if self._project_memory is None:
            raise RuntimeError("project memory unavailable")
        return self._project_memory.memory_status()

    def history_snapshot(self) -> tuple[ChatMessage, ...]:
        return tuple(self._memory.messages())

    def record_external_exchange(self, user_text: str, assistant_text: str) -> None:
        user_message = make_user_message(user_text)
        assistant_message = make_assistant_text_message(assistant_text)
        self._memory.append(user_message)
        self._memory.append(assistant_message)
        if self._project_memory is not None:
            self._project_memory.record_user_message(user_message)
            self._project_memory.record_assistant_message(assistant_message)

    async def _prepare_project_memory_context(self) -> FrameworkContext:
        if self._project_memory is None:
            return _empty_framework_context()

        async def compact_prepare(restored_history: Sequence[ChatMessage]) -> tuple[ChatMessage, ...]:
            return tuple(restored_history)

        try:
            return await self._project_memory.before_user_request(compact_prepare=compact_prepare)
        except Exception:
            return _empty_framework_context()

    async def compact(
        self,
        *,
        mode: AgentMode,
    ) -> AsyncIterable[AgentEvent]:
        self._next_turn_id += 1
        run_deadline = (
            time.monotonic() + self.config.run_timeout_seconds
            if self.config.run_timeout_seconds is not None
            else None
        )

        try:
            turn_context = self._prompt_builder.begin_turn(
                turn_id=self._next_turn_id,
                plan_only=mode.plan_only,
            )

            def build_request(history):
                deferred_reminder = _make_deferred_tool_reminder(
                    self._deferred_summaries()
                )
                round_turn_context = (
                    _replace_turn_context(
                        turn_context,
                        reminders=turn_context.reminders + (deferred_reminder,),
                    )
                    if deferred_reminder is not None
                    else turn_context
                )
                round_turn_context = _replace_turn_context(
                    round_turn_context,
                    framework_blocks=self._framework_blocks(()),
                )
                return self._prompt_builder.build(
                    history=history,
                    tools=self._model_definitions(),
                    turn=round_turn_context,
                    round_index=1,
                )

            report = await self._context_manager.compact_manual(
                build_request=build_request,
                run_deadline=run_deadline,
            )
            yield AgentEvent(
                AgentEventType.COMPACTION,
                content=report.message_zh,
                compaction=report,
            )
        except CompactError as exc:
            yield AgentEvent(
                AgentEventType.ERROR,
                content=exc.report.message_zh or str(exc),
                error_code=AgentErrorCode.COMPACTION_ERROR,
                compaction=exc.report,
            )
        except (PromptBuildError, PromptConfigurationError) as exc:
            yield AgentEvent(
                AgentEventType.ERROR,
                content=str(exc),
                error_code=AgentErrorCode.PROMPT_ERROR,
            )
        except LLMError as exc:
            yield AgentEvent(
                AgentEventType.ERROR,
                content=str(exc),
                error_code=AgentErrorCode.LLM_ERROR,
            )
        except asyncio.CancelledError:
            yield AgentEvent(
                AgentEventType.CANCELLED,
                content="cancelled",
                error_code=AgentErrorCode.CANCELLED,
            )

    async def run(
        self,
        user_text: str,
        *,
        mode: AgentMode,
        approval_provider: ApprovalProvider | None = None,
        initial_skill_scope=None,
        initial_framework_blocks=(),
        isolated_depth: int = 0,
    ) -> AsyncIterable[AgentEvent]:
        started = await self._start_run(
            user_text,
            mode=mode,
            approval_provider=approval_provider,
            initial_skill_scope=initial_skill_scope,
            initial_framework_blocks=initial_framework_blocks,
            isolated_depth=isolated_depth,
        )
        if started.error_event is not None:
            yield started.error_event
            return
        state = started.state
        if state is None:
            return
        yield AgentEvent(AgentEventType.USER_MESSAGE, content=user_text)

        try:
            for round_index in range(1, self.config.max_rounds + 1):
                async for event in self._run_model_round(state, round_index):
                    yield event
                if state.finished:
                    return

            yield self._max_rounds_exceeded_event()
            self._clear_skill_current_scope()
            await self._finish_hook_request(state)
        except (PromptBuildError, PromptConfigurationError) as exc:
            yield await self._handle_run_error(state, exc)
        except LLMError as exc:
            yield await self._handle_run_error(state, exc)
        except asyncio.CancelledError:
            yield await self._handle_run_error(state, asyncio.CancelledError())

    async def _start_run(
        self,
        user_text: str,
        *,
        mode: AgentMode,
        approval_provider: ApprovalProvider | None,
        initial_skill_scope,
        initial_framework_blocks,
        isolated_depth: int,
    ) -> _StartRunResult:
        self._next_turn_id += 1
        turn_id = self._next_turn_id

        run_deadline = (
            time.monotonic() + self.config.run_timeout_seconds
            if self.config.run_timeout_seconds is not None
            else None
        )

        framework_context = await self._prepare_project_memory_context()
        if self._skill_runtime is not None:
            self._skill_runtime.refresh()
        blocking_diagnostic = _project_memory_blocking_diagnostic(framework_context)
        if blocking_diagnostic is not None:
            return _StartRunResult(
                error_event=AgentEvent(
                    AgentEventType.ERROR,
                    content=blocking_diagnostic.message,
                    error_code=AgentErrorCode.COMPACTION_ERROR,
                )
            )

        base_framework_blocks = tuple(getattr(framework_context, "blocks", ())) + tuple(initial_framework_blocks)
        turn_context = self._prompt_builder.begin_turn(
            turn_id=self._next_turn_id,
            plan_only=mode.plan_only,
            framework_blocks=self._framework_blocks(base_framework_blocks),
        )
        if self._skill_runtime is not None:
            if initial_skill_scope is not None:
                if hasattr(self._skill_runtime, "set_current_scope_object"):
                    self._skill_runtime.set_current_scope_object(initial_skill_scope)
                else:
                    self._skill_runtime.set_current_scope(initial_skill_scope.name)
            self._skill_runtime.set_current_run_context(
                history=tuple(self._memory.messages()),
                framework_blocks=turn_context.framework_blocks,
                approval_provider=approval_provider,
                isolated_depth=isolated_depth,
            )
        await self._trigger_hook(
            build_event_hook_context(
                event=HookEvent.USER_REQUEST_START,
                workspace_root=Path.cwd(),
                turn_id=turn_id,
                user_text=user_text,
                plan_only=mode.plan_only,
            )
        )
        current_user_message = make_user_message(user_text)
        self._memory.append(current_user_message)
        if self._project_memory is not None:
            self._project_memory.record_user_message(current_user_message)
        await self._trigger_hook(
            build_message_hook_context(
                event=HookEvent.USER_MESSAGE,
                workspace_root=Path.cwd(),
                message=current_user_message,
                turn_id=turn_id,
                plan_only=mode.plan_only,
            )
        )
        return _StartRunResult(
            state=_RunState(
                turn_id=turn_id,
                user_text=user_text,
                mode=mode,
                approval_provider=approval_provider,
                run_deadline=run_deadline,
                framework_context=framework_context,
                base_framework_blocks=base_framework_blocks,
                turn_context=turn_context,
                current_user_message=current_user_message,
            )
        )

    async def _finish_hook_request(self, state: _RunState) -> None:
        if state.hook_finished:
            return
        state.hook_finished = True
        await self._trigger_hook(
            build_event_hook_context(
                event=HookEvent.USER_REQUEST_END,
                workspace_root=Path.cwd(),
                turn_id=state.turn_id,
                user_text=state.user_text,
                plan_only=state.mode.plan_only,
            )
        )
        self._hook_runtime.clear_request_state()

    async def _run_model_round(
        self,
        state: _RunState,
        round_index: int,
    ) -> AsyncIterator[AgentEvent]:
        prepared = await self._prepare_round_request(state, round_index)
        for event in prepared.events:
            yield event
        if prepared.should_stop:
            state.finished = True
            return

        round_state = _RoundState()
        async for event in self._stream_model_response(
            state,
            round_index,
            prepared.prepared_context,
            round_state,
        ):
            yield event
        if state.finished:
            return

        await self._trigger_hook(
            build_event_hook_context(
                event=HookEvent.MODEL_ROUND_END,
                workspace_root=Path.cwd(),
                turn_id=state.turn_id,
                round_index=round_index,
                user_text=state.user_text,
                plan_only=state.mode.plan_only,
            )
        )

        if not round_state.tool_calls:
            async for event in self._finish_text_response(state, round_state, round_index):
                yield event
            return

        self._record_assistant_tool_calls(round_state.tool_calls)
        async for event in self._run_tool_batches(state, round_state.tool_calls, round_index):
            yield event

    async def _prepare_round_request(
        self,
        state: _RunState,
        round_index: int,
    ) -> _PreparedRound:
        await self._trigger_hook(
            build_event_hook_context(
                event=HookEvent.MODEL_ROUND_START,
                workspace_root=Path.cwd(),
                turn_id=state.turn_id,
                round_index=round_index,
                user_text=state.user_text,
                plan_only=state.mode.plan_only,
            )
        )

        def build_request(history):
            deferred_reminder = _make_deferred_tool_reminder(
                self._deferred_summaries()
            )
            round_turn_context = (
                _replace_turn_context(
                    state.turn_context,
                    reminders=state.turn_context.reminders + (deferred_reminder,),
                )
                if deferred_reminder is not None
                else state.turn_context
            )
            round_turn_context = _replace_turn_context(
                round_turn_context,
                framework_blocks=self._framework_blocks(state.base_framework_blocks),
            )
            return self._prompt_builder.build(
                history=history,
                tools=self._model_definitions(),
                turn=round_turn_context,
                round_index=round_index,
            )

        try:
            prepared_context = await self._context_manager.prepare_auto(
                build_request=build_request,
                run_deadline=state.run_deadline,
            )
        except CompactError as exc:
            return _PreparedRound(
                events=(
                    AgentEvent(
                        AgentEventType.ERROR,
                        content=exc.report.message_zh or str(exc),
                        round_index=round_index,
                        error_code=AgentErrorCode.COMPACTION_ERROR,
                        compaction=exc.report,
                    ),
                ),
                should_stop=True,
            )

        events: list[AgentEvent] = []
        if _has_compaction_action(prepared_context.report):
            events.append(
                AgentEvent(
                    AgentEventType.COMPACTION,
                    content=prepared_context.report.message_zh,
                    round_index=round_index,
                    compaction=prepared_context.report,
                )
            )
        return _PreparedRound(prepared_context=prepared_context, events=tuple(events))

    async def _stream_model_response(
        self,
        state: _RunState,
        round_index: int,
        prepared_context,
        round_state: _RoundState,
    ) -> AsyncIterator[AgentEvent]:
        prompt_request = prepared_context.request
        stream = self._llm.stream_chat(
            list(prompt_request.messages),
            tools=list(prompt_request.tools),
        ).__aiter__()

        while True:
            run_remaining = None
            if state.run_deadline is not None:
                run_remaining = state.run_deadline - time.monotonic()
                if run_remaining <= 0:
                    state.finished = True
                    yield AgentEvent(
                        AgentEventType.ERROR,
                        content="run timeout",
                        round_index=round_index,
                        error_code=AgentErrorCode.RUN_TIMEOUT,
                    )
                    return

            wait_timeout = _minimum_timeout(self.config.model_timeout_seconds, run_remaining)
            try:
                event = (
                    await asyncio.wait_for(anext(stream), timeout=wait_timeout)
                    if wait_timeout is not None
                    else await anext(stream)
                )
            except StopAsyncIteration:
                break
            except asyncio.TimeoutError:
                error_code = (
                    AgentErrorCode.RUN_TIMEOUT
                    if _run_timeout_won(self.config.model_timeout_seconds, run_remaining)
                    else AgentErrorCode.MODEL_TIMEOUT
                )
                state.finished = True
                yield AgentEvent(
                    AgentEventType.ERROR,
                    content="run timeout" if error_code == AgentErrorCode.RUN_TIMEOUT else "model timeout",
                    round_index=round_index,
                    error_code=error_code,
                )
                return

            if event.type == StreamEventType.TEXT_DELTA:
                round_state.assistant_parts.append(event.content)
                yield AgentEvent(AgentEventType.TEXT_DELTA, content=event.content, round_index=round_index)
            elif event.type == StreamEventType.THINKING_DELTA:
                yield AgentEvent(AgentEventType.THINKING_DELTA, content=event.content, round_index=round_index)
            elif event.type == StreamEventType.TOOL_CALL and event.tool_call is not None:
                round_state.tool_calls.append(event.tool_call)
            elif event.type == StreamEventType.ERROR:
                state.finished = True
                yield AgentEvent(
                    AgentEventType.ERROR,
                    content=event.content,
                    round_index=round_index,
                    error_code=AgentErrorCode.LLM_ERROR,
                )
                return
            elif event.type == StreamEventType.DONE:
                if event.usage is not None:
                    self._context_manager.record_usage(
                        prepared_context.snapshot,
                        event.usage,
                    )
                    yield AgentEvent(
                        AgentEventType.USAGE,
                        round_index=round_index,
                        usage=event.usage,
                    )
                break
        try:
            await stream.aclose()
        except AttributeError:
            pass

    async def _finish_text_response(
        self,
        state: _RunState,
        round_state: _RoundState,
        round_index: int,
    ) -> AsyncIterator[AgentEvent]:
        assistant_text = round_state.assistant_text
        final_message = make_assistant_text_message(assistant_text)
        await self._trigger_hook(
            build_message_hook_context(
                event=HookEvent.ASSISTANT_MESSAGE,
                workspace_root=Path.cwd(),
                message=final_message,
                turn_id=state.turn_id,
                round_index=round_index,
                plan_only=state.mode.plan_only,
            )
        )
        if assistant_text:
            self._memory.append(final_message)
            if self._project_memory is not None:
                self._project_memory.record_assistant_message(final_message)
        yield AgentEvent(
            AgentEventType.FINAL_RESPONSE,
            content=assistant_text,
            round_index=round_index,
        )
        if self._project_memory is not None:
            self._project_memory.after_final_response(
                user_message=state.current_user_message,
                assistant_message=final_message,
                framework_context=state.framework_context,
            )
        self._latest_framework_context = state.framework_context
        self._clear_skill_current_scope()
        await self._finish_hook_request(state)
        state.finished = True

    def _record_assistant_tool_calls(self, tool_calls: list[ToolCall]) -> None:
        for call in tool_calls:
            tool_call_message = make_assistant_tool_call_message(call)
            self._memory.append(tool_call_message)
            if self._project_memory is not None:
                self._project_memory.record_tool_history(assistant_tool_call=tool_call_message)

    async def _run_tool_batches(
        self,
        state: _RunState,
        tool_calls: list[ToolCall],
        round_index: int,
    ) -> AsyncIterator[AgentEvent]:
        try:
            batches = build_tool_batches(tool_calls, self._tool_registry)
        except ToolScheduleError as exc:
            error_code = (
                AgentErrorCode.UNKNOWN_TOOL
                if exc.code == "unknown_tool"
                else AgentErrorCode.INVALID_TOOL_KIND
            )
            state.finished = True
            yield AgentEvent(
                AgentEventType.ERROR,
                content=str(exc),
                round_index=round_index,
                error_code=error_code,
            )
            return

        for batch in batches:
            for call in batch.calls:
                yield AgentEvent(
                    AgentEventType.TOOL_CALL_STARTED,
                    round_index=round_index,
                    tool_call=call,
                )

            executable_calls: list[ToolCall] = []
            for call in batch.calls:
                outcome = _ToolPermissionOutcome()
                async for event in self._resolve_tool_call_permission(
                    state,
                    call,
                    round_index,
                    outcome,
                ):
                    yield event
                if state.finished:
                    return
                if outcome.executable:
                    executable_calls.append(call)

            executed = await self._execute_tool_batch(
                batch,
                executable_calls,
                round_index=round_index,
                run_deadline=state.run_deadline,
            )
            if executed.error_event is not None:
                state.finished = True
                yield executed.error_event
                return

            for call, result in zip(executable_calls, executed.results):
                tool = self._tool_registry.get(call.name)
                async for event in self._record_tool_result(
                    state,
                    call,
                    result,
                    round_index=round_index,
                    definition=tool.definition if tool is not None else None,
                    apply_after_tool=True,
                    trigger_after_tool=True,
                    trigger_result_message=tool is not None,
                    apply_skill_scope=True,
                ):
                    yield event

    async def _resolve_tool_call_permission(
        self,
        state: _RunState,
        call: ToolCall,
        round_index: int,
        outcome: _ToolPermissionOutcome,
    ) -> AsyncIterator[AgentEvent]:
        tool = self._tool_registry.get(call.name)
        if tool is None:
            state.finished = True
            yield AgentEvent(
                AgentEventType.ERROR,
                content=f"unknown tool: {call.name}",
                round_index=round_index,
                error_code=AgentErrorCode.UNKNOWN_TOOL,
            )
            return

        if not self._skill_allows_tool(call.name):
            state.finished = True
            yield AgentEvent(
                AgentEventType.ERROR,
                content=f"tool not allowed by active skill: {call.name}",
                round_index=round_index,
                tool_call=call,
                error_code=AgentErrorCode.TOOL_ERROR,
            )
            self._clear_skill_current_scope()
            return

        permission_decision = await self._permission.before_tool(
            call,
            tool.definition,
            plan_only=state.mode.plan_only,
            round_index=round_index,
        )
        if permission_decision.effect is PermissionEffect.ALLOW:
            hook_result = await self._hook_runtime.before_tool(
                call=call,
                definition=tool.definition,
                round_index=round_index,
                turn_id=state.turn_id,
                plan_only=state.mode.plan_only,
            )
            if hook_result.blocked_tool_result is not None:
                async for event in self._record_tool_result(
                    state,
                    call,
                    hook_result.blocked_tool_result,
                    round_index=round_index,
                    definition=tool.definition,
                    trigger_result_message=True,
                ):
                    yield event
            else:
                outcome.executable = True
        elif permission_decision.effect in {
            PermissionEffect.DENY,
            PermissionEffect.FORBIDDEN,
        }:
            result = self._permission.denied_result(call, permission_decision)
            async for event in self._record_tool_result(
                state,
                call,
                result,
                round_index=round_index,
                definition=tool.definition,
                trigger_result_message=True,
            ):
                yield event
        elif permission_decision.effect is PermissionEffect.ASK:
            try:
                approval_request = self._permission.create_approval_request(
                    call,
                    permission_decision,
                    plan_only=state.mode.plan_only,
                    round_index=round_index,
                )
            except Exception:
                result = self._permission.denied_result(call, permission_decision)
                async for event in self._record_tool_result(
                    state,
                    call,
                    result,
                    round_index=round_index,
                    definition=tool.definition,
                    trigger_result_message=False,
                ):
                    yield event
                return
            yield AgentEvent(
                AgentEventType.APPROVAL_REQUIRED,
                content=permission_decision.message_zh,
                round_index=round_index,
                tool_call=call,
                approval_request=approval_request,
            )
            if state.approval_provider is None:
                approval_decision = ApprovalDecision(ApprovalDecisionType.REJECT)
            else:
                try:
                    approval_decision = await state.approval_provider(approval_request)
                except Exception:
                    approval_decision = ApprovalDecision(ApprovalDecisionType.REJECT)
            resolution = await self._permission.resolve_approval(
                approval_request,
                approval_decision,
            )
            if resolution.outcome is ApprovalOutcome.EXECUTE:
                hook_result = await self._hook_runtime.before_tool(
                    call=call,
                    definition=tool.definition,
                    round_index=round_index,
                    turn_id=state.turn_id,
                    plan_only=state.mode.plan_only,
                )
                if hook_result.blocked_tool_result is not None:
                    async for event in self._record_tool_result(
                        state,
                        call,
                        hook_result.blocked_tool_result,
                        round_index=round_index,
                        definition=tool.definition,
                        trigger_result_message=True,
                    ):
                        yield event
                else:
                    outcome.executable = True
            elif resolution.outcome in {
                ApprovalOutcome.REJECTED,
                ApprovalOutcome.ERROR,
            }:
                result = resolution.tool_result or self._permission.denied_result(
                    call, permission_decision
                )
                async for event in self._record_tool_result(
                    state,
                    call,
                    result,
                    round_index=round_index,
                    definition=tool.definition,
                    trigger_result_message=True,
                    record_project_memory=False,
                    yield_before_memory=True,
                ):
                    yield event
            elif resolution.outcome is ApprovalOutcome.CANCELLED:
                state.finished = True
                yield AgentEvent(
                    AgentEventType.CANCELLED,
                    content="\u7528\u6237\u53d6\u6d88\u4e86\u5de5\u5177\u5ba1\u6279\u3002",
                    round_index=round_index,
                    tool_call=call,
                    error_code=AgentErrorCode.CANCELLED,
                )

    async def _execute_tool_batch(
        self,
        batch,
        executable_calls: list[ToolCall],
        *,
        round_index: int,
        run_deadline: float | None,
    ) -> _ToolBatchExecution:
        try:
            if batch.kind == ToolKind.READ and len(executable_calls) > 1:
                results = await asyncio.gather(
                    *(
                        _execute_tool_with_run_deadline(
                            self._tool_executor,
                            call,
                            run_deadline,
                        )
                        for call in executable_calls
                    )
                )
            else:
                results = [
                    await _execute_tool_with_run_deadline(
                        self._tool_executor,
                        call,
                        run_deadline,
                    )
                    for call in executable_calls
                ]
        except asyncio.TimeoutError:
            return _ToolBatchExecution(
                error_event=AgentEvent(
                    AgentEventType.ERROR,
                    content="run timeout",
                    round_index=round_index,
                    error_code=AgentErrorCode.RUN_TIMEOUT,
                )
            )
        return _ToolBatchExecution(results=tuple(results))

    async def _record_tool_result(
        self,
        state: _RunState,
        call: ToolCall,
        result: ToolResult,
        *,
        round_index: int,
        definition,
        apply_after_tool: bool = False,
        trigger_after_tool: bool = False,
        trigger_result_message: bool = False,
        record_project_memory: bool = True,
        yield_before_memory: bool = False,
        apply_skill_scope: bool = False,
    ) -> AsyncIterator[AgentEvent]:
        if apply_after_tool:
            result = await self._permission.after_tool(call, result)
        if trigger_after_tool and definition is not None:
            await self._hook_runtime.after_tool(
                call=call,
                definition=definition,
                result=result,
                round_index=round_index,
                turn_id=state.turn_id,
                plan_only=state.mode.plan_only,
            )
        if apply_skill_scope:
            self._maybe_set_skill_scope(result)
        if trigger_result_message and definition is not None:
            await self._trigger_tool_result_message(
                call,
                definition,
                result,
                round_index=round_index,
                turn_id=state.turn_id,
                plan_only=state.mode.plan_only,
            )
        if not yield_before_memory:
            result_message = make_tool_result_message(call, result)
            self._memory.append(result_message)
            if self._project_memory is not None and record_project_memory:
                self._project_memory.record_tool_history(tool_result=result_message)
        yield AgentEvent(
            AgentEventType.TOOL_RESULT,
            round_index=round_index,
            tool_call=call,
            tool_result=result,
        )
        if yield_before_memory:
            result_message = make_tool_result_message(call, result)
            self._memory.append(result_message)
            if self._project_memory is not None and record_project_memory:
                self._project_memory.record_tool_history(tool_result=result_message)

    async def _handle_run_error(
        self,
        state: _RunState,
        exc: BaseException,
    ) -> AgentEvent:
        if isinstance(exc, (PromptBuildError, PromptConfigurationError)):
            await self._trigger_hook(
                build_error_hook_context(
                    workspace_root=Path.cwd(),
                    error_code=AgentErrorCode.PROMPT_ERROR.value,
                    error_message=str(exc),
                    turn_id=state.turn_id,
                    plan_only=state.mode.plan_only,
                )
            )
            await self._finish_hook_request(state)
            return AgentEvent(
                AgentEventType.ERROR,
                content=str(exc),
                error_code=AgentErrorCode.PROMPT_ERROR,
            )
        if isinstance(exc, LLMError):
            await self._trigger_hook(
                build_error_hook_context(
                    workspace_root=Path.cwd(),
                    error_code=AgentErrorCode.LLM_ERROR.value,
                    error_message=str(exc),
                    turn_id=state.turn_id,
                    plan_only=state.mode.plan_only,
                )
            )
            await self._finish_hook_request(state)
            return AgentEvent(
                AgentEventType.ERROR,
                content=str(exc),
                error_code=AgentErrorCode.LLM_ERROR,
            )

        self._clear_skill_current_scope()
        await self._trigger_hook(
            build_error_hook_context(
                workspace_root=Path.cwd(),
                error_code=AgentErrorCode.CANCELLED.value,
                error_message="cancelled",
                turn_id=state.turn_id,
                plan_only=state.mode.plan_only,
            )
        )
        await self._finish_hook_request(state)
        return AgentEvent(
            AgentEventType.CANCELLED,
            content="cancelled",
            error_code=AgentErrorCode.CANCELLED,
        )

    def _max_rounds_exceeded_event(self) -> AgentEvent:
        return AgentEvent(
            AgentEventType.ERROR,
            content=f"max rounds exceeded: {self.config.max_rounds}",
            round_index=self.config.max_rounds,
            error_code=AgentErrorCode.MAX_ROUNDS_EXCEEDED,
        )

    def _framework_blocks(self, blocks) -> tuple[PromptContextBlock, ...]:
        converted = _convert_framework_blocks(blocks)
        skill_blocks = (
            tuple(self._skill_runtime.prompt_blocks())
            if self._skill_runtime is not None
            else ()
        )
        return converted + skill_blocks + tuple(self._hook_runtime.prompt_blocks())

    async def _trigger_hook(self, context) -> None:
        await self._hook_runtime.trigger(context)

    async def _trigger_tool_result_message(
        self,
        call,
        definition,
        result: ToolResult,
        *,
        round_index: int,
        turn_id: int,
        plan_only: bool,
    ) -> None:
        await self._trigger_hook(
            HookContext(
                event=HookEvent.TOOL_RESULT_MESSAGE,
                workspace_root=Path.cwd(),
                turn_id=turn_id,
                round_index=round_index,
                tool_call=call,
                tool_definition=definition,
                raw_arguments=call.arguments or {},
                tool_result=result,
                plan_only=plan_only,
            )
        )

    def _model_definitions(self):
        if self._skill_runtime is None:
            return self._tool_registry.model_definitions()
        return self._tool_registry.model_definitions(visible_names=self._skill_runtime.visible_tool_names())

    def _deferred_summaries(self):
        if self._skill_runtime is not None and self._skill_runtime.visible_tool_names() is not None:
            return []
        return self._tool_registry.deferred_summaries()

    def _skill_allows_tool(self, name: str) -> bool:
        if self._skill_runtime is None:
            return True
        return self._skill_runtime.allows_tool(name)

    def _maybe_set_skill_scope(self, result: ToolResult) -> None:
        if self._skill_runtime is None or not result.ok:
            return
        if result.tool_name != getattr(self._skill_runtime, "LOAD_TOOL_NAME", "load_skill"):
            return
        content = result.content
        if content.get("action") == "activated" and content.get("set_scope") is True:
            name = content.get("name")
            if isinstance(name, str):
                self._skill_runtime.set_current_scope(name)

    def _clear_skill_current_scope(self) -> None:
        if self._skill_runtime is not None:
            self._skill_runtime.clear_current_scope()


def _minimum_timeout(*values: float | None) -> float | None:
    # None 表示对应维度不限制；实际等待时间取所有有限超时里的最小值。
    finite_values = [value for value in values if value is not None]
    if not finite_values:
        return None
    return min(finite_values)


def _replace_turn_context(turn_context, **changes):
    if is_dataclass(turn_context):
        return replace(turn_context, **changes)
    return turn_context


def _run_timeout_won(model_timeout: float | None, run_remaining: float | None) -> bool:
    if run_remaining is None:
        return False
    if model_timeout is None:
        return True
    return run_remaining <= model_timeout


def _has_compaction_action(report) -> bool:
    return any(action is not CompactAction.NONE for action in report.actions)


async def _execute_tool_with_run_deadline(
    executor: ToolExecutor,
    call,
    run_deadline: float | None,
):
    # ToolExecutor 负责单工具超时；这里额外保证整次 Agent run 的截止时间。
    if run_deadline is None:
        return await executor.execute(call)
    remaining = run_deadline - time.monotonic()
    if remaining <= 0:
        raise asyncio.TimeoutError
    return await asyncio.wait_for(executor.execute(call), timeout=remaining)


def _make_deferred_tool_reminder(summaries) -> SystemReminder | None:
    if not summaries:
        return None
    lines = [
        "以下 MCP 工具可按完整名称发现；需要使用时先调用 tool_search 获取完整定义：",
        *(
            f"- {summary.name}: {' '.join(summary.description.split())}"
            for summary in summaries
        ),
    ]
    content = "\n".join(lines)
    return SystemReminder(
        id="mcp-deferred-tools",
        full_content=content,
        concise_content=content,
    )


def _convert_framework_blocks(blocks) -> tuple[PromptContextBlock, ...]:
    converted: list[PromptContextBlock] = []
    for block in blocks:
        kind = getattr(block.kind, "value", block.kind)
        converted.append(
            PromptContextBlock(
                id=block.id,
                kind=str(kind),
                priority=block.priority,
                content=block.content,
            )
        )
    return tuple(converted)


def _project_memory_blocking_diagnostic(framework_context: FrameworkContext):
    for diagnostic in getattr(framework_context, "diagnostics", ()):
        if getattr(diagnostic, "code", None) == "restore_compaction_failed":
            return diagnostic
    return None


def _empty_framework_context() -> FrameworkContext:
    return FrameworkContext(blocks=(), restored_history=(), session_summary=None, diagnostics=())
