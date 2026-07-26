from __future__ import annotations

from mycode.slash.models import ParsedSlashInput, SlashInputKind


def parse_slash_input(text: str) -> ParsedSlashInput:
    stripped = text.strip()
    if not stripped:
        return ParsedSlashInput(kind=SlashInputKind.EMPTY, text="")

    if not stripped.startswith("/"):
        return ParsedSlashInput(kind=SlashInputKind.NORMAL, text=stripped)

    command_name, arguments = _split_command_body(stripped[1:])
    return ParsedSlashInput(
        kind=SlashInputKind.COMMAND,
        text=stripped,
        command_name=command_name.casefold(),
        arguments=arguments,
    )


def _split_command_body(command_body: str) -> tuple[str, str]:
    separator_start = None
    for index, character in enumerate(command_body):
        if character.isspace():
            separator_start = index
            break

    if separator_start is None:
        return command_body, ""

    separator_end = separator_start
    while separator_end < len(command_body) and command_body[separator_end].isspace():
        separator_end += 1

    return command_body[:separator_start], command_body[separator_end:]
