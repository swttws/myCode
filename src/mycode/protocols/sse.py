from __future__ import annotations

from dataclasses import dataclass
from typing import AsyncIterable, Iterable


@dataclass(frozen=True)
class SSEEvent:
    event: str | None
    data: str


def parse_sse_events(lines: Iterable[str | bytes]) -> Iterable[SSEEvent]:
    event_name: str | None = None
    data_lines: list[str] = []

    for raw_line in lines:
        line = _decode_line(raw_line)
        if line == "":
            if data_lines:
                yield SSEEvent(event=event_name, data="\n".join(data_lines))
            event_name = None
            data_lines = []
            continue

        if line.startswith(":"):
            continue

        field, _, value = line.partition(":")
        if value.startswith(" "):
            value = value[1:]

        if field == "event":
            event_name = value
        elif field == "data":
            data_lines.append(value)

    # 有些测试流或代理不会补最后一个空行，这里仍然产出最后事件。
    if data_lines:
        yield SSEEvent(event=event_name, data="\n".join(data_lines))


async def parse_sse_events_async(lines: AsyncIterable[str | bytes]) -> AsyncIterable[SSEEvent]:
    event_name: str | None = None
    data_lines: list[str] = []

    # HTTP 流来自异步客户端，SSE parser 也提供异步入口，避免阻塞 LLM 调用链路。
    async for raw_line in lines:
        line = _decode_line(raw_line)
        if line == "":
            if data_lines:
                yield SSEEvent(event=event_name, data="\n".join(data_lines))
            event_name = None
            data_lines = []
            continue

        if line.startswith(":"):
            continue

        field, _, value = line.partition(":")
        if value.startswith(" "):
            value = value[1:]

        if field == "event":
            event_name = value
        elif field == "data":
            data_lines.append(value)

    if data_lines:
        yield SSEEvent(event=event_name, data="\n".join(data_lines))


def _decode_line(raw_line: str | bytes) -> str:
    if isinstance(raw_line, bytes):
        raw_line = raw_line.decode("utf-8")
    return raw_line.rstrip("\r\n")
