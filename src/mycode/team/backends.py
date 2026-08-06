from __future__ import annotations

from typing import Protocol

from mycode.team.models import (
    BackendEnvironment,
    BackendSelection,
    MemberBackend,
    ResolvedBackend,
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
    ) -> BackendSelection:
        if not isinstance(requested_backend, MemberBackend):
            raise ValueError("requested_backend must be a MemberBackend")
        if not isinstance(environment, BackendEnvironment):
            raise ValueError("environment must be a BackendEnvironment")
        if environment.requested_backend is not requested_backend:
            raise ValueError("environment requested_backend must match requested_backend")

        candidates = _candidate_backends(requested_backend)
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


def _candidate_backends(requested_backend: MemberBackend) -> tuple[ResolvedBackend, ...]:
    if requested_backend is MemberBackend.AUTO:
        return AUTO_BACKEND_ORDER
    if requested_backend is MemberBackend.TMUX:
        return (ResolvedBackend.TMUX,)
    if requested_backend is MemberBackend.TERMINAL:
        return (ResolvedBackend.WINDOWS_TERMINAL,)
    if requested_backend is MemberBackend.IN_PROCESS:
        return (ResolvedBackend.IN_PROCESS,)
    raise ValueError("requested_backend must be a MemberBackend")


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


__all__ = [
    "AUTO_BACKEND_ORDER",
    "BackendCapabilityProbe",
    "BackendSelector",
]
