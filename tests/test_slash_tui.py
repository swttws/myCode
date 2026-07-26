from __future__ import annotations

import asyncio
from io import StringIO
from pathlib import Path
from types import SimpleNamespace

from prompt_toolkit.shortcuts import CompleteStyle
from rich.console import Console

from mycode.agent import AgentEvent, AgentEventType
from mycode.compact.models import ContextTokenStatus
from mycode.memory.models import (
    MemoryScope,
    MemoryScopeStatus,
    MemoryStatusSnapshot,
    SessionSource,
    SessionStatusSnapshot,
)
from mycode.mcp.models import MCPServerState
from mycode.permission.models import PermissionMode, RuleSource
from mycode.slash import SlashDispatchKind, SlashDispatchResult, SlashMode, create_default_slash_registry
from mycode.slash.completion import SlashCommandCompleter
from mycode.slash.models import MCPServerStatus, MCPStatusSnapshot, PermissionStatusSnapshot
from mycode import tui as tui_module
from mycode.tui import ChatTUI


class FakeSession:
    def __init__(
        self,
        *,
        send_scripts=None,
        compact_scripts=None,
        token_status=None,
        session_status=None,
        memory_status=None,
        permission=(PermissionMode.DEFAULT, None),
    ) -> None:
        self.send_scripts = list(send_scripts or [])
        self.compact_scripts = list(compact_scripts or [])
        self.send_calls: list[str] = []
        self.send_kwargs = []
        self.compact_count = 0
        self.clear_count = 0
        self.plan_only = False
        self.permission = permission
        self._token_status = token_status
        self._session_status = session_status
        self._memory_status = memory_status

    async def send(self, user_text, **kwargs):
        self.send_calls.append(user_text)
        self.send_kwargs.append(kwargs)
        for event in self.send_scripts.pop(0):
            yield event

    async def compact(self):
        self.compact_count += 1
        for event in self.compact_scripts.pop(0):
            yield event

    def clear(self):
        self.clear_count += 1

    def set_plan_only(self, enabled):
        self.plan_only = enabled

    def is_plan_only(self):
        return self.plan_only

    def permission_mode(self):
        return self.permission

    def set_permission_mode(self, mode):
        self.permission = (mode, RuleSource.SESSION)

    async def token_status(self):
        return self._token_status

    async def session_status(self):
        return self._session_status

    async def memory_status(self):
        return self._memory_status


class ScriptedDispatcher:
    def __init__(self, results):
        self.results = list(results)
        self.calls = []

    async def dispatch(self, text, controller):
        self.calls.append((text, controller))
        return self.results.pop(0)


def _make_console():
    output = StringIO()
    console = Console(file=output, force_terminal=False, color_system=None, width=120)
    return console, output


def _make_session_status():
    return SessionStatusSnapshot(
        session_id="session-1",
        message_count=2,
        source=SessionSource.NEW,
        restored_from_session_id=None,
        updated_at=None,
    )


def _make_memory_status():
    return MemoryStatusSnapshot(
        user=MemoryScopeStatus(
            scope=MemoryScope.USER,
            path="user",
            note_count=1,
            index_line_count=2,
            index_byte_count=3,
            diagnostic_codes=("user_index_warning",),
        ),
        project=MemoryScopeStatus(
            scope=MemoryScope.PROJECT,
            path="project",
            note_count=4,
            index_line_count=5,
            index_byte_count=6,
            diagnostic_codes=("project_index_warning",),
        ),
        diagnostic_codes=("user_index_warning", "project_index_warning"),
    )


def test_tui_routes_dispatcher_results_and_only_sends_plain_text():
    console, output = _make_console()
    session = FakeSession(
        send_scripts=[
            [
                AgentEvent(AgentEventType.TEXT_DELTA, "reply"),
                AgentEvent(AgentEventType.FINAL_RESPONSE, "reply"),
            ]
        ]
    )
    dispatcher = ScriptedDispatcher(
        [
            SlashDispatchResult(kind=SlashDispatchKind.EMPTY),
            SlashDispatchResult(kind=SlashDispatchKind.HANDLED),
            SlashDispatchResult(kind=SlashDispatchKind.NOT_COMMAND, normal_text="hello"),
            SlashDispatchResult(kind=SlashDispatchKind.EXIT),
        ]
    )
    inputs = iter(["", "/help", "  hello  ", "/quit"])
    tui = ChatTUI(
        session=session,
        dispatcher=dispatcher,
        registry=create_default_slash_registry(),
        console=console,
        input_func=lambda: next(inputs),
    )

    exit_code = asyncio.run(tui.run())

    assert exit_code == 0
    assert [text for text, _controller in dispatcher.calls] == ["", "/help", "  hello  ", "/quit"]
    assert session.send_calls == ["hello"]
    assert "reply" in output.getvalue()
    assert "/help" in output.getvalue()
    assert "/exit" not in output.getvalue()


def test_tui_prompt_session_uses_completion_and_dynamic_toolbar(monkeypatch):
    console, _ = _make_console()
    session = FakeSession()
    registry = create_default_slash_registry()
    prompts = iter(["/help", "/exit"])
    captured = {}

    class FakePromptSession:
        def __init__(self, **kwargs):
            captured["kwargs"] = kwargs
            captured["session"] = self
            self.prompts = []

        async def prompt_async(self, label):
            self.prompts.append(label)
            return next(prompts)

    monkeypatch.setattr(tui_module, "PromptSession", FakePromptSession)

    tui = ChatTUI(session=session, registry=registry, console=console)

    first = asyncio.run(tui._prompt())
    tui.set_mode(SlashMode.PLAN)
    second = asyncio.run(tui._prompt())

    assert first == "/help"
    assert second == "/exit"
    kwargs = captured["kwargs"]
    assert isinstance(kwargs["completer"], SlashCommandCompleter)
    assert kwargs["complete_while_typing"] is False
    assert kwargs["complete_style"] is CompleteStyle.COLUMN
    assert callable(kwargs["bottom_toolbar"])
    assert kwargs["bottom_toolbar"]() == "[PLAN]"
    assert captured["session"].prompts == ["[DEFAULT] you> ", "[PLAN] you> "]


def test_tui_plain_prompt_falls_back_to_mode_prefixed_input(monkeypatch):
    console, _ = _make_console()
    session = FakeSession()
    registry = create_default_slash_registry()
    prompts = []

    class NoConsolePromptSession:
        def __init__(self, **kwargs):
            raise tui_module.NoConsoleScreenBufferError

    monkeypatch.setattr(tui_module, "PromptSession", NoConsolePromptSession)
    monkeypatch.setattr("builtins.input", lambda prompt: prompts.append(prompt) or "/exit")

    tui = ChatTUI(session=session, registry=registry, console=console)
    tui.set_mode(SlashMode.PLAN)

    assert asyncio.run(tui._prompt()) == "/exit"
    assert prompts == ["[PLAN] you> "]


def test_tui_status_methods_forward_and_application_status_isolated(monkeypatch):
    console, _ = _make_console()
    token_status = ContextTokenStatus(
        estimated_tokens=123,
        context_window_tokens=456,
        usage_ratio=0.27,
        source="full_chars",
    )
    session_status = _make_session_status()
    memory_status = _make_memory_status()
    mcp_status = MCPStatusSnapshot(
        servers=(
            MCPServerStatus(
                name="remote",
                state=MCPServerState.READY,
                available=True,
                tool_count=1,
                diagnostic_categories=(),
            ),
        )
    )

    class SuccessSession(FakeSession):
        def __init__(self):
            super().__init__(
                token_status=token_status,
                session_status=session_status,
                memory_status=memory_status,
                permission=(PermissionMode.STRICT, RuleSource.LOCAL_PROJECT),
            )

    class FakePool:
        server_names = ("remote",)
        diagnostics = ()
        tools = (SimpleNamespace(server_name="remote"),)

        def server_state(self, server_name):
            assert server_name == "remote"
            return MCPServerState.READY

        def is_available(self, server_name):
            assert server_name == "remote"
            return True

    def boom_git(_workspace_root, *, timeout_seconds=2.0):
        raise RuntimeError("git failed")

    monkeypatch.setattr(tui_module, "collect_git_status", boom_git)

    tui = ChatTUI(
        session=SuccessSession(),
        registry=create_default_slash_registry(),
        mcp_pool=FakePool(),
        workspace_root="/tmp/workspace",
        console=console,
    )

    assert asyncio.run(tui.token_status()) is token_status
    assert asyncio.run(tui.session_status()) is session_status
    assert asyncio.run(tui.memory_status()) is memory_status

    status = asyncio.run(tui.application_status())

    assert status.workspace_root == str(Path("/tmp/workspace"))
    assert status.mode is SlashMode.DEFAULT
    assert status.permission.value == PermissionStatusSnapshot(
        mode=PermissionMode.STRICT,
        source=RuleSource.LOCAL_PROJECT,
    )
    assert status.token.value is token_status
    assert status.session.value is session_status
    assert status.memory.value is memory_status
    assert status.git.value is None
    assert status.git.error == "git_status_unavailable"
    assert status.mcp.value == mcp_status
    assert status.mcp.error is None
