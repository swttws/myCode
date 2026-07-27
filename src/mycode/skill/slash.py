from __future__ import annotations

from mycode.skill.models import SkillDiagnostic
from mycode.skill.runtime import SkillRuntime
from mycode.slash.models import (
    SlashCommand,
    SlashCommandContext,
    SlashCommandType,
    SlashHandlerSignal,
)
from mycode.slash.registry import SlashCommandRegistry


class SkillSlashBridge:
    def __init__(
        self,
        *,
        runtime: SkillRuntime,
        registry: SlashCommandRegistry,
    ) -> None:
        self._runtime = runtime
        self._registry = registry
        self._pending_diagnostics: tuple[SkillDiagnostic, ...] = ()

    def refresh(self) -> tuple[SkillDiagnostic, ...]:
        snapshot = self._runtime.refresh()
        commands = tuple(_command_for_skill(definition.metadata.name, definition.metadata.description) for definition in snapshot.definitions)
        self._registry.replace_dynamic_commands(commands)
        diagnostics = self._pending_diagnostics + snapshot.diagnostics
        self._pending_diagnostics = ()
        return tuple(diagnostics)

    def refresh_silent(self) -> None:
        snapshot = self._runtime.refresh()
        commands = tuple(_command_for_skill(definition.metadata.name, definition.metadata.description) for definition in snapshot.definitions)
        self._registry.replace_dynamic_commands(commands)
        self._pending_diagnostics = snapshot.diagnostics


def _command_for_skill(name: str, description: str) -> SlashCommand:
    async def handler(context: SlashCommandContext, arguments: str) -> SlashHandlerSignal:
        await context.controller.execute_skill(name, arguments)
        return SlashHandlerSignal.CONTINUE

    return SlashCommand(
        name=name,
        aliases=(),
        description=description,
        usage=f"/{name} [arguments]",
        command_type=SlashCommandType.PROMPT,
        handler=handler,
        argument_hint="[arguments]",
    )
