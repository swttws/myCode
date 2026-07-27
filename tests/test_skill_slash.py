from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from prompt_toolkit.document import Document

from mycode.permission.models import PermissionMode, RuleSource
from mycode.slash import SlashCommand, SlashCommandRegistry, SlashCommandType
from mycode.slash.completion import SlashCommandCompleter
from mycode.slash.dispatcher import SlashCommandDispatcher
from mycode.slash.models import SlashDispatchKind, SlashHandlerSignal, SlashMode
from mycode.slash.registry import SlashCommandRegistrationError
from mycode.skill.catalog import SkillCatalog
from mycode.skill.loader import SkillLoader
from mycode.skill.runtime import SkillRuntime
from mycode.skill.slash import SkillSlashBridge
from tests.skill_test_support import write_skill


class FakeController:
    def __init__(self) -> None:
        self.messages = []
        self.executed_skills = []

    def show_message(self, text: str, *, error: bool = False) -> None:
        self.messages.append((text, error))

    async def send_user_message(self, text: str) -> None:
        raise AssertionError("dynamic skills should call execute_skill")

    async def execute_skill(self, name: str, arguments: str) -> None:
        self.executed_skills.append((name, arguments))

    def clear_session(self) -> None:
        pass

    def current_mode(self):
        return SlashMode.DEFAULT

    def set_mode(self, mode):
        pass

    def permission_status(self):
        from mycode.slash.models import PermissionStatusSnapshot

        return PermissionStatusSnapshot(PermissionMode.DEFAULT, RuleSource.DEFAULT)


async def static_handler(context, arguments):
    return SlashHandlerSignal.CONTINUE


def make_runtime(tmp_path: Path) -> tuple[SkillRuntime, Path]:
    workspace = tmp_path / "workspace"
    home = tmp_path / "home"
    builtin = tmp_path / "builtins"
    project_root = workspace / ".mycode" / "skills"
    for root in (project_root, home / ".mycode" / "skills", builtin):
        root.mkdir(parents=True)
    runtime = SkillRuntime(
        SkillCatalog(
            loader=SkillLoader(workspace_root=workspace, home=home, builtin_root=builtin),
            tool_names=lambda: frozenset({"read_file"}),
            reserved_slash_names=frozenset({"help", "h"}),
        )
    )
    runtime.refresh()
    return runtime, project_root


def test_registry_atomically_replaces_dynamic_commands_and_preserves_static_conflicts():
    static = SlashCommand(
        name="help",
        aliases=("h",),
        description="help",
        usage="/help",
        command_type=SlashCommandType.LOCAL,
        handler=static_handler,
    )
    registry = SlashCommandRegistry([static])
    review = SlashCommand(
        name="review",
        aliases=(),
        description="review",
        usage="/review",
        command_type=SlashCommandType.PROMPT,
        handler=static_handler,
    )

    registry.replace_dynamic_commands([review])
    assert [command.name for command in registry.public_commands()] == ["help", "review"]

    with pytest.raises(SlashCommandRegistrationError):
        registry.replace_dynamic_commands(
            [
                SlashCommand(
                    name="h",
                    aliases=(),
                    description="bad",
                    usage="/h",
                    command_type=SlashCommandType.PROMPT,
                    handler=static_handler,
                )
            ]
        )

    assert [command.name for command in registry.public_commands()] == ["help", "review"]


def test_skill_slash_bridge_refresh_registers_commands_and_dispatches_arguments(tmp_path):
    runtime, project_root = make_runtime(tmp_path)
    registry = SlashCommandRegistry([])
    bridge = SkillSlashBridge(runtime=runtime, registry=registry)
    write_skill(project_root, "review", description="审查变更", allowed_tools=("read_file",))

    diagnostics = bridge.refresh()

    assert diagnostics == ()
    command = registry.resolve("review")
    assert command.description == "审查变更"
    controller = FakeController()
    result = asyncio.run(SlashCommandDispatcher(registry).dispatch("/review main", controller))

    assert result.kind is SlashDispatchKind.HANDLED
    assert controller.executed_skills == [("review", "main")]


def test_dispatcher_and_completer_refresh_before_reading_registry(tmp_path):
    runtime, project_root = make_runtime(tmp_path)
    registry = SlashCommandRegistry([])
    bridge = SkillSlashBridge(runtime=runtime, registry=registry)
    dispatcher = SlashCommandDispatcher(registry, before_dispatch=bridge.refresh)
    completer = SlashCommandCompleter(registry, before_complete=bridge.refresh_silent)

    write_skill(project_root, "test", description="运行测试", allowed_tools=("read_file",))

    completions = list(completer.get_completions(Document("/te"), None))
    result = asyncio.run(dispatcher.dispatch("/test target", FakeController()))

    assert [completion.text for completion in completions] == ["/test"]
    assert result.kind is SlashDispatchKind.HANDLED
