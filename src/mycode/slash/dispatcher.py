from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from typing import Any

from mycode.slash.controller import SlashCommandController
from mycode.slash.models import (
    SlashCommandContext,
    SlashDispatchKind,
    SlashDispatchResult,
    SlashHandlerSignal,
    SlashInputKind,
)
from mycode.slash.parser import parse_slash_input
from mycode.slash.registry import SlashCommandRegistry


logger = logging.getLogger(__name__)


class SlashCommandDispatcher:
    def __init__(
        self,
        registry: SlashCommandRegistry,
        *,
        before_dispatch: Callable[[], Sequence[Any]] | None = None,
    ) -> None:
        self._registry = registry
        self._before_dispatch = before_dispatch

    @property
    def registry(self) -> SlashCommandRegistry:
        return self._registry

    async def dispatch(
        self,
        text: str,
        controller: SlashCommandController,
    ) -> SlashDispatchResult:
        parsed = parse_slash_input(text)
        if parsed.kind is SlashInputKind.EMPTY:
            return SlashDispatchResult(kind=SlashDispatchKind.EMPTY)

        if parsed.kind is SlashInputKind.NORMAL:
            return SlashDispatchResult(
                kind=SlashDispatchKind.NOT_COMMAND,
                normal_text=parsed.text,
            )

        if self._before_dispatch is not None:
            for diagnostic in self._before_dispatch():
                controller.show_message(diagnostic.message, error=True)

        command_name = parsed.command_name or ""
        command = self._registry.resolve(command_name)
        if command is None:
            # 未知斜杠输入只展示帮助引导，不能把它降级成普通对话。
            display_name = f"/{command_name}" if command_name else "/"
            logger.warning("未知斜杠命令：%s", display_name)
            controller.show_message(
                f"未找到命令 {display_name}。请输入 /help 查看可用命令。",
                error=True,
            )
            return SlashDispatchResult(kind=SlashDispatchKind.HANDLED)

        context = SlashCommandContext(controller=controller, registry=self._registry)
        try:
            signal = await command.handler(context, parsed.arguments)
        except Exception:
            # 处理函数失败后只返回稳定错误，不把异常细节变成新的对话输入。
            logger.exception("斜杠命令处理异常：%s", command.name)
            controller.show_message(
                f"slash_command_failed: {command.name}",
                error=True,
            )
            return SlashDispatchResult(kind=SlashDispatchKind.HANDLED)

        if signal is SlashHandlerSignal.EXIT:
            return SlashDispatchResult(kind=SlashDispatchKind.EXIT)
        return SlashDispatchResult(kind=SlashDispatchKind.HANDLED)
