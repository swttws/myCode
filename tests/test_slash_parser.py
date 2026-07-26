from __future__ import annotations

import pytest

from mycode.slash.models import ParsedSlashInput, SlashInputKind
from mycode.slash.parser import parse_slash_input


@pytest.mark.parametrize("text", ["", " ", "\t", "\n"])
def test_parse_slash_input_treats_empty_and_whitespace_only_input_as_empty(text: str):
    assert parse_slash_input(text) == ParsedSlashInput(
        kind=SlashInputKind.EMPTY,
        text="",
    )


@pytest.mark.parametrize(
    ("text", "expected_text"),
    [
        ("hello world", "hello world"),
        ("  hello   world  ", "hello   world"),
        ("\thello\t world\t", "hello\t world"),
    ],
)
def test_parse_slash_input_trims_outer_whitespace_but_preserves_internal_whitespace(
    text: str,
    expected_text: str,
):
    assert parse_slash_input(text) == ParsedSlashInput(
        kind=SlashInputKind.NORMAL,
        text=expected_text,
    )


@pytest.mark.parametrize(
    ("text", "expected_text", "expected_command_name"),
    [
        ("/help", "/help", "help"),
        ("  /HeLp  ", "/HeLp", "help"),
        (" /? ", "/?", "?"),
    ],
)
def test_parse_slash_input_recognizes_commands_and_casefolds_the_command_name(
    text: str,
    expected_text: str,
    expected_command_name: str,
):
    assert parse_slash_input(text) == ParsedSlashInput(
        kind=SlashInputKind.COMMAND,
        text=expected_text,
        command_name=expected_command_name,
    )


@pytest.mark.parametrize(
    ("text", "expected_arguments"),
    [
        ("/help one", "one"),
        ("/help   one   two", "one   two"),
        ("/help\tone\t two", "one\t two"),
    ],
)
def test_parse_slash_input_splits_command_and_arguments_once(
    text: str,
    expected_arguments: str,
):
    result = parse_slash_input(text)

    assert result.kind is SlashInputKind.COMMAND
    assert result.text == text.strip()
    assert result.command_name == "help"
    assert result.arguments == expected_arguments
