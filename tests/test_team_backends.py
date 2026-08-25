from __future__ import annotations

import asyncio
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import pytest

from mycode.team.domain.models import (
    BackendHandle,
    BackendEnvironment,
    BackendSelection,
    MemberLaunchSpec,
    MemberBackend,
    ResolvedBackend,
    WakeEndpoint,
)


NOW = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)


@dataclass
class FakeCapabilityProbe:
    available: dict[ResolvedBackend, bool]
    calls: list[ResolvedBackend]

    def __call__(self, backend: ResolvedBackend, environment: BackendEnvironment) -> bool:
        self.calls.append(backend)
        return self.available.get(backend, False)


def make_environment(
    root: Path,
    *,
    requested_backend: MemberBackend = MemberBackend.AUTO,
    tmux_available: bool = False,
    terminal_available: bool = False,
    in_process_available: bool = False,
) -> BackendEnvironment:
    return BackendEnvironment(
        requested_backend=requested_backend,
        platform="linux",
        shell_name="bash",
        tmux_available=tmux_available,
        terminal_available=terminal_available,
        in_process_available=in_process_available,
        coordinator_enabled=False,
        workspace_root=root / "workspace",
        repository_root=root / "workspace" / "repo",
        member_name="dev",
    )


def test_backend_selector_auto_tries_in_process_before_external_backends(tmp_path: Path):
    from mycode.team.execution.backends import BackendSelector

    probe = FakeCapabilityProbe(
        available={
            ResolvedBackend.TMUX: False,
            ResolvedBackend.WINDOWS_TERMINAL: True,
            ResolvedBackend.IN_PROCESS: True,
        },
        calls=[],
    )
    selector = BackendSelector(capability_probe=probe)

    selection = selector.select(MemberBackend.AUTO, make_environment(tmp_path))

    assert probe.calls == [ResolvedBackend.IN_PROCESS]
    assert selection == BackendSelection(
        requested_backend=MemberBackend.AUTO,
        resolved_backend=ResolvedBackend.IN_PROCESS,
        available=True,
        reason_code="backend_available",
        reason="selected in_process",
        environment=make_environment(tmp_path),
        fallback_chain=(ResolvedBackend.IN_PROCESS,),
    )


def test_backend_selector_auto_falls_back_to_in_process_when_native_backends_are_unavailable(
    tmp_path: Path,
):
    from mycode.team.execution.backends import BackendSelector

    probe = FakeCapabilityProbe(
        available={
            ResolvedBackend.TMUX: False,
            ResolvedBackend.WINDOWS_TERMINAL: False,
            ResolvedBackend.IN_PROCESS: True,
        },
        calls=[],
    )
    selector = BackendSelector(capability_probe=probe)

    selection = selector.select(MemberBackend.AUTO, make_environment(tmp_path))

    assert probe.calls == [ResolvedBackend.IN_PROCESS]
    assert selection.resolved_backend is ResolvedBackend.IN_PROCESS
    assert selection.available is True
    assert selection.fallback_chain == (
        ResolvedBackend.IN_PROCESS,
    )


def test_backend_selector_auto_honors_configured_priority(tmp_path: Path):
    from mycode.team.execution.backends import BackendSelector

    probe = FakeCapabilityProbe(
        available={
            ResolvedBackend.TMUX: True,
            ResolvedBackend.IN_PROCESS: True,
        },
        calls=[],
    )
    selector = BackendSelector(capability_probe=probe)

    selection = selector.select(
        MemberBackend.AUTO,
        make_environment(tmp_path),
        priority=(MemberBackend.IN_PROCESS, MemberBackend.TMUX),
    )

    assert probe.calls == [ResolvedBackend.IN_PROCESS]
    assert selection.resolved_backend is ResolvedBackend.IN_PROCESS
    assert selection.fallback_chain == (ResolvedBackend.IN_PROCESS,)


def test_backend_router_dispatches_by_resolved_backend_and_handle_endpoint(tmp_path: Path):
    from mycode.team.execution.backends import BackendRouter

    class RecordingBackend:
        def __init__(self, backend: ResolvedBackend) -> None:
            self.backend = backend
            self.started = []
            self.woken = []
            self.stopped = []

        async def start(self, spec):
            self.started.append(spec)
            return BackendHandle(
                wake_endpoint=spec.wake_endpoint,
                process_id=123,
                started_at=NOW,
                token=f"{self.backend.value}-token",
            )

        async def wake(self, handle):
            self.woken.append(handle)

        async def stop(self, handle, *, force: bool):
            self.stopped.append((handle, force))

    tmux = RecordingBackend(ResolvedBackend.TMUX)
    in_process = RecordingBackend(ResolvedBackend.IN_PROCESS)
    router = BackendRouter(
        {
            ResolvedBackend.TMUX: tmux,
            ResolvedBackend.IN_PROCESS: in_process,
        }
    )

    async def scenario():
        spec = make_launch_spec(tmp_path)

        handle = await router.start(spec)
        await router.wake(handle)
        await router.stop(handle, force=True)

        assert in_process.started == [spec]
        assert in_process.woken == [handle]
        assert in_process.stopped == [(handle, True)]
        assert tmux.started == []

    asyncio.run(scenario())


def test_backend_selector_explicit_unavailable_backend_fails_closed(tmp_path: Path):
    from mycode.team.execution.backends import BackendSelector

    probe = FakeCapabilityProbe(
        available={
            ResolvedBackend.TMUX: False,
            ResolvedBackend.WINDOWS_TERMINAL: True,
            ResolvedBackend.IN_PROCESS: True,
        },
        calls=[],
    )
    selector = BackendSelector(capability_probe=probe)

    selection = selector.select(
        MemberBackend.TMUX,
        make_environment(tmp_path, requested_backend=MemberBackend.TMUX),
    )

    assert probe.calls == [ResolvedBackend.TMUX]
    assert selection == BackendSelection(
        requested_backend=MemberBackend.TMUX,
        resolved_backend=None,
        available=False,
        reason_code="backend_unavailable",
        reason="requested tmux backend is unavailable",
        environment=make_environment(tmp_path, requested_backend=MemberBackend.TMUX),
        fallback_chain=(),
    )


def test_backend_selector_explicit_available_backend_does_not_probe_fallbacks(
    tmp_path: Path,
):
    from mycode.team.execution.backends import BackendSelector

    probe = FakeCapabilityProbe(
        available={
            ResolvedBackend.TMUX: True,
            ResolvedBackend.WINDOWS_TERMINAL: True,
            ResolvedBackend.IN_PROCESS: False,
        },
        calls=[],
    )
    selector = BackendSelector(capability_probe=probe)

    selection = selector.select(
        MemberBackend.TERMINAL,
        make_environment(
            tmp_path,
            requested_backend=MemberBackend.TERMINAL,
        ),
    )

    assert probe.calls == [ResolvedBackend.WINDOWS_TERMINAL]
    assert selection == BackendSelection(
        requested_backend=MemberBackend.TERMINAL,
        resolved_backend=ResolvedBackend.WINDOWS_TERMINAL,
        available=True,
        reason_code="backend_available",
        reason="selected windows_terminal",
        environment=make_environment(
            tmp_path,
            requested_backend=MemberBackend.TERMINAL,
        ),
        fallback_chain=(ResolvedBackend.WINDOWS_TERMINAL,),
    )


def test_backend_selector_rejects_invalid_probe_result(tmp_path: Path):
    from mycode.team.execution.backends import BackendSelector

    class BrokenProbe:
        def __call__(self, backend: ResolvedBackend, environment: BackendEnvironment) -> bool:
            return 1  # type: ignore[return-value]

    selector = BackendSelector(capability_probe=BrokenProbe())

    with pytest.raises(ValueError, match="capability probe"):
        selector.select(MemberBackend.AUTO, make_environment(tmp_path))


def make_launch_spec(root: Path, *, backend: ResolvedBackend = ResolvedBackend.IN_PROCESS) -> MemberLaunchSpec:
    workspace = root / "workspace"
    workspace.mkdir()
    endpoint = WakeEndpoint(
        member_name="dev",
        backend=backend,
        endpoint=f"{backend.value}:dev",
        revision=1,
    )
    return MemberLaunchSpec(
        team_name="team-a",
        member_name="dev",
        role_name="general",
        role_revision=1,
        requested_backend=MemberBackend.IN_PROCESS,
        resolved_backend=backend,
        argv=(sys.executable, "-m", "mycode", "--team-worker", "team-a/dev"),
        environment={
            "MYCODE_TEAM": "team-a",
            "MYCODE_TEAM_MEMBER": "dev",
            "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src"),
        },
        workspace_root=workspace,
        repository_root=workspace,
        repository_id="repo-123",
        branch_name="mycode/team/team-a/dev",
        context_path=root / "context.json",
        wake_endpoint=endpoint,
        task_id="task-1",
        batch_id="batch-1",
        goal="ship",
        approval_required=False,
        read_only=False,
        revision=1,
    )


class FakeRuntime:
    def __init__(self) -> None:
        self.run_count = 0
        self.stop_count = 0
        self._stop = asyncio.Event()

    async def run_event_consumer(self):
        self.run_count += 1
        await self._stop.wait()

    async def stop_consumer(self):
        self.stop_count += 1
        self._stop.set()


class BlockingRuntime:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.run_count = 0
        self.active = 0
        self.max_active = 0
        self._stop = asyncio.Event()

    async def run_event_consumer(self):
        self.run_count += 1
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        self.started.set()
        await self.release.wait()
        self.active -= 1
        await self._stop.wait()

    async def stop_consumer(self):
        self.release.set()
        self._stop.set()


def test_in_process_backend_runs_member_runtime_and_wakes_existing_handle(tmp_path: Path):
    from mycode.team.execution.backends import InProcessBackend

    async def scenario():
        runtime = FakeRuntime()
        backend = InProcessBackend(
            runtime_factory=lambda spec: runtime,
            clock=lambda: NOW,
            token_factory=lambda: "token-1",
        )
        spec = make_launch_spec(tmp_path)

        handle = await backend.start(spec)
        await asyncio.sleep(0)
        await backend.wake(handle)
        await asyncio.sleep(0)
        await backend.stop(handle, force=False)

        assert handle.wake_endpoint == spec.wake_endpoint
        assert handle.process_id > 0
        assert handle.started_at == NOW
        assert handle.token == "token-1"
        assert runtime.run_count == 1
        assert runtime.stop_count == 1

    asyncio.run(scenario())


class FakeProcess:
    def __init__(self, pid: int = 1234) -> None:
        self.pid = pid
        self.terminated = False
        self.killed = False

    def poll(self):
        return None

    def terminate(self):
        self.terminated = True

    def kill(self):
        self.killed = True


def test_tmux_backend_reports_event_driven_unsupported(tmp_path: Path):
    """tmux backend start raises event_driven_unsupported error."""
    from mycode.team.execution.backends import TmuxBackend
    from mycode.team.domain.models import TeamError

    async def scenario():
        backend = TmuxBackend(
            clock=lambda: NOW,
            token_factory=lambda: "tmux-token",
        )
        spec = make_launch_spec(tmp_path, backend=ResolvedBackend.TMUX)

        with pytest.raises(TeamError) as exc_info:
            await backend.start(spec)
        assert exc_info.value.code == "event_driven_unsupported"

    asyncio.run(scenario())


def test_terminal_backend_reports_event_driven_unsupported(tmp_path: Path):
    """windows terminal backend start raises event_driven_unsupported error."""
    from mycode.team.execution.backends import WindowsTerminalBackend
    from mycode.team.domain.models import TeamError

    async def scenario():
        backend = WindowsTerminalBackend(
            clock=lambda: NOW,
            token_factory=lambda: "terminal-token",
        )
        spec = make_launch_spec(tmp_path, backend=ResolvedBackend.WINDOWS_TERMINAL)

        with pytest.raises(TeamError) as exc_info:
            await backend.start(spec)
        assert exc_info.value.code == "event_driven_unsupported"

    asyncio.run(scenario())


def test_in_process_backend_does_not_drop_wake_during_runtime(tmp_path: Path):
    from mycode.team.execution.backends import InProcessBackend

    async def scenario():
        runtime = BlockingRuntime()
        backend = InProcessBackend(runtime_factory=lambda spec: runtime, token_factory=lambda: "token-1")
        spec = make_launch_spec(tmp_path)

        handle = await backend.start(spec)
        await runtime.started.wait()
        await backend.wake(handle)
        runtime.release.set()
        await asyncio.sleep(0)
        await asyncio.sleep(0)

        assert runtime.run_count == 1
        assert runtime.max_active == 1
        await backend.stop(handle, force=True)

    asyncio.run(scenario())


def test_in_process_backend_coalesces_wakes_without_concurrent_runtime(tmp_path: Path):
    from mycode.team.execution.backends import InProcessBackend

    async def scenario():
        runtime = BlockingRuntime()
        backend = InProcessBackend(runtime_factory=lambda spec: runtime, token_factory=lambda: "token-1")
        spec = make_launch_spec(tmp_path)

        handle = await backend.start(spec)
        await runtime.started.wait()
        await asyncio.gather(*(backend.wake(handle) for _ in range(5)))
        runtime.release.set()
        await asyncio.sleep(0)
        await asyncio.sleep(0)

        assert runtime.run_count == 1
        assert runtime.max_active == 1
        await backend.stop(handle, force=True)

    asyncio.run(scenario())


def test_in_process_backend_wake_notifies_injected_notifier(tmp_path: Path):
    """wake delivers a deduplicated signal to the injected notifier queue."""
    from mycode.team.execution.backends import InProcessBackend
    from mycode.team.execution.notifier import TeamEventNotifier

    async def scenario():
        runtime = BlockingRuntime()
        notifier = TeamEventNotifier()
        backend = InProcessBackend(
            runtime_factory=lambda spec: runtime,
            notifier=notifier,
            token_factory=lambda: "token-1",
        )
        spec = make_launch_spec(tmp_path)
        queue = notifier.register_queue(spec.wake_endpoint.member_name)

        handle = await backend.start(spec)
        await runtime.started.wait()
        await backend.wake(handle)
        assert queue.qsize() == 1
        await backend.wake(handle)
        assert queue.qsize() == 1
        runtime.release.set()
        await backend.stop(handle, force=True)

    asyncio.run(scenario())