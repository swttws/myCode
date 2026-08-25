"""In-process role wakeup queues for Agent Team."""

from __future__ import annotations

import asyncio


class TeamEventNotifier:
    """In-process role wakeup queues; business messages remain in event storage."""

    def __init__(self) -> None:
        self._queues: dict[str, asyncio.Queue[None]] = {}

    def register_queue(self, role_name: str) -> asyncio.Queue[None]:
        if type(role_name) is not str or not role_name:
            raise ValueError("role_name must be a non-empty string")
        queue = self._queues.get(role_name)
        if queue is None:
            queue = asyncio.Queue(maxsize=1)
            self._queues[role_name] = queue
        return queue

    def unregister_queue(self, role_name: str) -> None:
        self._queues.pop(role_name, None)

    async def notify(self, role_name: str) -> bool:
        queue = self._queues.get(role_name)
        if queue is None:
            return False
        if queue.empty():
            queue.put_nowait(None)
        return True

    async def notify_many(self, role_names: tuple[str, ...]) -> tuple[str, ...]:
        notified = []
        for role_name in role_names:
            if await self.notify(role_name):
                notified.append(role_name)
        return tuple(notified)

    def queue_for(self, role_name: str) -> asyncio.Queue[None] | None:
        return self._queues.get(role_name)


__all__ = ["TeamEventNotifier"]
