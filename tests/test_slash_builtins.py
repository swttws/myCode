from __future__ import annotations

import asyncio
from types import SimpleNamespace

from mycode.mcp.models import MCPServerState
from mycode.permission.models import PermissionMode, RuleSource
from mycode.slash import (
    GitStatusSnapshot,
    MCPServerStatus,
    MCPStatusSnapshot,
    ParsedSlashInput,
    PermissionStatusSnapshot,
    SlashCommandContext,
    SlashCommandType,
    SlashHandlerSignal,
    SlashMode,
)
import mycode.slash.builtins as builtins


def _assert_in_order(text: str, parts: list[str]) -> None:
    cursor = 0
    for part in parts:
        index = text.find(part, cursor)
        assert index >= cursor
        cursor = index + len(part)


class FakeController:
    def __init__(self) -> None:
        self.messages: list[tuple[str, bool]] = []
        self.user_messages: list[str] = []
        self.compact_calls = 0
        self.clear_calls = 0
        self.mode = SlashMode.DEFAULT
        self.mode_updates: list[SlashMode] = []
        self.permission_updates: list[PermissionMode] = []
        self.permission_snapshot = PermissionStatusSnapshot(
            mode=PermissionMode.DEFAULT,
            source=RuleSource.SESSION,
        )
        self.permission_status_calls = 0
        self.token_snapshot = SimpleNamespace(
            estimated_tokens=2048,
            context_window_tokens=8192,
            usage_ratio=0.25,
            source="estimated",
        )
        self.session_snapshot = SimpleNamespace(
            session_id="session-1",
            message_count=7,
            source="restored",
            restored_from_session_id="session-0",
            updated_at="2026-07-24T09:00:00+08:00",
        )
        self.memory_snapshot = SimpleNamespace(
            user=SimpleNamespace(
                path="/tmp/user-memory",
                note_count=2,
                line_count=5,
                byte_count=100,
                diagnostics=("memory_index_truncated",),
            ),
            project=SimpleNamespace(
                path="/tmp/project-memory",
                note_count=3,
                line_count=8,
                byte_count=200,
                diagnostics=("project_index_truncated",),
            ),
        )
        self.git_snapshot = GitStatusSnapshot(
            is_repository=True,
            repository_root="/tmp/workspace",
            branch="main",
            upstream="origin/main",
            ahead=1,
            behind=2,
            staged=3,
            unstaged=4,
            untracked=5,
        )
        self.mcp_snapshot = MCPStatusSnapshot(
            servers=(
                MCPServerStatus(
                    name="files",
                    state=MCPServerState.READY,
                    available=True,
                    tool_count=4,
                    diagnostic_categories=("connection", "tool_registry"),
                ),
            )
        )
        self.application_snapshot = SimpleNamespace(
            workspace_root="/tmp/workspace",
            mode=SlashMode.PLAN,
            permission=SimpleNamespace(value=self.permission_snapshot, error=None),
            token=SimpleNamespace(value=self.token_snapshot, error=None),
            session=SimpleNamespace(value=self.session_snapshot, error=None),
            memory=SimpleNamespace(value=self.memory_snapshot, error=None),
            git=SimpleNamespace(value=self.git_snapshot, error=None),
            mcp=SimpleNamespace(value=self.mcp_snapshot, error=None),
        )

    def show_message(self, text: str, *, error: bool = False) -> None:
        self.messages.append((text, error))

    async def send_user_message(self, text: str) -> None:
        self.user_messages.append(text)

    async def compact_context(self) -> None:
        self.compact_calls += 1

    def clear_session(self) -> None:
        self.clear_calls += 1
        self.mode = SlashMode.DEFAULT
        self.permission_snapshot = PermissionStatusSnapshot(
            mode=PermissionMode.DEFAULT,
            source=None,
        )

    def current_mode(self) -> SlashMode:
        return self.mode

    def set_mode(self, mode: SlashMode) -> None:
        self.mode_updates.append(mode)
        self.mode = mode

    def permission_status(self) -> PermissionStatusSnapshot:
        self.permission_status_calls += 1
        return self.permission_snapshot

    def set_permission_mode(self, mode: PermissionMode) -> None:
        self.permission_updates.append(mode)
        self.permission_snapshot = PermissionStatusSnapshot(
            mode=mode,
            source=RuleSource.SESSION,
        )

    async def token_status(self):
        return self.token_snapshot

    async def session_status(self):
        return self.session_snapshot

    async def memory_status(self):
        return self.memory_snapshot

    async def application_status(self):
        return self.application_snapshot


def _context(controller: FakeController, registry=None) -> SlashCommandContext:
    if registry is None:
        registry = builtins.create_default_slash_registry()
    return SlashCommandContext(controller=controller, registry=registry)


def _run_command(command_name: str, controller: FakeController, arguments: str = ""):
    registry = builtins.create_default_slash_registry()
    command = registry.resolve(command_name)
    assert command is not None
    return asyncio.run(command.handler(_context(controller, registry), arguments))


def test_create_default_slash_registry_registers_expected_commands_and_aliases():
    registry = builtins.create_default_slash_registry()

    assert [command.name for command in registry.public_commands()] == [
        "help",
        "compact",
        "clear",
        "plan",
        "do",
        "session",
        "memory",
        "permission",
        "status",
    ]
    assert [command.aliases for command in registry.public_commands()] == [
        ("h", "?"),
        ("comp",),
        ("cls",),
        ("p",),
        ("d",),
        ("sess",),
        ("mem",),
        ("perm",),
        ("stat",),
    ]
    assert [command.command_type for command in registry.public_commands()] == [
        SlashCommandType.LOCAL,
        SlashCommandType.LOCAL,
        SlashCommandType.UI_STATE,
        SlashCommandType.UI_STATE,
        SlashCommandType.UI_STATE,
        SlashCommandType.LOCAL,
        SlashCommandType.LOCAL,
        SlashCommandType.UI_STATE,
        SlashCommandType.LOCAL,
    ]
    assert [command.hidden for command in registry.public_commands()] == [False] * 9
    assert registry.resolve("help") is registry.resolve("h")
    assert registry.resolve("help") is registry.resolve("?")
    assert registry.resolve("compact") is registry.resolve("comp")
    assert registry.resolve("clear") is registry.resolve("cls")
    assert registry.resolve("plan") is registry.resolve("p")
    assert registry.resolve("do") is registry.resolve("d")
    assert registry.resolve("session") is registry.resolve("sess")
    assert registry.resolve("memory") is registry.resolve("mem")
    assert registry.resolve("permission") is registry.resolve("perm")
    assert registry.resolve("status") is registry.resolve("stat")
    assert registry.resolve("review") is None
    assert registry.resolve("rev") is None

    exit_command = registry.resolve("exit")
    assert exit_command is not None
    assert exit_command.hidden is True
    assert registry.resolve("quit") is exit_command
    assert registry.resolve("plan-only") is None
    assert "exit" not in {command.name for command in registry.public_commands()}


def test_help_lists_public_commands_in_registration_order_and_hides_exit():
    controller = FakeController()

    result = _run_command("help", controller)

    assert result is SlashHandlerSignal.CONTINUE
    assert controller.user_messages == []
    assert len(controller.messages) == 1
    text, is_error = controller.messages[0]
    assert is_error is False
    _assert_in_order(
        text,
        [
            "/help",
            "/compact",
            "/clear",
            "/plan",
            "/do",
            "/session",
            "/memory",
            "/permission",
            "/status",
        ],
    )
    assert "/exit" not in text
    assert "/quit" not in text


def test_help_detail_uses_alias_and_shows_type_hint_and_usage():
    controller = FakeController()

    result = _run_command("help", controller, "stat")

    assert result is SlashHandlerSignal.CONTINUE
    assert len(controller.messages) == 1
    text, is_error = controller.messages[0]
    assert is_error is False
    assert "/status" in text
    assert "stat" in text
    assert "status" in text
    assert "本地" in text
    assert "参数" in text
    assert "/exit" not in text


def test_help_rejects_unknown_hidden_or_extra_arguments():
    controller = FakeController()

    result = _run_command("help", controller, "quit")

    assert result is SlashHandlerSignal.CONTINUE
    assert controller.user_messages == []
    assert len(controller.messages) == 1
    text, is_error = controller.messages[0]
    assert is_error is True
    assert "/help" in text
    assert "/quit" in text

    controller = FakeController()
    result = _run_command("help", controller, "status extra")

    assert result is SlashHandlerSignal.CONTINUE
    text, is_error = controller.messages[0]
    assert is_error is True
    assert "/help [command]" in text


def test_compact_command_invokes_compact_context_without_forwarding_user_message():
    controller = FakeController()

    result = _run_command("compact", controller)

    assert result is SlashHandlerSignal.CONTINUE
    assert controller.compact_calls == 1
    assert controller.user_messages == []
    assert controller.messages == []


def test_compact_command_rejects_extra_arguments():
    controller = FakeController()

    result = _run_command("compact", controller, "unexpected")

    assert result is SlashHandlerSignal.CONTINUE
    assert controller.compact_calls == 0
    assert controller.user_messages == []
    assert len(controller.messages) == 1
    text, is_error = controller.messages[0]
    assert is_error is True
    assert "/compact" in text


def test_clear_command_resets_session_without_forwarding_user_message():
    controller = FakeController()
    controller.mode = SlashMode.PLAN
    controller.permission_snapshot = PermissionStatusSnapshot(
        mode=PermissionMode.STRICT,
        source=RuleSource.SESSION,
    )

    result = _run_command("clear", controller)

    assert result is SlashHandlerSignal.CONTINUE
    assert controller.clear_calls == 1
    assert controller.mode is SlashMode.DEFAULT
    assert controller.permission_snapshot.mode is PermissionMode.DEFAULT
    assert controller.user_messages == []
    assert len(controller.messages) == 1
    text, is_error = controller.messages[0]
    assert is_error is False
    assert "清空" in text


def test_plan_and_do_commands_toggle_mode_stably():
    controller = FakeController()

    result = _run_command("plan", controller)
    assert result is SlashHandlerSignal.CONTINUE
    assert controller.mode is SlashMode.PLAN
    assert controller.mode_updates == [SlashMode.PLAN]
    assert controller.messages[-1][1] is False
    assert "[PLAN]" in controller.messages[-1][0]

    result = _run_command("plan", controller)
    assert result is SlashHandlerSignal.CONTINUE
    assert controller.mode_updates == [SlashMode.PLAN]
    assert "[PLAN]" in controller.messages[-1][0]

    result = _run_command("do", controller)
    assert result is SlashHandlerSignal.CONTINUE
    assert controller.mode is SlashMode.DEFAULT
    assert controller.mode_updates == [SlashMode.PLAN, SlashMode.DEFAULT]
    assert "[DEFAULT]" in controller.messages[-1][0]

    result = _run_command("do", controller)
    assert result is SlashHandlerSignal.CONTINUE
    assert controller.mode_updates == [SlashMode.PLAN, SlashMode.DEFAULT]
    assert "[DEFAULT]" in controller.messages[-1][0]


def test_permission_command_reports_and_updates_session_mode():
    controller = FakeController()
    controller.permission_snapshot = PermissionStatusSnapshot(
        mode=PermissionMode.PERMISSIVE,
        source=RuleSource.LOCAL_PROJECT,
    )

    result = _run_command("permission", controller)

    assert result is SlashHandlerSignal.CONTINUE
    assert controller.permission_status_calls == 1
    assert controller.permission_updates == []
    assert len(controller.messages) == 1
    text, is_error = controller.messages[0]
    assert is_error is False
    assert "permissive" in text
    assert "local_project" in text

    result = _run_command("permission", controller, "strict")

    assert result is SlashHandlerSignal.CONTINUE
    assert controller.permission_updates == [PermissionMode.STRICT]
    assert "strict" in controller.messages[-1][0]

    result = _run_command("permission", controller, "strict extra")

    assert result is SlashHandlerSignal.CONTINUE
    assert controller.permission_updates == [PermissionMode.STRICT]
    assert controller.messages[-1][1] is True
    assert "/permission" in controller.messages[-1][0]


def test_session_command_formats_summary_and_does_not_send_user_message():
    controller = FakeController()

    result = _run_command("session", controller)

    assert result is SlashHandlerSignal.CONTINUE
    assert controller.user_messages == []
    assert len(controller.messages) == 1
    text, is_error = controller.messages[0]
    assert is_error is False
    assert "session-1" in text
    assert "7" in text
    assert "restored" in text
    assert "2026-07-24T09:00:00+08:00" in text


def test_memory_command_formats_paths_counts_and_diagnostics_without_body():
    controller = FakeController()

    result = _run_command("memory", controller)

    assert result is SlashHandlerSignal.CONTINUE
    assert controller.user_messages == []
    assert len(controller.messages) == 1
    text, is_error = controller.messages[0]
    assert is_error is False
    assert "/tmp/user-memory" in text
    assert "/tmp/project-memory" in text
    assert "2" in text
    assert "3" in text
    assert "memory_index_truncated" in text
    assert "project_index_truncated" in text
    assert "body" not in text.lower()


def test_status_command_formats_application_status_without_forwarding_user_message():
    controller = FakeController()

    result = _run_command("status", controller)

    assert result is SlashHandlerSignal.CONTINUE
    assert controller.user_messages == []
    assert len(controller.messages) == 1
    text, is_error = controller.messages[0]
    assert is_error is False
    assert "[PLAN]" in text
    assert "/tmp/workspace" in text
    assert "main" in text
    assert "files" in text
    assert "session-1" in text
    assert "/tmp/user-memory" in text


def test_review_is_no_longer_registered_as_fixed_builtin_command():
    registry = builtins.create_default_slash_registry()

    assert registry.resolve("review") is None
    assert registry.resolve("rev") is None


def test_hidden_exit_command_returns_exit_and_quit_alias_matches_it():
    registry = builtins.create_default_slash_registry()
    controller = FakeController()
    context = _context(controller, registry)

    assert registry.resolve("exit") is not None
    assert registry.resolve("quit") is registry.resolve("exit")
    assert asyncio.run(registry.resolve("exit").handler(context, "")) is SlashHandlerSignal.EXIT
    assert asyncio.run(registry.resolve("quit").handler(context, "")) is SlashHandlerSignal.EXIT
