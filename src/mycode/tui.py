from __future__ import annotations

import inspect
from collections.abc import Callable

from prompt_toolkit import PromptSession
from rich.console import Console

from mycode.llm import StreamEventType
from mycode.session import ChatSession


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
        self._console.print("[bold cyan]myCode[/bold cyan] 纯对话模式，输入 /exit 退出，/clear 清空上下文。")
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

            await self._render_stream(command)

    async def _read_input(self) -> str:
        value = self._input_func()
        if inspect.isawaitable(value):
            return await value
        return value

    async def _prompt(self) -> str:
        # 只有真实交互输入时才创建 PromptSession，避免测试环境缺少控制台而失败。
        if self._prompt_session is None:
            self._prompt_session = PromptSession()
        return await self._prompt_session.prompt_async("you> ")

    async def _render_stream(self, user_text: str) -> None:
        self._console.print("[bold green]assistant>[/bold green] ", end="")
        async for event in self._session.send(user_text):
            if event.type == StreamEventType.TEXT_DELTA:
                self._console.print(event.content, end="")
            elif event.type == StreamEventType.THINKING_DELTA and self._show_thinking:
                # thinking 用弱化样式输出，避免和最终回答混在一起。
                self._console.print(event.content, style="dim italic", end="")
            elif event.type == StreamEventType.ERROR:
                self._console.print(f"\n[red]错误：{event.content}[/red]")
        self._console.print()
