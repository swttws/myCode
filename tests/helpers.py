from pathlib import Path

import httpx

from mycode.compact.models import (
    CompactAction,
    CompactReport,
    CompactStatus,
    PreparedContext,
    RequestSnapshot,
    TokenEstimate,
)
from mycode.workspace import WorkspaceContext, WorkspaceKind, WorkspaceTaskIdentity


async def collect_async(async_iterable):
    return [item async for item in async_iterable]


def shared_workspace(root: str | Path) -> WorkspaceContext:
    resolved = Path(root).resolve()
    return WorkspaceContext(
        kind=WorkspaceKind.SHARED,
        root=resolved,
        repository_root=resolved,
        repository_id="test-repository",
        task_identity=None,
        branch_name=None,
        hooks_path=None,
    )


def worktree_workspace(
    root: str | Path,
    *,
    token: str = "task-000001",
    hooks_path: str | Path | None = None,
) -> WorkspaceContext:
    resolved = Path(root).resolve()
    identity = WorkspaceTaskIdentity(
        repository_id="test-repository",
        task_id="agent-000001",
        role_name="review",
        task_token=token,
        relative_name=f"review/{token}",
        branch_name=f"mycode/worktree/review/{token}",
        base_commit="a" * 40,
    )
    return WorkspaceContext(
        kind=WorkspaceKind.WORKTREE,
        root=resolved,
        repository_root=resolved.parent,
        repository_id=identity.repository_id,
        task_identity=identity,
        branch_name=identity.branch_name,
        hooks_path=Path(hooks_path).resolve() if hooks_path is not None else None,
    )


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
        self.prepared_contexts = []
        self.record_usage_calls = []
        self.clear_calls = 0
        self.close_calls = 0

    async def prepare_auto(self, *, build_request, run_deadline):
        self.prepare_calls.append({"run_deadline": run_deadline})
        request = build_request(tuple(self.memory.messages()))
        prepared = PreparedContext(
            request=request,
            snapshot=RequestSnapshot(ascii_chars=0, non_ascii_chars=0, fingerprint="test"),
            estimate=TokenEstimate(tokens=0, source="full_chars", delta_tokens=0),
            report=self.report,
        )
        self.prepared_contexts.append(prepared)
        return prepared

    def record_usage(self, snapshot, usage):
        self.record_usage_calls.append((snapshot, usage))

    def clear(self):
        self.clear_calls += 1
        self.memory.clear()

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
