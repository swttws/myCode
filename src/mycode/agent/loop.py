from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterable
from dataclasses import replace
from pathlib import Path

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
from mycode.llm import BaseLLM, LLMError, StreamEventType
from mycode.memory import ConversationMemory
from mycode.prompt import (
    PromptBuildError,
    PromptConfigurationError,
    PromptBuilder,
    create_default_prompt_builder,
)
from mycode.prompt.models import SystemReminder
from mycode.permission.models import (
    ApprovalDecision,
    ApprovalDecisionType,
    ApprovalOutcome,
    ApprovalProvider,
    PermissionEffect,
)
from mycode.permission.service import PermissionInterceptor
from mycode.tool import ToolExecutor, ToolKind, ToolRegistry, ToolResult


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
        config: AgentConfig | None = None,
        prompt_builder: PromptBuilder | None = None,
    ) -> None:
        self._llm = llm
        self._memory = memory
        self._tool_executor = tool_executor
        self._tool_registry = tool_registry
        self.config = config or AgentConfig()
        self._permission = permission
        self._prompt_builder = prompt_builder or create_default_prompt_builder(Path.cwd(), self.config.prompt)
        self._next_turn_id = 0

    def clear_memory(self) -> None:
        # /clear 只清会话历史，不重建模型、工具注册中心或运行配置。
        self._memory.clear()

    async def run(
        self,
        user_text: str,
        *,
        mode: AgentMode,
        approval_provider: ApprovalProvider | None = None,
    ) -> AsyncIterable[AgentEvent]:
        self._memory.append(make_user_message(user_text))
        yield AgentEvent(AgentEventType.USER_MESSAGE, content=user_text)
        self._next_turn_id += 1
        # 整次 run 共用一个截止时间，模型等待和工具执行都不能越过它。
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
            for round_index in range(1, self.config.max_rounds + 1):
                assistant_parts: list[str] = []
                tool_calls = []
                # 只注入延迟工具摘要；模型通过 tool_search 发现后，完整 schema 才进入下一轮。
                deferred_reminder = _make_deferred_tool_reminder(
                    self._tool_registry.deferred_summaries()
                )
                round_turn_context = (
                    replace(
                        turn_context,
                        reminders=turn_context.reminders + (deferred_reminder,),
                    )
                    if deferred_reminder is not None
                    else turn_context
                )
                prompt_request = self._prompt_builder.build(
                    history=self._memory.messages(),
                    tools=self._tool_registry.model_definitions(),
                    turn=round_turn_context,
                    round_index=round_index,
                )
                stream = self._llm.stream_chat(
                    list(prompt_request.messages),
                    tools=list(prompt_request.tools),
                ).__aiter__()

                while True:
                    run_remaining = None
                    if run_deadline is not None:
                        run_remaining = run_deadline - time.monotonic()
                        if run_remaining <= 0:
                            yield AgentEvent(
                                AgentEventType.ERROR,
                                content="run timeout",
                                round_index=round_index,
                                error_code=AgentErrorCode.RUN_TIMEOUT,
                            )
                            return

                    wait_timeout = _minimum_timeout(self.config.model_timeout_seconds, run_remaining)
                    try:
                        # 逐个拉取模型事件，才能在长时间无输出时触发 model/run timeout。
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
                        yield AgentEvent(
                            AgentEventType.ERROR,
                            content="run timeout" if error_code == AgentErrorCode.RUN_TIMEOUT else "model timeout",
                            round_index=round_index,
                            error_code=error_code,
                        )
                        return

                    if event.type == StreamEventType.TEXT_DELTA:
                        assistant_parts.append(event.content)
                        yield AgentEvent(AgentEventType.TEXT_DELTA, content=event.content, round_index=round_index)
                    elif event.type == StreamEventType.THINKING_DELTA:
                        yield AgentEvent(AgentEventType.THINKING_DELTA, content=event.content, round_index=round_index)
                    elif event.type == StreamEventType.TOOL_CALL and event.tool_call is not None:
                        tool_calls.append(event.tool_call)
                    elif event.type == StreamEventType.ERROR:
                        yield AgentEvent(
                            AgentEventType.ERROR,
                            content=event.content,
                            round_index=round_index,
                            error_code=AgentErrorCode.LLM_ERROR,
                        )
                        return
                    elif event.type == StreamEventType.DONE:
                        if event.usage is not None:
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

                if not tool_calls:
                    assistant_text = "".join(assistant_parts)
                    if assistant_text:
                        self._memory.append(make_assistant_text_message(assistant_text))
                    yield AgentEvent(
                        AgentEventType.FINAL_RESPONSE,
                        content=assistant_text,
                        round_index=round_index,
                    )
                    return

                for call in tool_calls:
                    # 先写入 assistant tool-call 历史，下一轮模型才能看到工具请求上下文。
                    self._memory.append(make_assistant_tool_call_message(call))

                try:
                    batches = build_tool_batches(tool_calls, self._tool_registry)
                except ToolScheduleError as exc:
                    error_code = (
                        AgentErrorCode.UNKNOWN_TOOL
                        if exc.code == "unknown_tool"
                        else AgentErrorCode.INVALID_TOOL_KIND
                    )
                    yield AgentEvent(
                        AgentEventType.ERROR,
                        content=str(exc),
                        round_index=round_index,
                        error_code=error_code,
                    )
                    return

                for batch in batches:
                    for call in batch.calls:
                        # 先通知 UI 收到工具请求，只有权限检查通过后才会进入真实执行器。
                        yield AgentEvent(
                            AgentEventType.TOOL_CALL_STARTED,
                            round_index=round_index,
                            tool_call=call,
                        )

                    executable_calls = []
                    for call in batch.calls:
                        tool = self._tool_registry.get(call.name)
                        if tool is None:
                            yield AgentEvent(
                                AgentEventType.ERROR,
                                content=f"unknown tool: {call.name}",
                                round_index=round_index,
                                error_code=AgentErrorCode.UNKNOWN_TOOL,
                            )
                            return

                        permission_decision = await self._permission.before_tool(
                            call,
                            tool.definition,
                            plan_only=mode.plan_only,
                            round_index=round_index,
                        )
                        if permission_decision.effect is PermissionEffect.ALLOW:
                            executable_calls.append(call)
                        elif permission_decision.effect in {
                            PermissionEffect.DENY,
                            PermissionEffect.FORBIDDEN,
                        }:
                            result = self._permission.denied_result(call, permission_decision)
                            yield AgentEvent(
                                AgentEventType.TOOL_RESULT,
                                round_index=round_index,
                                tool_call=call,
                                tool_result=result,
                            )
                            self._memory.append(make_tool_result_message(call, result))
                        elif permission_decision.effect is PermissionEffect.ASK:
                            try:
                                approval_request = self._permission.create_approval_request(
                                    call,
                                    permission_decision,
                                    plan_only=mode.plan_only,
                                    round_index=round_index,
                                )
                            except Exception:
                                # 审批上下文异常时沿用当前 ASK 的安全拒绝结果，绝不跳过检查执行工具。
                                result = self._permission.denied_result(call, permission_decision)
                                yield AgentEvent(
                                    AgentEventType.TOOL_RESULT,
                                    round_index=round_index,
                                    tool_call=call,
                                    tool_result=result,
                                )
                                self._memory.append(make_tool_result_message(call, result))
                                continue
                            yield AgentEvent(
                                AgentEventType.APPROVAL_REQUIRED,
                                content=permission_decision.message_zh,
                                round_index=round_index,
                                tool_call=call,
                                approval_request=approval_request,
                            )
                            if approval_provider is None:
                                approval_decision = ApprovalDecision(ApprovalDecisionType.REJECT)
                            else:
                                try:
                                    approval_decision = await approval_provider(approval_request)
                                except Exception:
                                    approval_decision = ApprovalDecision(ApprovalDecisionType.REJECT)
                            resolution = await self._permission.resolve_approval(
                                approval_request,
                                approval_decision,
                            )
                            if resolution.outcome is ApprovalOutcome.EXECUTE:
                                executable_calls.append(call)
                            elif resolution.outcome in {
                                ApprovalOutcome.REJECTED,
                                ApprovalOutcome.ERROR,
                            }:
                                result = resolution.tool_result or self._permission.denied_result(
                                    call, permission_decision
                                )
                                yield AgentEvent(
                                    AgentEventType.TOOL_RESULT,
                                    round_index=round_index,
                                    tool_call=call,
                                    tool_result=result,
                                )
                                self._memory.append(make_tool_result_message(call, result))
                            elif resolution.outcome is ApprovalOutcome.CANCELLED:
                                yield AgentEvent(
                                    AgentEventType.CANCELLED,
                                    content="用户取消了工具审批。",
                                    round_index=round_index,
                                    tool_call=call,
                                    error_code=AgentErrorCode.CANCELLED,
                                )
                                return

                    try:
                        if batch.kind == ToolKind.READ and len(executable_calls) > 1:
                            # 只有连续读工具并发；写工具保持单独顺序执行，避免副作用交错。
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
                        yield AgentEvent(
                            AgentEventType.ERROR,
                            content="run timeout",
                            round_index=round_index,
                            error_code=AgentErrorCode.RUN_TIMEOUT,
                        )
                        return

                    for call, result in zip(executable_calls, results):
                        result = await self._permission.after_tool(call, result)
                        yield AgentEvent(
                            AgentEventType.TOOL_RESULT,
                            round_index=round_index,
                            tool_call=call,
                            tool_result=result,
                        )
                        self._memory.append(make_tool_result_message(call, result))

            yield AgentEvent(
                AgentEventType.ERROR,
                content=f"max rounds exceeded: {self.config.max_rounds}",
                round_index=self.config.max_rounds,
                error_code=AgentErrorCode.MAX_ROUNDS_EXCEEDED,
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


def _minimum_timeout(*values: float | None) -> float | None:
    # None 表示对应维度不限制；实际等待时间取所有有限超时里的最小值。
    finite_values = [value for value in values if value is not None]
    if not finite_values:
        return None
    return min(finite_values)


def _run_timeout_won(model_timeout: float | None, run_remaining: float | None) -> bool:
    if run_remaining is None:
        return False
    if model_timeout is None:
        return True
    return run_remaining <= model_timeout


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
