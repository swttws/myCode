"""Serial event consumer for Lead and member roles."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

from ..domain import EventFailure, TeamEvent
from ..infrastructure.events import TeamEventStore
from .notifier import TeamEventNotifier

logger = logging.getLogger("mycode.team.consumer")

EventHandler = Callable[[TeamEvent], Awaitable[None]]
FailureHandler = Callable[[EventFailure], Awaitable[None]]


class RoleEventConsumer:
    def __init__(
        self,
        role_name: str,
        *,
        events: TeamEventStore,
        notifier: TeamEventNotifier,
        handler: EventHandler,
        on_terminal_failure: FailureHandler | None = None,
    ) -> None:
        self.role_name = role_name
        self._events = events
        self._notifier = notifier
        self._handler = handler
        self._on_terminal_failure = on_terminal_failure
        self._queue = notifier.register_queue(role_name)
        self._stop = asyncio.Event()

    async def run(self) -> None:
        while not self._stop.is_set():
            await self._queue.get()
            while not self._stop.is_set():
                event = self._events.next_event(self.role_name)
                if event is None:
                    break
                await self._process_event(event)

    async def run_until_idle(self) -> None:
        """Process all currently pending events and return."""
        while not self._stop.is_set():
            event = self._events.next_event(self.role_name)
            if event is None:
                break
            await self._process_event(event)

    async def _process_event(self, event: TeamEvent) -> None:
        try:
            self._events.begin_event(self.role_name, event.event_id)
            await self._handler(event)
        except Exception as exc:
            await self._handle_failure(event, exc, reason_code="handler_error")
            return

        try:
            self._events.ack_event(self.role_name, event.event_id)
        except Exception as exc:
            await self._handle_failure(event, exc, reason_code="ack_error")

    async def _handle_failure(self, event: TeamEvent, exc: Exception, *, reason_code: str) -> None:
        failure = self._events.fail_event(
            self.role_name,
            event.event_id,
            str(exc) or type(exc).__name__,
            reason_code=reason_code,
        )
        if failure is None:
            await self._notifier.notify(self.role_name)
        elif self._on_terminal_failure is not None:
            try:
                await self._on_terminal_failure(failure)
            except Exception:
                logger.exception(
                    "team.consumer.terminal_failure_handler_failed",
                    extra={"role_name": self.role_name, "event_id": event.event_id},
                )
        logger.error(
            "team.consumer.event.failed",
            exc_info=(type(exc), exc, exc.__traceback__),
            extra={"role_name": self.role_name, "event_id": event.event_id, "reason_code": reason_code},
        )

    async def stop(self) -> None:
        self._stop.set()
        await self._notifier.notify(self.role_name)


__all__ = ["EventHandler", "FailureHandler", "RoleEventConsumer"]
