from __future__ import annotations

from collections.abc import Sequence

from mycode.slash.models import SlashCommand, SlashCompletionCandidate


class SlashCommandRegistrationError(ValueError):
    """Raised when slash command metadata cannot be registered safely."""


class SlashCommandRegistry:
    def __init__(self, commands: Sequence[SlashCommand]) -> None:
        command_tuple = tuple(commands)
        self._static_commands = command_tuple
        self._dynamic_commands: tuple[SlashCommand, ...] = ()
        index: dict[str, SlashCommand] = {}

        for command in command_tuple:
            self._register_identifier(index, command.name, command)
            for alias in command.aliases:
                self._register_identifier(index, alias, command)

        # Validate against a temporary index first so failures do not leave partial state.
        self._commands = command_tuple
        self._index = index

    def replace_dynamic_commands(
        self,
        commands: Sequence[SlashCommand],
    ) -> None:
        dynamic_commands = tuple(commands)
        command_tuple = self._static_commands + dynamic_commands
        index: dict[str, SlashCommand] = {}
        for command in command_tuple:
            self._register_identifier(index, command.name, command)
            for alias in command.aliases:
                self._register_identifier(index, alias, command)
        self._dynamic_commands = dynamic_commands
        self._commands = command_tuple
        self._index = index

    def resolve(self, name: str, *, include_hidden: bool = True) -> SlashCommand | None:
        command = self._index.get(name.casefold())
        if command is None:
            return None
        if not include_hidden and command.hidden:
            return None
        return command

    def public_commands(self) -> tuple[SlashCommand, ...]:
        return tuple(command for command in self._commands if not command.hidden)

    def completion_candidates(self, prefix: str) -> tuple[SlashCompletionCandidate, ...]:
        normalized_prefix = prefix.casefold()
        candidates: list[SlashCompletionCandidate] = []

        for command in self._commands:
            if command.hidden:
                continue

            for identifier in (command.name, *command.aliases):
                candidate_text = f"/{identifier}"
                if candidate_text.casefold().startswith(normalized_prefix):
                    candidates.append(
                        SlashCompletionCandidate(
                            text=candidate_text,
                            description=command.description,
                        )
                    )

        return tuple(candidates)

    def _register_identifier(
        self,
        index: dict[str, SlashCommand],
        identifier: str,
        command: SlashCommand,
    ) -> None:
        self._validate_identifier(identifier)

        key = identifier.casefold()
        existing = index.get(key)
        if existing is not None:
            raise SlashCommandRegistrationError(
                "duplicate slash command identifier "
                f"{identifier!r} between {existing.name!r} and {command.name!r}"
            )
        index[key] = command

    @staticmethod
    def _validate_identifier(identifier: str) -> None:
        if not identifier:
            raise SlashCommandRegistrationError("slash command identifier must not be empty")
        if identifier.startswith("/"):
            raise SlashCommandRegistrationError(
                f"slash command identifier must not start with '/': {identifier!r}"
            )
        if any(character.isspace() for character in identifier):
            raise SlashCommandRegistrationError(
                f"slash command identifier must not contain whitespace: {identifier!r}"
            )
