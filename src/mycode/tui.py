from __future__ import annotations

import asyncio
import inspect
from collections.abc import Callable

from prompt_toolkit import PromptSession
from rich.console import Console

from mycode.agent import ApprovalDecision, ApprovalDecisionType, ApprovalRequest, AgentEventType
from mycode.session import ChatSession

try:
    from prompt_toolkit.output.win32 import NoConsoleScreenBufferError
except ImportError:
    class NoConsoleScreenBufferError(Exception):
        pass


class ChatTUI:
    def __init__(
        self,
        *,
        session: ChatSession,
        console: Console | None = None,
        input_func: Callable[[], str] | None = None,
        show_thinking: bool = False,
    ) -> None:
        self._session = session
        self._console = console or Console()
        self._show_thinking = show_thinking
        self._prompt_session: PromptSession | None = None
        self._input_func = input_func or self._prompt

    async def run(self) -> int:
        self._console.print("[bold cyan]myCode[/bold cyan] Stage 03 Agent 模式，输入 /exit 退出，/clear 清空上下文。")
        while True:
            try:
                user_text = await self._read_input()
            except (EOFError, KeyboardInterrupt):
                self._console.print()
                return 0

            command = user_text.strip()
            if not command:
                continue
            if command == "/exit":
                return 0
            if command == "/clear":
                self._session.clear()
                self._console.print("[dim]上下文已清空。[/dim]")
                continue
            if command == "/plan-only":
                # 只查询状态，不触发模型请求。
                state = "开启" if self._session.is_plan_only() else "关闭"
                self._console.print(f"[dim]plan-only 当前：{state}。[/dim]")
                continue
            if command == "/plan-only on":
                self._session.set_plan_only(True)
                self._console.print("[dim]plan-only 已开启。[/dim]")
                continue
            if command == "/plan-only off":
                self._session.set_plan_only(False)
                self._console.print("[dim]plan-only 已关闭。[/dim]")
                continue

            await self._render_stream(command)

    async def _read_input(self) -> str:
        value = self._input_func()
        if inspect.isawaitable(value):
            return await value
        return value

    async def _prompt(self) -> str:
        # 只有真实交互输入时才创建 PromptSession，避免测试环境缺少控制台而失败。
        if self._prompt_session is None:
            try:
                self._prompt_session = PromptSession()
            except NoConsoleScreenBufferError:
                return await self._plain_input()
        try:
            return await self._prompt_session.prompt_async("you> ")
        except NoConsoleScreenBufferError:
            self._prompt_session = None
            return await self._plain_input()

    async def _plain_input(self) -> str:
        return await asyncio.to_thread(input, "you> ")

    async def _render_stream(self, user_text: str) -> None:
        self._console.print("[bold green]assistant>[/bold green] ", end="")
        async for event in self._session.send(user_text, approval_provider=self._approval_provider):
            if event.type == AgentEventType.TEXT_DELTA:
                self._console.print(event.content, end="")
            elif event.type == AgentEventType.THINKING_DELTA and self._show_thinking:
                # thinking 用弱化样式输出，避免和最终回答混在一起。
                self._console.print(event.content, style="dim italic", end="")
            elif event.type == AgentEventType.TOOL_CALL_STARTED and event.tool_call is not None:
                self._console.print(
                    f"\n[dim]工具开始：{event.tool_call.name}[/dim]",
                    end="",
                )
            elif event.type == AgentEventType.TOOL_RESULT and event.tool_result is not None:
                if event.tool_result.ok:
                    self._console.print(
                        f"\n[dim]工具已执行：{event.tool_result.tool_name}[/dim]",
                        end="",
                    )
                else:
                    self._console.print(
                        f"\n[red]工具失败：{event.tool_result.tool_name} - {event.tool_result.error}[/red]",
                        end="",
                    )
            elif event.type == AgentEventType.ERROR:
                self._console.print(f"\n[red]错误：{event.content}[/red]")
            elif event.type == AgentEventType.CANCELLED:
                self._console.print(f"\n[yellow]已取消：{event.content}[/yellow]")
            elif event.type == AgentEventType.APPROVAL_REQUIRED and event.approval_request is not None:
                self._console.print(
                    f"\n[yellow]等待审批：{event.approval_request.tool_call.name}[/yellow]",
                    end="",
                )
        self._console.print()

    async def _approval_provider(self, request: ApprovalRequest) -> ApprovalDecision:
        # Agent 在写工具需要审批时调用这里；TUI 负责把用户输入翻译成审批决定。
        self._console.print(
            f"\n[yellow]plan-only 请求执行写工具：{request.tool_call.name}。批准？[y/n/c][/yellow]"
        )
        answer = (await self._read_input()).strip().lower()
        if answer == "y":
            return ApprovalDecision(ApprovalDecisionType.APPROVE_ONCE)
        if answer == "n":
            return ApprovalDecision(ApprovalDecisionType.REJECT)
        return ApprovalDecision(ApprovalDecisionType.CANCEL)
