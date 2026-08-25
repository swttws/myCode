from __future__ import annotations

import asyncio
import inspect
import json
import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from prompt_toolkit import PromptSession
from prompt_toolkit.shortcuts import CompleteStyle
from rich.console import Console

from mycode.agent import ApprovalDecision, ApprovalDecisionType, ApprovalRequest, AgentEventType
from mycode.compact.models import CompactAction, CompactStatus
from mycode.permission.models import PermissionMode, RuleSource
from mycode.session import ChatSession
from mycode.slash.builtins import create_default_slash_registry
from mycode.slash.completion import SlashCommandCompleter
from mycode.slash.dispatcher import SlashCommandDispatcher
from mycode.slash.models import (
    ApplicationStatusSnapshot,
    PermissionStatusSnapshot,
    SlashDispatchKind,
    SlashMode,
    StatusSection,
)
from mycode.slash.status import collect_git_status, collect_mcp_status
from mycode.subagent.rendering import render_event_message, render_multiline, render_prefix

try:
    from prompt_toolkit.output.win32 import NoConsoleScreenBufferError
except ImportError:  # pragma: no cover - Windows-only fallback
    class NoConsoleScreenBufferError(Exception):
        pass


logger = logging.getLogger(__name__)


@runtime_checkable
class _RenderableSession(Protocol):
    def render(self, text: str, *, approval_provider):
        raise NotImplementedError


class ChatTUI:
    def __init__(
        self,
        *,
        session: ChatSession,
        dispatcher: SlashCommandDispatcher | None = None,
        registry=None,
        completer: SlashCommandCompleter | None = None,
        mcp_pool: Any | None = None,
        workspace_root: str | Path | None = None,
        console: Console | None = None,
        input_func: Callable[[], str] | None = None,
        show_thinking: bool = False,
    ) -> None:
        self._session = session
        self._console = console or Console()
        self._show_thinking = show_thinking
        self._workspace_root = Path(workspace_root) if workspace_root is not None else Path.cwd()
        self._mcp_pool = mcp_pool

        self._registry = registry
        self._dispatcher = dispatcher
        if self._registry is None and self._dispatcher is not None:
            self._registry = self._dispatcher.registry
        if self._registry is None:
            self._registry = create_default_slash_registry()
        if self._dispatcher is None:
            self._dispatcher = SlashCommandDispatcher(self._registry)
        self._completer = completer or SlashCommandCompleter(self._registry)

        self._prompt_session: PromptSession | None = None
        self._input_func = input_func or self._prompt

    async def run(self) -> int:
        self._console.print("myCode 已就绪，输入 /help 查看可用命令。", style="cyan", markup=False)
        while True:
            try:
                user_text = await self._read_input()
            except (EOFError, KeyboardInterrupt):
                self._console.print()
                await self._close_session()
                return 0

            result = await self._dispatcher.dispatch(user_text, self)
            if result.kind is SlashDispatchKind.EMPTY:
                continue
            if result.kind is SlashDispatchKind.NOT_COMMAND:
                await self.send_user_message(result.normal_text)
                continue
            if result.kind is SlashDispatchKind.EXIT:
                await self._close_session()
                return 0

    def show_message(self, text: str, *, error: bool = False) -> None:
        self._console.print(text, markup=False, style="red" if error else None)

    async def send_user_message(self, text: str) -> None:
        await self._render_stream(self._session_stream(text))

    async def execute_skill(self, name: str, arguments: str) -> None:
        await self._render_stream(
            self._session.send_skill(name, arguments, approval_provider=self._approval_provider)
        )

    async def compact_context(self) -> None:
        await self._render_compaction_stream()

    def clear_session(self):
        return self._session.clear()

    def current_mode(self) -> SlashMode:
        return SlashMode.PLAN if self._session.is_plan_only() else SlashMode.DEFAULT

    def set_mode(self, mode: SlashMode) -> None:
        self._session.set_plan_only(mode is SlashMode.PLAN)

    def permission_status(self) -> PermissionStatusSnapshot:
        mode, source = self._session.permission_mode()
        return PermissionStatusSnapshot(mode=mode, source=source)

    def set_permission_mode(self, mode: PermissionMode) -> None:
        self._session.set_permission_mode(mode)

    async def token_status(self):
        return await self._session.token_status()

    async def session_status(self):
        return await self._session.session_status()

    async def memory_status(self):
        return await self._session.memory_status()

    def _session_stream(self, text: str):
        if isinstance(self._session, _RenderableSession):
            return self._session.render(text, approval_provider=self._approval_provider)
        return self._session.send(text, approval_provider=self._approval_provider)

    async def detach_active_subagent(self):
        result = await self._session.detach_active_subagent()
        if result is None:
            return None
        self._console.print(
            f"\n子 Agent 已转入后台：{result.id}",
            markup=False,
        )
        return result

    def list_subagent_tasks(self):
        return self._session.list_subagent_tasks()

    def get_subagent_task(self, task_id: str):
        return self._session.get_subagent_task(task_id)

    async def application_status(self) -> ApplicationStatusSnapshot:
        try:
            permission = self.permission_status()
            permission_section = StatusSection(value=permission)
        except Exception:
            logger.exception("slash permission status unavailable")
            permission_section = StatusSection(value=None, error="permission_status_unavailable")

        token_section, session_section, memory_section, git_section, mcp_section = await asyncio.gather(
            self._safe_async_status(self.token_status, "token_status_unavailable"),
            self._safe_async_status(self.session_status, "session_status_unavailable"),
            self._safe_async_status(self.memory_status, "memory_status_unavailable"),
            self._collect_git_status(),
            self._collect_mcp_status(),
        )
        return ApplicationStatusSnapshot(
            workspace_root=str(self._workspace_root),
            mode=self.current_mode(),
            permission=permission_section,
            token=token_section,
            session=session_section,
            memory=memory_section,
            git=git_section,
            mcp=mcp_section,
        )

    async def _safe_async_status(self, getter, error: str):
        try:
            value = await getter()
        except Exception:
            logger.exception("slash status unavailable: %s", error)
            return StatusSection(value=None, error=error)
        return StatusSection(value=value)

    async def _collect_git_status(self):
        try:
            return await asyncio.to_thread(collect_git_status, self._workspace_root)
        except Exception:
            logger.exception("slash git status unavailable")
            return StatusSection(value=None, error="git_status_unavailable")

    async def _collect_mcp_status(self):
        try:
            return await asyncio.to_thread(collect_mcp_status, self._mcp_pool)
        except Exception:
            logger.exception("slash mcp status unavailable")
            return StatusSection(value=None, error="mcp_status_unavailable")

    async def _read_input(self) -> str:
        value = self._input_func()
        if inspect.isawaitable(value):
            return await value
        return value

    async def _prompt(self) -> str:
        if self._prompt_session is None:
            try:
                self._prompt_session = PromptSession(
                    completer=self._completer,
                    complete_while_typing=False,
                    complete_style=CompleteStyle.COLUMN,
                    bottom_toolbar=self._mode_toolbar,
                )
            except NoConsoleScreenBufferError:
                return await self._plain_input()
        try:
            return await self._prompt_session.prompt_async(self._prompt_label())
        except NoConsoleScreenBufferError:
            self._prompt_session = None
            return await self._plain_input()

    async def _plain_input(self) -> str:
        return await asyncio.to_thread(input, self._prompt_label())

    def _mode_toolbar(self) -> str:
        return f"[{self.current_mode().value.upper()}]"

    def _prompt_label(self) -> str:
        return f"{self._mode_toolbar()} you> "

    async def _render_stream(self, events) -> None:
        assistant_open = False
        async for event in events:
            if event.type == AgentEventType.USER_MESSAGE:
                continue
            if event.type == AgentEventType.TEXT_DELTA:
                if not assistant_open:
                    self._console.print(f"{render_prefix(event)} assistant> ", end="", markup=False)
                    assistant_open = True
                self._console.print(event.content, end="", markup=False)
                continue
            if event.type == AgentEventType.THINKING_DELTA and self._show_thinking:
                if not assistant_open:
                    self._console.print(f"{render_prefix(event)} assistant> ", end="", markup=False)
                    assistant_open = True
                self._console.print(event.content, style="dim italic", end="", markup=False)
                continue
            if event.type == AgentEventType.FINAL_RESPONSE:
                if not assistant_open:
                    self._print_prefixed_message(event, f"assistant> {event.content}")
                    assistant_open = True
                continue

            if event.type == AgentEventType.APPROVAL_REQUIRED and event.approval_request is not None:
                self._print_prefixed_message(
                    event,
                    f"等待审批：{event.approval_request.tool_call.name}",
                    style="yellow",
                )
                continue
            if event.type == AgentEventType.COMPACTION:
                self._print_prefixed_message(
                    event,
                    _format_compaction(event.compaction, event.content),
                    style="dim",
                )
                continue

            message = render_event_message(event)
            if message is None:
                continue

            style = None
            if event.type in {AgentEventType.ERROR, AgentEventType.SUBAGENT_TASK_FAILED}:
                style = "red"
            elif event.type in {AgentEventType.CANCELLED, AgentEventType.SUBAGENT_TASK_CANCELLED}:
                style = "yellow"
            elif event.type in {
                AgentEventType.TOOL_CALL_STARTED,
                AgentEventType.TOOL_RESULT,
                AgentEventType.SUBAGENT_TASK_QUEUED,
                AgentEventType.SUBAGENT_TASK_STARTED,
                AgentEventType.SUBAGENT_TASK_DETACHED,
                AgentEventType.SUBAGENT_TASK_COMPLETED,
            }:
                style = "dim"
            self._print_prefixed_message(event, message, style=style)
        self._console.print()

    async def _render_compaction_stream(self) -> None:
        async for event in self._session.compact():
            if event.type == AgentEventType.COMPACTION:
                self._print_prefixed_message(
                    event,
                    _format_compaction(event.compaction, event.content),
                    style="dim",
                )
            elif event.type == AgentEventType.ERROR:
                self._print_prefixed_message(event, event.content, style="red")
            elif event.type == AgentEventType.CANCELLED:
                self._print_prefixed_message(event, event.content, style="yellow")
        self._console.print()

    def _print_prefixed_message(self, event, message: str, *, style: str | None = None) -> None:
        prefix = render_prefix(event)
        for line in render_multiline(prefix, message):
            self._console.print(line, markup=False, style=style)

    async def _close_session(self) -> None:
        try:
            await self._session.close()
        except Exception:
            logger.exception("session close failed")

    async def _approval_provider(self, request: ApprovalRequest) -> ApprovalDecision:
        decision = request.decision
        arguments = json.dumps(dict(decision.display_arguments), ensure_ascii=False, sort_keys=True)
        source = _source_label(decision.source)
        rule = f"，规则：{decision.rule_id}" if decision.rule_id else ""
        self._console.print(f"\n[yellow]工具调用：{request.tool_call.name}[/yellow]")
        self._console.print(f"[yellow]参数：{arguments}[/yellow]")
        self._console.print(f"[yellow]原因：{decision.message_zh}[/yellow]")
        self._console.print(
            f"[yellow]档位：{_mode_label(decision.mode)}；来源：{source}{rule}[/yellow]"
        )
        option_text = []
        if ApprovalDecisionType.APPROVE_ONCE in request.options:
            option_text.append("o/y 本次允许")
        if ApprovalDecisionType.APPROVE_SESSION in request.options:
            option_text.append("s 本会话允许")
        if ApprovalDecisionType.APPROVE_PROJECT in request.options:
            option_text.append("p 当前项目永久允许")
        if ApprovalDecisionType.REJECT in request.options:
            option_text.append("n 拒绝")
        if ApprovalDecisionType.CANCEL in request.options:
            option_text.append("c 取消")
        self._console.print("[yellow]请选择：" + "；".join(option_text) + "[/yellow]")
        answer = (await self._read_input()).strip().lower()
        mapping = {
            "o": ApprovalDecisionType.APPROVE_ONCE,
            "y": ApprovalDecisionType.APPROVE_ONCE,
            "s": ApprovalDecisionType.APPROVE_SESSION,
            "p": ApprovalDecisionType.APPROVE_PROJECT,
            "n": ApprovalDecisionType.REJECT,
            "c": ApprovalDecisionType.CANCEL,
        }
        selected = mapping.get(answer)
        if selected is not None and selected in request.options:
            return ApprovalDecision(selected)
        self._console.print("[yellow]无效审批选项，已取消本次工具调用。[/yellow]")
        return ApprovalDecision(ApprovalDecisionType.CANCEL)


def _format_compaction(report, fallback: str = "") -> str:
    if report is None:
        return fallback or "上下文压缩状态未知"

    actions = set(report.actions)
    if report.status is CompactStatus.FAILED:
        headline = "压缩失败"
    elif report.status is CompactStatus.NO_OP or actions == {CompactAction.NONE}:
        headline = "无需压缩"
    elif CompactAction.EMERGENCY in actions:
        headline = "应急压缩完成"
    elif CompactAction.FORCE in actions:
        headline = "手动压缩完成"
    else:
        headline = "上下文已压缩"

    if report.message_zh:
        headline = f"{headline}：{report.message_zh}"
    elif fallback:
        headline = f"{headline}：{fallback}"

    details = [
        f"{report.before_tokens} -> {report.after_tokens}",
        f"归档 {report.archived_count}",
    ]
    if report.attempts:
        details.append(f"尝试 {report.attempts}")
    if report.circuit_open:
        details.append("熔断已打开")
    if report.failure_code is not None:
        details.append(f"原因 {report.failure_code.value}")
    return f"{headline}，{'；'.join(details)}"


def _mode_label(mode: PermissionMode) -> str:
    return {
        PermissionMode.STRICT: "严格 (strict)",
        PermissionMode.DEFAULT: "默认 (default)",
        PermissionMode.PERMISSIVE: "宽松 (permissive)",
    }[mode]


def _source_label(source: RuleSource | None) -> str:
    return {
        RuleSource.SESSION: "当前会话",
        RuleSource.LOCAL_PROJECT: "本地项目",
        RuleSource.REPOSITORY_PROJECT: "仓库项目",
        RuleSource.USER_GLOBAL: "用户全局",
        None: "内置默认",
    }[source]
