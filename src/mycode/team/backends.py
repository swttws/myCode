from __future__ import annotations

import asyncio
import os
import subprocess
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

from mycode.team.models import (
    BackendHandle,
    BackendEnvironment,
    BackendSelection,
    MemberBackend,
    MemberLaunchSpec,
    ResolvedBackend,
    TeamError,
)


AUTO_BACKEND_ORDER: tuple[ResolvedBackend, ...] = (
    ResolvedBackend.TMUX,
    ResolvedBackend.WINDOWS_TERMINAL,
    ResolvedBackend.IN_PROCESS,
)


class BackendCapabilityProbe(Protocol):
    def __call__(
        self,
        backend: ResolvedBackend,
        environment: BackendEnvironment,
    ) -> bool: ...


class BackendSelector:
    def __init__(
        self,
        *,
        capability_probe: BackendCapabilityProbe | None = None,
    ) -> None:
        self._capability_probe = capability_probe or _default_capability_probe

    def select(
        self,
        requested_backend: MemberBackend,
        environment: BackendEnvironment,
        *,
        priority: tuple[MemberBackend, ...] | None = None,
    ) -> BackendSelection:
        if not isinstance(requested_backend, MemberBackend):
            raise ValueError("requested_backend must be a MemberBackend")
        if not isinstance(environment, BackendEnvironment):
            raise ValueError("environment must be a BackendEnvironment")
        if environment.requested_backend is not requested_backend:
            raise ValueError("environment requested_backend must match requested_backend")

        candidates = _candidate_backends(requested_backend, priority=priority)
        attempted: list[ResolvedBackend] = []
        for candidate in candidates:
            attempted.append(candidate)
            if self._is_available(candidate, environment):
                reason = _available_reason(requested_backend, candidate, attempted)
                return BackendSelection(
                    requested_backend=requested_backend,
                    resolved_backend=candidate,
                    available=True,
                    reason_code="backend_available",
                    reason=reason,
                    environment=environment,
                    fallback_chain=tuple(attempted),
                )

        reason = _unavailable_reason(requested_backend)
        fallback_chain = tuple(attempted) if requested_backend is MemberBackend.AUTO else ()
        return BackendSelection(
            requested_backend=requested_backend,
            resolved_backend=None,
            available=False,
            reason_code="backend_unavailable",
            reason=reason,
            environment=environment,
            fallback_chain=fallback_chain,
        )

    def _is_available(
        self,
        backend: ResolvedBackend,
        environment: BackendEnvironment,
    ) -> bool:
        available = self._capability_probe(backend, environment)
        if type(available) is not bool:
            raise ValueError("capability probe must return a bool")
        return available


@dataclass
class _RuntimeState:
    runtime: object
    task: asyncio.Task | None


@dataclass
class _ProcessState:
    spec: MemberLaunchSpec
    process: object


class BackendRouter:
    def __init__(self, backends: Mapping[ResolvedBackend, object]) -> None:
        self._backends = dict(backends)

    async def start(self, spec: MemberLaunchSpec) -> BackendHandle:
        return await _maybe_await(self._backend(spec.resolved_backend).start(spec))

    async def wake(self, handle: BackendHandle) -> None:
        await _maybe_await(self._backend(handle.wake_endpoint.backend).wake(handle))

    async def stop(self, handle: BackendHandle, *, force: bool) -> None:
        await _maybe_await(self._backend(handle.wake_endpoint.backend).stop(handle, force=force))

    def _backend(self, resolved_backend: ResolvedBackend):
        backend = self._backends.get(resolved_backend)
        if backend is None:
            raise TeamError(
                code="backend_unavailable",
                phase="backend",
                message=f"backend is not configured: {resolved_backend.value}",
            )
        return backend


class InProcessBackend:
    def __init__(
        self,
        *,
        runtime_factory=None,
        clock=None,
        token_factory=None,
    ) -> None:
        self._runtime_factory = runtime_factory or _missing_runtime_factory
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._token_factory = token_factory or (lambda: uuid.uuid4().hex)
        self._runtimes: dict[str, _RuntimeState] = {}

    async def start(self, spec: MemberLaunchSpec) -> BackendHandle:
        _require_launch_spec(spec, ResolvedBackend.IN_PROCESS)
        runtime = self._runtime_factory(spec)
        token = self._token_factory()
        handle = BackendHandle(
            wake_endpoint=spec.wake_endpoint,
            process_id=os.getpid(),
            started_at=self._clock(),
            token=token,
        )
        self._runtimes[token] = _RuntimeState(
            runtime=runtime,
            task=asyncio.create_task(_run_until_idle(runtime)),
        )
        return handle

    async def wake(self, handle: BackendHandle) -> None:
        state = self._runtime_state(handle)
        if state.task is None or state.task.done():
            state.task = asyncio.create_task(_run_until_idle(state.runtime))

    async def stop(self, handle: BackendHandle, *, force: bool) -> None:
        state = self._runtime_state(handle)
        if force and state.task is not None and not state.task.done():
            state.task.cancel()
            await asyncio.gather(state.task, return_exceptions=True)
        elif not force:
            graceful_stop = getattr(state.runtime, "graceful_stop", None)
            if callable(graceful_stop):
                result = graceful_stop()
                if asyncio.iscoroutine(result):
                    await result
        self._runtimes.pop(handle.token, None)

    def _runtime_state(self, handle: BackendHandle) -> _RuntimeState:
        state = self._runtimes.get(handle.token)
        if state is None:
            raise TeamError(
                code="backend_handle_unknown",
                phase="backend",
                message="unknown in-process backend handle",
                member_name=handle.wake_endpoint.member_name,
            )
        return state


class _ProcessBackend:
    resolved_backend: ResolvedBackend

    def __init__(
        self,
        *,
        process_factory=None,
        clock=None,
        token_factory=None,
    ) -> None:
        self._process_factory = process_factory or _default_process_factory
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._token_factory = token_factory or (lambda: uuid.uuid4().hex)
        self._processes: dict[str, _ProcessState] = {}

    async def start(self, spec: MemberLaunchSpec) -> BackendHandle:
        _require_launch_spec(spec, self.resolved_backend)
        process = self._launch(spec)
        process_id = getattr(process, "pid", 0)
        token = self._token_factory()
        handle = BackendHandle(
            wake_endpoint=spec.wake_endpoint,
            process_id=process_id,
            started_at=self._clock(),
            token=token,
        )
        self._processes[token] = _ProcessState(spec=spec, process=process)
        return handle

    async def wake(self, handle: BackendHandle) -> None:
        state = self._process_state(handle)
        if getattr(state.process, "poll", lambda: None)() is not None:
            state.process = self._launch(state.spec)

    async def stop(self, handle: BackendHandle, *, force: bool) -> None:
        state = self._process_state(handle)
        process = state.process
        if getattr(process, "poll", lambda: None)() is None:
            stopper = getattr(process, "kill" if force else "terminate", None)
            if callable(stopper):
                stopper()
        self._processes.pop(handle.token, None)

    def _launch(self, spec: MemberLaunchSpec) -> object:
        argv = self._build_argv(spec)
        env = {**os.environ, **dict(spec.environment)}
        return self._process_factory(argv, spec.workspace_root, env)

    def _build_argv(self, spec: MemberLaunchSpec) -> tuple[str, ...]:
        raise NotImplementedError

    def _process_state(self, handle: BackendHandle) -> _ProcessState:
        process = self._processes.get(handle.token)
        if process is None:
            raise TeamError(
                code="backend_handle_unknown",
                phase="backend",
                message="unknown process backend handle",
                member_name=handle.wake_endpoint.member_name,
            )
        return process


class TmuxBackend(_ProcessBackend):
    resolved_backend = ResolvedBackend.TMUX

    def _build_argv(self, spec: MemberLaunchSpec) -> tuple[str, ...]:
        session_name = _safe_session_name(spec.team_name, spec.member_name)
        return ("tmux", "new-session", "-d", "-s", session_name, *spec.argv)


class WindowsTerminalBackend(_ProcessBackend):
    resolved_backend = ResolvedBackend.WINDOWS_TERMINAL

    def _build_argv(self, spec: MemberLaunchSpec) -> tuple[str, ...]:
        title = f"{spec.team_name}:{spec.member_name}"
        return (
            "wt",
            "new-tab",
            "--title",
            title,
            "--startingDirectory",
            str(spec.workspace_root),
            *spec.argv,
        )


def _candidate_backends(
    requested_backend: MemberBackend,
    *,
    priority: tuple[MemberBackend, ...] | None = None,
) -> tuple[ResolvedBackend, ...]:
    if requested_backend is MemberBackend.AUTO:
        if priority is not None:
            return tuple(_resolved_from_requested(item) for item in priority)
        return AUTO_BACKEND_ORDER
    if requested_backend is MemberBackend.TMUX:
        return (ResolvedBackend.TMUX,)
    if requested_backend is MemberBackend.TERMINAL:
        return (ResolvedBackend.WINDOWS_TERMINAL,)
    if requested_backend is MemberBackend.IN_PROCESS:
        return (ResolvedBackend.IN_PROCESS,)
    raise ValueError("requested_backend must be a MemberBackend")


def _resolved_from_requested(backend: MemberBackend) -> ResolvedBackend:
    if backend is MemberBackend.AUTO:
        raise ValueError("backend priority must not contain auto")
    if backend is MemberBackend.TMUX:
        return ResolvedBackend.TMUX
    if backend is MemberBackend.TERMINAL:
        return ResolvedBackend.WINDOWS_TERMINAL
    if backend is MemberBackend.IN_PROCESS:
        return ResolvedBackend.IN_PROCESS
    raise ValueError("backend priority must contain MemberBackend values")


def _available_reason(
    requested_backend: MemberBackend,
    resolved_backend: ResolvedBackend,
    attempted: list[ResolvedBackend],
) -> str:
    if requested_backend is not MemberBackend.AUTO:
        return f"selected {resolved_backend.value}"
    if len(attempted) == 1:
        return f"selected {resolved_backend.value}"
    failed = _join_backend_names(attempted[:-1])
    verb = "was" if len(attempted) == 2 else "were"
    return f"selected {resolved_backend.value} after {failed} {verb} unavailable"


def _unavailable_reason(requested_backend: MemberBackend) -> str:
    if requested_backend is MemberBackend.AUTO:
        return "no backend capability was available"
    return f"requested {requested_backend.value} backend is unavailable"


def _join_backend_names(backends: tuple[ResolvedBackend, ...] | list[ResolvedBackend]) -> str:
    names = [backend.value for backend in backends]
    if not names:
        return ""
    if len(names) == 1:
        return names[0]
    if len(names) == 2:
        return f"{names[0]} and {names[1]}"
    return ", ".join(names[:-1]) + f", and {names[-1]}"


def _default_capability_probe(
    backend: ResolvedBackend,
    environment: BackendEnvironment,
) -> bool:
    if backend is ResolvedBackend.TMUX:
        return environment.tmux_available
    if backend is ResolvedBackend.WINDOWS_TERMINAL:
        return environment.terminal_available
    if backend is ResolvedBackend.IN_PROCESS:
        return environment.in_process_available
    raise ValueError("unknown backend")


async def _run_until_idle(runtime: object) -> None:
    run_until_idle = getattr(runtime, "run_until_idle", None)
    if not callable(run_until_idle):
        raise TeamError(
            code="runtime_invalid",
            phase="backend",
            message="runtime must provide run_until_idle",
        )
    result = run_until_idle()
    if asyncio.iscoroutine(result):
        await result


async def _maybe_await(result):
    if asyncio.iscoroutine(result):
        return await result
    return result


def _missing_runtime_factory(spec: MemberLaunchSpec):
    raise TeamError(
        code="runtime_factory_missing",
        phase="backend",
        message="in-process backend requires a runtime factory",
        team_name=spec.team_name,
        member_name=spec.member_name,
    )


def _require_launch_spec(spec: MemberLaunchSpec, expected_backend: ResolvedBackend) -> None:
    if not isinstance(spec, MemberLaunchSpec):
        raise ValueError("spec must be a MemberLaunchSpec")
    if spec.resolved_backend is not expected_backend:
        raise ValueError(f"spec resolved_backend must be {expected_backend.value}")


def _default_process_factory(argv: tuple[str, ...], cwd: Path, env: dict[str, str]):
    return subprocess.Popen(
        argv,
        cwd=str(cwd),
        env=env,
        shell=False,
    )


def _safe_session_name(team_name: str, member_name: str) -> str:
    safe = []
    for char in f"mycode-{team_name}-{member_name}":
        if char.isalnum() or char in {"-", "_", ":"}:
            safe.append(char)
        else:
            safe.append("-")
    return "".join(safe)


__all__ = [
    "AUTO_BACKEND_ORDER",
    "BackendCapabilityProbe",
    "BackendRouter",
    "BackendSelector",
    "InProcessBackend",
    "TmuxBackend",
    "WindowsTerminalBackend",
]
