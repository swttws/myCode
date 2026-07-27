from __future__ import annotations

from prompt_toolkit.document import Document

from mycode.slash.completion import SlashCommandCompleter
from mycode.slash.builtins import create_default_slash_registry


def _texts_and_meta(completions):
    return [
        (
            completion.text,
            completion.start_position,
            getattr(completion, "display_meta_text", ""),
        )
        for completion in completions
    ]


def test_slash_completion_matches_partial_and_casefolded_commands():
    registry = create_default_slash_registry()
    completer = SlashCommandCompleter(registry)
    status_description = registry.resolve("status").description

    completions = list(
        completer.get_completions(Document(text="/sta", cursor_position=4), None)
    )
    assert _texts_and_meta(completions) == [
        ("/status", -4, status_description),
        ("/stat", -4, status_description),
    ]

    completions = list(
        completer.get_completions(Document(text="/STA", cursor_position=4), None)
    )
    assert [completion.text for completion in completions] == ["/status", "/stat"]


def test_slash_completion_keeps_registry_order_and_hides_exit():
    completer = SlashCommandCompleter(create_default_slash_registry())

    completions = list(completer.get_completions(Document(text="/c", cursor_position=2), None))
    assert [completion.text for completion in completions] == [
        "/compact",
        "/comp",
        "/clear",
        "/cls",
    ]
    assert all(completion.start_position == -2 for completion in completions)

    exit_completions = list(
        completer.get_completions(Document(text="/ex", cursor_position=3), None)
    )
    quit_completions = list(
        completer.get_completions(Document(text="/quit", cursor_position=5), None)
    )
    assert exit_completions == []
    assert quit_completions == []


def test_slash_completion_ignores_arguments_and_plain_text():
    completer = SlashCommandCompleter(create_default_slash_registry())

    assert list(completer.get_completions(Document(text="plain", cursor_position=5), None)) == []
    assert list(
        completer.get_completions(Document(text="/status ", cursor_position=8), None)
    ) == []
    assert list(
        completer.get_completions(Document(text="/permission d", cursor_position=13), None)
    ) == []
