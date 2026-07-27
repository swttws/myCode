from __future__ import annotations

import asyncio
import subprocess
from io import StringIO
from pathlib import Path
from types import SimpleNamespace

from rich.console import Console

from mycode.agent import AgentEvent, AgentEventType
from mycode.compact.models import ContextTokenStatus
from mycode.mcp.models import MCPServerState
from mycode.memory.models import (
    MemoryScope,
    MemoryScopeStatus,
    MemoryStatusSnapshot,
    SessionSource,
    SessionStatusSnapshot,
)
from mycode.permission.models import (
    ApprovalDecisionType,
    ApprovalRequest,
    PermissionDecision,
    PermissionEffect,
    PermissionMode,
    RuleSource,
)
from mycode.slash import (
    REVIEW_PROMPT,
    SlashCommandCompleter,
    SlashCommandDispatcher,
    create_default_slash_registry,
)
from mycode.tui import ChatTUI
from mycode.tool import ToolCall


class FakeSession:
    def __init__(self, *, send_scripts=None) -> None:
        self.send_scripts = list(send_scripts or [])
        self.send_calls: list[str] = []
        self.approval_decisions: list[ApprovalDecisionType] = []
        self.compact_count = 0
        self.clear_count = 0
        self.plan_only = False
        self.mode_updates: list[bool] = []
        self.permission = (PermissionMode.DEFAULT, None)
        self.permission_updates: list[PermissionMode] = []
        self.token_snapshot = ContextTokenStatus(
            estimated_tokens=512,
            context_window_tokens=2048,
            usage_ratio=0.25,
            source="full_chars",
        )
        self.session_snapshot = SessionStatusSnapshot(
            session_id="session-1",
            message_count=2,
            source=SessionSource.NEW,
            restored_from_session_id=None,
            updated_at="2026-07-24T09:00:00+08:00",
        )
        self.memory_snapshot = MemoryStatusSnapshot(
            user=MemoryScopeStatus(
                scope=MemoryScope.USER,
                path="/tmp/user-memory",
                note_count=3,
                index_line_count=7,
                index_byte_count=111,
                diagnostic_codes=("user_diag",),
            ),
            project=MemoryScopeStatus(
                scope=MemoryScope.PROJECT,
                path="/tmp/project-memory",
                note_count=5,
                index_line_count=9,
                index_byte_count=222,
                diagnostic_codes=("project_diag",),
            ),
            diagnostic_codes=("user_diag", "project_diag"),
        )

    async def send(self, user_text, *, approval_provider=None):
        self.send_calls.append(user_text)
        if not self.send_scripts:
            raise AssertionError(f"unexpected user message: {user_text!r}")

        for event in self.send_scripts.pop(0):
            yield event
            if event.type is AgentEventType.APPROVAL_REQUIRED:
                assert approval_provider is not None
                decision = await approval_provider(event.approval_request)
                self.approval_decisions.append(decision.type)

    async def compact(self):
        self.compact_count += 1
        if False:
            yield None

    def clear(self):
        self.clear_count += 1
        self.plan_only = False
        self.permission = (PermissionMode.DEFAULT, None)

    def set_plan_only(self, enabled):
        self.mode_updates.append(enabled)
        self.plan_only = enabled

    def is_plan_only(self):
        return self.plan_only

    def permission_mode(self):
        return self.permission

    def set_permission_mode(self, mode):
        self.permission_updates.append(mode)
        self.permission = (mode, RuleSource.SESSION)

    async def token_status(self):
        return self.token_snapshot

    async def session_status(self):
        return self.session_snapshot

    async def memory_status(self):
        return self.memory_snapshot


class FakeMCPPool:
    server_names = ("files",)
    diagnostics = ()
    tools = (SimpleNamespace(server_name="files"),)

    def server_state(self, server_name):
        assert server_name == "files"
        return MCPServerState.READY

    def is_available(self, server_name):
        assert server_name == "files"
        return True


def _make_tui(session, workspace_root: Path, inputs):
    registry = create_default_slash_registry()
    output = StringIO()
    return (
        ChatTUI(
            session=session,
            dispatcher=SlashCommandDispatcher(registry),
            registry=registry,
            completer=SlashCommandCompleter(registry),
            mcp_pool=FakeMCPPool(),
            workspace_root=workspace_root,
            console=Console(file=output, force_terminal=False, color_system=None, width=140),
            input_func=lambda: next(inputs),
        ),
        output,
    )


def _init_git_workspace(workspace: Path) -> None:
    workspace.mkdir()
    _git(workspace, "init", "-b", "main")
    (workspace / ".gitignore").write_text("ignored.txt\n", encoding="utf-8")
    _git(workspace, "add", ".gitignore")
    _git(
        workspace,
        "-c",
        "user.name=Test User",
        "-c",
        "user.email=test@example.com",
        "commit",
        "-m",
        "init",
    )
    (workspace / "tracked.txt").write_text("staged\n", encoding="utf-8")
    _git(workspace, "add", "tracked.txt")
    (workspace / "tracked.txt").write_text("unstaged\n", encoding="utf-8")
    (workspace / "untracked.txt").write_text("new\n", encoding="utf-8")
    (workspace / "ignored.txt").write_text("secret-ignore\n", encoding="utf-8")


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def _approval_request() -> ApprovalRequest:
    return ApprovalRequest(
        id="approval-1",
        tool_call=ToolCall(
            id="call-1",
            name="write_file",
            arguments={"path": "review.txt"},
        ),
        decision=PermissionDecision(
            effect=PermissionEffect.ASK,
            reason_code="write_tool",
            message_zh="write requires approval",
            mode=PermissionMode.DEFAULT,
            display_arguments={"path": "review.txt"},
            source=RuleSource.SESSION,
        ),
        options=(
            ApprovalDecisionType.APPROVE_ONCE,
            ApprovalDecisionType.REJECT,
            ApprovalDecisionType.CANCEL,
        ),
        candidate_grant=None,
        plan_only=False,
        round_index=0,
    )


def test_slash_command_workflow_end_to_end(tmp_path):
    workspace = tmp_path / "workspace"
    _init_git_workspace(workspace)
    session = FakeSession(
        send_scripts=[
            (AgentEvent(AgentEventType.TEXT_DELTA, "plain reply"),),
        ],
    )
    inputs = iter(
        [
            "/help",
            "/plan",
            "/status",
            "/session",
            "/memory",
            "plain question",
            "/do",
            "/clear",
            "/exit",
        ]
    )
    tui, output = _make_tui(session, workspace, inputs)

    assert asyncio.run(tui.run()) == 0

    text = output.getvalue()
    assert session.send_calls == ["plain question"]
    assert session.mode_updates == [True, False]
    assert session.clear_count == 1
    assert session.plan_only is False
    assert session.permission == (PermissionMode.DEFAULT, None)
    assert "/help" in text
    assert "/plan" in text
    assert "/do" in text
    assert "/review" in text
    assert "/plan-only" not in text
    assert "/exit" not in text
    assert "[PLAN]" in text
    assert "[DEFAULT]" in text
    assert str(workspace) in text
    assert "512 / 2048" in text
    assert "session-1" in text
    assert "new" in text
    assert "/tmp/user-memory" in text
    assert "/tmp/project-memory" in text
    assert "7" in text
    assert "9" in text
    assert "user_diag" in text
    assert "project_diag" in text
    assert "staged/unstaged/untracked" in text
    assert "1/1/1" in text
    assert "ignored.txt" not in text
    assert "secret-ignore" not in text
    assert "files" in text
    assert "available=yes" in text
    assert "plain reply" in text


def test_review_command_uses_normal_agent_and_permission_flow(tmp_path):
    workspace = tmp_path / "workspace"
    _init_git_workspace(workspace)
    session = FakeSession(
        send_scripts=[
            (
                AgentEvent(AgentEventType.APPROVAL_REQUIRED, approval_request=_approval_request()),
                AgentEvent(AgentEventType.TEXT_DELTA, "review complete"),
            ),
        ],
    )
    inputs = iter(
        [
            "/review",
            "o",
            "/permission permissive",
            "/permission",
            "/exit",
        ]
    )
    tui, output = _make_tui(session, workspace, inputs)

    assert asyncio.run(tui.run()) == 0

    text = output.getvalue()
    assert session.send_calls == [REVIEW_PROMPT]
    assert "/review" not in session.send_calls[0]
    assert "已暂存" in session.send_calls[0]
    assert "未暂存" in session.send_calls[0]
    assert "未跟踪" in session.send_calls[0]
    assert "忽略" in session.send_calls[0]
    assert session.approval_decisions == [ApprovalDecisionType.APPROVE_ONCE]
    assert session.permission_updates == [PermissionMode.PERMISSIVE]
    assert session.permission == (PermissionMode.PERMISSIVE, RuleSource.SESSION)
    assert "review complete" in text
    assert "permissive" in text
    assert "review.txt" in text
    assert "ignored.txt" not in text
    assert "secret-ignore" not in text
