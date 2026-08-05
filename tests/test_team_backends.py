from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from mycode.team.models import (
    BackendEnvironment,
    BackendSelection,
    MemberBackend,
    ResolvedBackend,
)


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


def test_backend_selector_auto_tries_tmux_then_terminal_then_in_process(tmp_path: Path):
    from mycode.team.backends import BackendSelector

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

    assert probe.calls == [
        ResolvedBackend.TMUX,
        ResolvedBackend.WINDOWS_TERMINAL,
    ]
    assert selection == BackendSelection(
        requested_backend=MemberBackend.AUTO,
        resolved_backend=ResolvedBackend.WINDOWS_TERMINAL,
        available=True,
        reason_code="backend_available",
        reason="selected windows_terminal after tmux was unavailable",
        environment=make_environment(tmp_path),
        fallback_chain=(ResolvedBackend.TMUX, ResolvedBackend.WINDOWS_TERMINAL),
    )


def test_backend_selector_auto_falls_back_to_in_process_when_native_backends_are_unavailable(
    tmp_path: Path,
):
    from mycode.team.backends import BackendSelector

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

    assert probe.calls == [
        ResolvedBackend.TMUX,
        ResolvedBackend.WINDOWS_TERMINAL,
        ResolvedBackend.IN_PROCESS,
    ]
    assert selection.resolved_backend is ResolvedBackend.IN_PROCESS
    assert selection.available is True
    assert selection.fallback_chain == (
        ResolvedBackend.TMUX,
        ResolvedBackend.WINDOWS_TERMINAL,
        ResolvedBackend.IN_PROCESS,
    )


def test_backend_selector_explicit_unavailable_backend_fails_closed(tmp_path: Path):
    from mycode.team.backends import BackendSelector

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
    from mycode.team.backends import BackendSelector

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
    from mycode.team.backends import BackendSelector

    class BrokenProbe:
        def __call__(self, backend: ResolvedBackend, environment: BackendEnvironment) -> bool:
            return 1  # type: ignore[return-value]

    selector = BackendSelector(capability_probe=BrokenProbe())

    with pytest.raises(ValueError, match="capability probe"):
        selector.select(MemberBackend.AUTO, make_environment(tmp_path))
