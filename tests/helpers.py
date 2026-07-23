import httpx

from mycode.compact.models import (
    CompactAction,
    CompactReport,
    CompactStatus,
    PreparedContext,
    RequestSnapshot,
    TokenEstimate,
)


async def collect_async(async_iterable):
    return [item async for item in async_iterable]


def _default_compact_report() -> CompactReport:
    return CompactReport(
        status=CompactStatus.SAFE,
        actions=(CompactAction.NONE,),
        before_tokens=0,
        after_tokens=0,
        archived_count=0,
        attempts=0,
        circuit_open=False,
    )


class PassthroughContextManager:
    def __init__(self, memory, *, report: CompactReport | None = None) -> None:
        self.memory = memory
        self.report = report or _default_compact_report()
        self.prepare_calls = []
        self.record_usage_calls = []
        self.clear_calls = 0
        self.close_calls = 0

    async def prepare_auto(self, *, build_request, run_deadline):
        self.prepare_calls.append({"run_deadline": run_deadline})
        request = build_request(tuple(self.memory.messages()))
        return PreparedContext(
            request=request,
            snapshot=RequestSnapshot(ascii_chars=0, non_ascii_chars=0, fingerprint="test"),
            estimate=TokenEstimate(tokens=0, source="full_chars", delta_tokens=0),
            report=self.report,
        )

    def record_usage(self, snapshot, usage):
        self.record_usage_calls.append((snapshot, usage))

    def clear(self):
        self.clear_calls += 1

    def close(self):
        self.close_calls += 1


class ControlledAsyncByteStream(httpx.AsyncByteStream):
    def __init__(self, first_chunk: bytes, remaining_chunks: list[bytes]):
        self._first_chunk = first_chunk
        self._remaining_chunks = remaining_chunks
        self.first_chunk_sent = None
        self.release_remaining = None

    async def __aiter__(self):
        import asyncio

        self.first_chunk_sent = asyncio.Event()
        self.release_remaining = asyncio.Event()
        yield self._first_chunk
        self.first_chunk_sent.set()
        await self.release_remaining.wait()
        for chunk in self._remaining_chunks:
            yield chunk

    async def aclose(self):
        return None
