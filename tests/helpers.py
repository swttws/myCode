import httpx


async def collect_async(async_iterable):
    return [item async for item in async_iterable]


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
