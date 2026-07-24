from __future__ import annotations

from collections.abc import Sequence

from mycode.slash.models import SlashCommand


class SlashCommandRegistrationError(ValueError):
    """Raised when slash command metadata cannot be registered safely."""


class SlashCommandRegistry:
    def __init__(self, commands: Sequence[SlashCommand]) -> None:
        command_tuple = tuple(commands)
        index: dict[str, SlashCommand] = {}

        for command in command_tuple:
            self._register_identifier(index, command.name, command)
            for alias in command.aliases:
                self._register_identifier(index, alias, command)

        # 先在临时索引中完成全部校验，避免异常时留下部分注册状态。
        self._commands = command_tuple
        self._index = index

    def _register_identifier(
        self,
        index: dict[str, SlashCommand],
        identifier: str,
        command: SlashCommand,
    ) -> None:
        self._validate_identifier(identifier)

        key = identifier.lower()
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
