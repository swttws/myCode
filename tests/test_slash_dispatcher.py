from __future__ import annotations

import asyncio
import logging

import pytest

from mycode.slash.dispatcher import SlashCommandDispatcher
from mycode.slash.models import (
    SlashCommand,
    SlashCommandType,
    SlashDispatchKind,
    SlashDispatchResult,
    SlashHandlerSignal,
)
from mycode.slash.registry import SlashCommandRegistry


class RecordingController:
    def __init__(self) -> None:
        self.messages: list[tuple[str, bool]] = []
        self.sent_messages: list[str] = []

    def show_message(self, text: str, *, error: bool = False) -> None:
        self.messages.append((text, error))

    async def send_user_message(self, text: str) -> None:
        self.sent_messages.append(text)


def _command(
    handler,
    *,
    name: str = "help",
    aliases: tuple[str, ...] = (),
) -> SlashCommand:
    return SlashCommand(
        name=name,
        aliases=aliases,
        description=f"{name} description",
        usage=f"/{name}",
        command_type=SlashCommandType.LOCAL,
        handler=handler,
    )


def test_dispatch_empty_input_returns_empty_without_invoking_handlers():
    called = []

    async def handler(context, arguments):
        called.append((context, arguments))
        return SlashHandlerSignal.CONTINUE

    registry = SlashCommandRegistry([_command(handler)])
    dispatcher = SlashCommandDispatcher(registry)

    result = asyncio.run(dispatcher.dispatch("  \t  ", object()))

    assert result == SlashDispatchResult(kind=SlashDispatchKind.EMPTY)
    assert called == []


def test_dispatch_normal_input_returns_normal_text_without_invoking_handlers():
    called = []

    async def handler(context, arguments):
        called.append((context, arguments))
        return SlashHandlerSignal.CONTINUE

    registry = SlashCommandRegistry([_command(handler)])
    dispatcher = SlashCommandDispatcher(registry)

    result = asyncio.run(dispatcher.dispatch("  hello   world  ", object()))

    assert result == SlashDispatchResult(
        kind=SlashDispatchKind.NOT_COMMAND,
        normal_text="hello   world",
    )
    assert called == []


def test_dispatch_known_command_variants_call_the_handler_with_arguments():
    recorded = {}

    async def handler(context, arguments):
        recorded["context"] = context
        recorded["arguments"] = arguments
        return SlashHandlerSignal.CONTINUE

    command = _command(handler, aliases=("h",))
    registry = SlashCommandRegistry([command])
    dispatcher = SlashCommandDispatcher(registry)

    for text in ("/help one two", "/h one two", "  /HeLp one two  "):
        recorded.clear()

        result = asyncio.run(dispatcher.dispatch(text, object()))

        assert result == SlashDispatchResult(kind=SlashDispatchKind.HANDLED)
        assert recorded["arguments"] == "one two"
        assert recorded["context"].registry is registry


def test_dispatch_exit_handler_returns_exit():
    recorded = {}

    async def handler(context, arguments):
        recorded["context"] = context
        recorded["arguments"] = arguments
        return SlashHandlerSignal.EXIT

    registry = SlashCommandRegistry([_command(handler, name="exit", aliases=("quit",))])
    dispatcher = SlashCommandDispatcher(registry)

    result = asyncio.run(dispatcher.dispatch("/quit", object()))

    assert result == SlashDispatchResult(kind=SlashDispatchKind.EXIT)
    assert recorded["arguments"] == ""


def test_dispatch_unknown_command_shows_help_and_does_not_send_user_message():
    called = []

    async def handler(context, arguments):
        called.append((context, arguments))
        return SlashHandlerSignal.CONTINUE

    registry = SlashCommandRegistry([_command(handler)])
    dispatcher = SlashCommandDispatcher(registry)
    controller = RecordingController()

    result = asyncio.run(dispatcher.dispatch("/missing value", controller))

    assert result == SlashDispatchResult(kind=SlashDispatchKind.HANDLED)
    assert called == []
    assert controller.sent_messages == []
    assert len(controller.messages) == 1
    text, is_error = controller.messages[0]
    assert "/missing" in text
    assert "/help" in text
    assert is_error is True


def test_dispatch_handler_exception_reports_stable_error_and_keeps_dispatching(
    caplog: pytest.LogCaptureFixture,
):
    recorded = {}

    async def boom_handler(context, arguments):
        raise RuntimeError("mock secret token")

    async def ok_handler(context, arguments):
        recorded["context"] = context
        recorded["arguments"] = arguments
        return SlashHandlerSignal.CONTINUE

    registry = SlashCommandRegistry(
        [
            _command(boom_handler, name="help"),
            _command(ok_handler, name="status"),
        ]
    )
    dispatcher = SlashCommandDispatcher(registry)
    controller = RecordingController()

    with caplog.at_level(logging.ERROR, logger="mycode.slash.dispatcher"):
        result = asyncio.run(dispatcher.dispatch("/help secret", controller))

    assert result == SlashDispatchResult(kind=SlashDispatchKind.HANDLED)
    assert controller.sent_messages == []
    assert len(controller.messages) == 1
    text, is_error = controller.messages[0]
    assert "slash_command_failed" in text
    assert "help" in text
    assert "secret" not in text
    assert is_error is True
    assert "help" in caplog.text
    assert "Traceback" in caplog.text
    assert "RuntimeError" in caplog.text

    result = asyncio.run(dispatcher.dispatch("/status ready", controller))

    assert result == SlashDispatchResult(kind=SlashDispatchKind.HANDLED)
    assert recorded["arguments"] == "ready"
    assert recorded["context"].registry is registry
