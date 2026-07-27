from __future__ import annotations

from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.document import Document

from mycode.slash.registry import SlashCommandRegistry


class SlashCommandCompleter(Completer):
    def __init__(self, registry: SlashCommandRegistry, *, before_complete=None) -> None:
        self._registry = registry
        self._before_complete = before_complete

    def get_completions(self, document: Document, complete_event):
        del complete_event
        text_before_cursor = document.text_before_cursor
        if not text_before_cursor.startswith("/"):
            return
        if any(character.isspace() for character in text_before_cursor):
            return

        if self._before_complete is not None:
            self._before_complete()

        start_position = -len(text_before_cursor)
        for candidate in self._registry.completion_candidates(text_before_cursor):
            yield Completion(
                candidate.text,
                start_position=start_position,
                display_meta=candidate.description,
            )
