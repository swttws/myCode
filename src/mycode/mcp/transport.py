from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Mapping, Protocol


class MCPTransportError(RuntimeError):
    def __init__(self, category: str, message: str) -> None:
        super().__init__(message)
        self.category = category


class MCPTransport(Protocol):
    async def open(self) -> None:
        raise NotImplementedError

    async def send(self, message: Mapping[str, object]) -> None:
        raise NotImplementedError

    def receive(self) -> AsyncIterator[dict[str, object]]:
        raise NotImplementedError

    async def close(self) -> None:
        raise NotImplementedError
