from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterable

from mycode.agent.approval import ApprovalDecisionType, ApprovalProvider, ApprovalRequest
from mycode.agent.config import AgentConfig
from mycode.agent.events import AgentErrorCode, AgentEvent, AgentEventType
from mycode.agent.history import (
    make_assistant_text_message,
    make_assistant_tool_call_message,
    make_system_message,
    make_tool_result_message,
    make_user_message,
)
from mycode.agent.interceptor import InterceptDecisionType, PlanOnlyInterceptor, ToolInterceptor
from mycode.agent.scheduler import ToolScheduleError, build_tool_batches
from mycode.agent.state import AgentMode
from mycode.llm import BaseLLM, LLMError, StreamEventType
from mycode.memory import ConversationMemory
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
        config: AgentConfig | None = None,
        interceptor: ToolInterceptor | None = None,
    ) -> None:
        self._llm = llm
        self._memory = memory
        self._tool_executor = tool_executor
        self._tool_registry = tool_registry
        self.config = config or AgentConfig()
        self._interceptor = interceptor or PlanOnlyInterceptor()

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
        # 整次 run 共用一个截止时间，模型等待和工具执行都不能越过它。
        run_deadline = (
            time.monotonic() + self.config.run_timeout_seconds
            if self.config.run_timeout_seconds is not None
            else None
        )

        try:
            for round_index in range(1, self.config.max_rounds + 1):
                assistant_parts: list[str] = []
                tool_calls = []
                # system prompt 每轮临时注入，不写入普通会话 memory。
                messages = [make_system_message(self.config.minimal_system_prompt), *self._memory.messages()]
                stream = self._llm.stream_chat(messages, tools=self._tool_executor.definitions()).__aiter__()

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
                        # 先通知 UI 工具已开始，结果稍后单独通过 TOOL_RESULT 事件返回。
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

                        intercept_decision = await self._interceptor.before_tool(
                            call,
                            tool.definition,
                            mode,
                            round_index,
                        )
                        if intercept_decision.type == InterceptDecisionType.ALLOW:
                            executable_calls.append(call)
                        elif intercept_decision.type == InterceptDecisionType.DENY:
                            # 拦截器拒绝也回填结构化结果，让模型能读取失败原因继续决策。
                            result = intercept_decision.result or ToolResult(
                                ok=False,
                                tool_name=call.name,
                                content={"tool_call_id": call.id, "denied": True},
                                error=intercept_decision.reason or "tool denied",
                            )
                            yield AgentEvent(
                                AgentEventType.TOOL_RESULT,
                                round_index=round_index,
                                tool_call=call,
                                tool_result=result,
                            )
                            self._memory.append(make_tool_result_message(call, result))
                        elif intercept_decision.type == InterceptDecisionType.REQUIRE_APPROVAL:
                            if approval_provider is None:
                                yield AgentEvent(
                                    AgentEventType.ERROR,
                                    content=intercept_decision.reason or "approval required",
                                    round_index=round_index,
                                    tool_call=call,
                                    error_code=AgentErrorCode.APPROVAL_CANCELLED,
                                )
                                return

                            approval_request = ApprovalRequest(
                                id=f"approval-{call.id}",
                                tool_call=call,
                                reason=intercept_decision.reason,
                                plan_only=mode.plan_only,
                                round_index=round_index,
                            )
                            yield AgentEvent(
                                AgentEventType.APPROVAL_REQUIRED,
                                content=intercept_decision.reason,
                                round_index=round_index,
                                tool_call=call,
                                approval_request=approval_request,
                            )
                            approval_decision = await approval_provider(approval_request)
                            if approval_decision.type == ApprovalDecisionType.APPROVE_ONCE:
                                # 批准只放行当前工具一次，不改变会话的 plan-only 状态。
                                executable_calls.append(call)
                            elif approval_decision.type == ApprovalDecisionType.REJECT:
                                result = ToolResult(
                                    ok=False,
                                    tool_name=call.name,
                                    content={"tool_call_id": call.id, "rejected": True},
                                    error="tool rejected by user",
                                )
                                yield AgentEvent(
                                    AgentEventType.TOOL_RESULT,
                                    round_index=round_index,
                                    tool_call=call,
                                    tool_result=result,
                                )
                                self._memory.append(make_tool_result_message(call, result))
                            elif approval_decision.type == ApprovalDecisionType.CANCEL:
                                yield AgentEvent(
                                    AgentEventType.CANCELLED,
                                    content="approval cancelled",
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
                        result = await self._interceptor.after_tool(call, result, mode, round_index)
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
