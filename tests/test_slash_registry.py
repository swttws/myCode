from __future__ import annotations

import collections.abc
from dataclasses import FrozenInstanceError, MISSING, fields, is_dataclass
from typing import get_args, get_origin

import pytest


def import_slash():
    import mycode.slash as slash

    return slash


def assert_frozen_dataclass(model: type) -> None:
    assert is_dataclass(model)
    assert model.__dataclass_params__.frozen is True


def assert_field_defaults(model: type, expected: list[tuple[str, object]]) -> None:
    assert [(field.name, field.default) for field in fields(model)] == expected


def _annotation_name(value: object) -> str:
    return getattr(value, "__forward_arg__", getattr(value, "__name__", str(value)))


def test_slash_enum_values_are_fixed_by_stage_plan():
    slash = import_slash()

    assert issubclass(slash.SlashCommandType, str)
    assert [(member.name, member.value) for member in slash.SlashCommandType] == [
        ("LOCAL", "local"),
        ("UI_STATE", "ui_state"),
        ("PROMPT", "prompt"),
    ]
    assert issubclass(slash.SlashInputKind, str)
    assert [(member.name, member.value) for member in slash.SlashInputKind] == [
        ("EMPTY", "empty"),
        ("NORMAL", "normal"),
        ("COMMAND", "command"),
    ]
    assert issubclass(slash.SlashHandlerSignal, str)
    assert [(member.name, member.value) for member in slash.SlashHandlerSignal] == [
        ("CONTINUE", "continue"),
        ("EXIT", "exit"),
    ]
    assert issubclass(slash.SlashDispatchKind, str)
    assert [(member.name, member.value) for member in slash.SlashDispatchKind] == [
        ("EMPTY", "empty"),
        ("NOT_COMMAND", "not_command"),
        ("HANDLED", "handled"),
        ("EXIT", "exit"),
    ]
    assert issubclass(slash.SlashMode, str)
    assert [(member.name, member.value) for member in slash.SlashMode] == [
        ("DEFAULT", "default"),
        ("PLAN", "plan"),
    ]


def test_slash_models_are_frozen_dataclasses_with_expected_fields():
    slash = import_slash()

    for model in (
        slash.ParsedSlashInput,
        slash.SlashDispatchResult,
        slash.SlashCompletionCandidate,
        slash.SlashCommand,
        slash.SlashCommandContext,
    ):
        assert_frozen_dataclass(model)

    assert_field_defaults(
        slash.ParsedSlashInput,
        [
            ("kind", MISSING),
            ("text", MISSING),
            ("command_name", None),
            ("arguments", ""),
        ],
    )
    assert_field_defaults(
        slash.SlashDispatchResult,
        [
            ("kind", MISSING),
            ("normal_text", ""),
        ],
    )
    assert_field_defaults(
        slash.SlashCompletionCandidate,
        [
            ("text", MISSING),
            ("description", MISSING),
        ],
    )
    assert_field_defaults(
        slash.SlashCommand,
        [
            ("name", MISSING),
            ("aliases", MISSING),
            ("description", MISSING),
            ("usage", MISSING),
            ("command_type", MISSING),
            ("handler", MISSING),
            ("argument_hint", None),
            ("hidden", False),
        ],
    )
    assert_field_defaults(
        slash.SlashCommandContext,
        [
            ("controller", MISSING),
            ("registry", MISSING),
        ],
    )


def test_slash_command_handler_alias_matches_stage_contract():
    slash = import_slash()

    origin = get_origin(slash.SlashCommandHandler)
    args = get_args(slash.SlashCommandHandler)

    assert origin is collections.abc.Callable
    assert [_annotation_name(argument) for argument in args[0]] == [
        "SlashCommandContext",
        "str",
    ]
    assert get_origin(args[1]) is collections.abc.Awaitable
    assert get_args(args[1]) == (slash.SlashHandlerSignal,)


def test_slash_model_defaults_and_immutability():
    slash = import_slash()

    async def handler(context, arguments):
        return slash.SlashHandlerSignal.CONTINUE

    parsed = slash.ParsedSlashInput(slash.SlashInputKind.COMMAND, "/help")
    dispatch = slash.SlashDispatchResult(slash.SlashDispatchKind.NOT_COMMAND)
    candidate = slash.SlashCompletionCandidate("/help", "Show help.")
    command = slash.SlashCommand(
        name="help",
        aliases=("h",),
        description="Show help.",
        usage="/help",
        command_type=slash.SlashCommandType.LOCAL,
        handler=handler,
    )
    context = slash.SlashCommandContext(controller=object(), registry=object())

    assert parsed.command_name is None
    assert parsed.arguments == ""
    assert dispatch.normal_text == ""
    assert command.argument_hint is None
    assert command.hidden is False
    assert command.handler is handler

    for instance, attribute, value in (
        (parsed, "text", "/exit"),
        (dispatch, "normal_text", "hello"),
        (candidate, "description", "Changed."),
        (command, "hidden", True),
        (context, "controller", object()),
    ):
        with pytest.raises(FrozenInstanceError):
            setattr(instance, attribute, value)


def test_slash_package_exports_only_current_core_models():
    slash = import_slash()

    expected_exports = {
        "ParsedSlashInput",
        "SlashCommand",
        "SlashCommandContext",
        "SlashCommandHandler",
        "SlashCommandType",
        "SlashCompletionCandidate",
        "SlashDispatchKind",
        "SlashDispatchResult",
        "SlashHandlerSignal",
        "SlashInputKind",
        "SlashMode",
    }

    assert set(slash.__all__) == expected_exports
    for name in expected_exports:
        assert hasattr(slash, name)
    assert not hasattr(slash, "SlashCommandController")
    assert not hasattr(slash, "SlashCommandRegistry")
