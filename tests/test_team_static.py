"""Static analysis guards for the team module.

These tests enforce that the event-driven refactoring (Stage-18) remains clean:
- No dynamic attribute access in core team code without explicit exemption.
- No old mailbox consumption API leaks back into the main path.
- Per-file checks for supervisor, runtime, service, and backend.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import FrozenSet

import pytest

TEAM_SRC = Path(__file__).resolve().parent.parent / "src" / "mycode" / "team"

# ---------------------------------------------------------------------------
# getattr / hasattr whitelist
# ---------------------------------------------------------------------------
# Each entry is (filename, line_number, reason).
# Filenames are relative to TEAM_SRC.
_GETATTR_HASATTR_WHITELIST: FrozenSet[tuple[str, int, str]] = frozenset(
    [
        # --- dataclass field validation helpers (domain/models.py) ---
        ("domain/models.py", 844, "dataclass field validation: _normalize_string_tuple"),
        ("domain/models.py", 864, "dataclass field validation: _normalize_path_tuple"),
        ("domain/models.py", 873, "dataclass field validation: _normalize_enum_tuple"),
        ("domain/models.py", 883, "dataclass field validation: _normalize_string_mapping"),
        ("domain/models.py", 900, "dataclass field validation: _normalize_dataclass_tuple"),
        ("domain/models.py", 910, "dataclass field validation: _normalize_mapping"),
        # --- dataclass serialization (infrastructure/storage.py) ---
        ("infrastructure/storage.py", 304, "dataclass serialization: _encode_dataclass field access"),
        ("infrastructure/storage.py", 306, "dataclass serialization: _encode_dataclass None check"),
        # --- dataclass post-init validation loop (domain/state.py) ---
        ("domain/state.py", 71, "dataclass post-init validation: boolean field loop"),
        # --- enum / config / optional-attribute helpers (application/service.py) ---
        ("application/service.py", 67, "enum value extraction: _event_value helper"),
        # --- config fallback (execution/worker.py) ---
        ("execution/worker.py", 109, "config fallback: llm_config.team"),
        ("execution/worker.py", 235, "config fallback: tool_timeout_seconds"),
        ("execution/worker.py", 241, "config fallback: model"),
    ]
)

# ---------------------------------------------------------------------------
# Old mailbox consumption API patterns
# These must NOT appear in any source file under src/mycode/team.
# ---------------------------------------------------------------------------
_OLD_MAILBOX_PATTERNS: FrozenSet[str] = frozenset(
    [
        "MailboxStore",
        "lead_unread",
        "acknowledge_lead",
        "watch_mailbox",
    ]
)

# Pattern ".unread(" — checked separately to avoid false positives on
# method names like "mark_unread" which are fine.
_OLD_MAILBOX_UNREAD_SUFFIX = ".unread("


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _find_getattr_hasattr_calls() -> list[tuple[str, int, str]]:
    """Return (filename, lineno, call_name) for every getattr/hasattr in TEAM_SRC."""
    results: list[tuple[str, int, str]] = []
    for py_file in sorted(TEAM_SRC.rglob("*.py")):
        if py_file.name == "__init__.py" and py_file.parent == TEAM_SRC:
            continue  # skip top-level __init__.py re-exports
        try:
            tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                if node.func.id in ("getattr", "hasattr"):
                    rel = py_file.relative_to(TEAM_SRC)
                    results.append((str(rel).replace("\\", "/"), node.lineno, node.func.id))
    return results


def _find_pattern_in_src(pattern: str) -> list[str]:
    """Return relative file paths containing the given text pattern."""
    matches: list[str] = []
    for py_file in sorted(TEAM_SRC.rglob("*.py")):
        try:
            text = py_file.read_text(encoding="utf-8")
        except OSError:
            continue
        if pattern in text:
            rel = str(py_file.relative_to(TEAM_SRC)).replace("\\", "/")
            matches.append(rel)
    return matches


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestTeamCoreNoDynamicAttributeCalls:
    """Every getattr/hasattr call in src/mycode/team must be whitelisted."""

    def test_no_unlisted_getattr_or_hasattr(self) -> None:
        actual = _find_getattr_hasattr_calls()
        whitelist_keys = {(f, ln) for f, ln, _ in _GETATTR_HASATTR_WHITELIST}

        violations: list[str] = []
        for file, lineno, call_name in actual:
            if (file, lineno) not in whitelist_keys:
                violations.append(f"  {file}:{lineno}  {call_name}()")

        if violations:
            msg = (
                f"Found {len(violations)} unlisted getattr/hasattr call(s) in "
                f"src/mycode/team:\n"
                + "\n".join(violations)
                + "\n\nEither fix the call to use direct attribute access, "
                "or add it to _GETATTR_HASATTR_WHITELIST with a clear reason."
            )
            pytest.fail(msg)

    def test_whitelist_entries_still_exist(self) -> None:
        """Ensure whitelist entries haven't gone stale (no false passes)."""
        actual = {(f, ln) for f, ln, _ in _find_getattr_hasattr_calls()}
        stale: list[str] = []
        for file, lineno, reason in sorted(_GETATTR_HASATTR_WHITELIST):
            if (file, lineno) not in actual:
                stale.append(f"  {file}:{lineno}  ({reason})")
        if stale:
            msg = (
                f"Whitelist entries no longer match any getattr/hasattr call:\n"
                + "\n".join(stale)
                + "\n\nRemove these stale entries from _GETATTR_HASATTR_WHITELIST."
            )
            pytest.fail(msg)


class TestOldMailboxConsumptionApiAbsent:
    """Old mailbox consumption API must not appear in any team source file."""

    def test_no_mailbox_store_class(self) -> None:
        files = _find_pattern_in_src("MailboxStore")
        assert not files, (
            f"MailboxStore found in: {files}. "
            "Remove or mark as deprecated (not in main consumption path)."
        )

    def test_no_lead_unread(self) -> None:
        files = _find_pattern_in_src("lead_unread")
        assert not files, f"lead_unread found in: {files}"

    def test_no_acknowledge_lead(self) -> None:
        files = _find_pattern_in_src("acknowledge_lead")
        assert not files, f"acknowledge_lead found in: {files}"

    def test_no_watch_mailbox(self) -> None:
        files = _find_pattern_in_src("watch_mailbox")
        assert not files, f"watch_mailbox found in: {files}"

    def test_no_mailbox_unread_call(self) -> None:
        """`.unread(` must not appear as a mailbox consumption call."""
        for py_file in sorted(TEAM_SRC.rglob("*.py")):
            try:
                text = py_file.read_text(encoding="utf-8")
            except OSError:
                continue
            if _OLD_MAILBOX_UNREAD_SUFFIX in text:
                rel = str(py_file.relative_to(TEAM_SRC)).replace("\\", "/")
                pytest.fail(
                    f"`.unread(` found in {rel}. "
                    "Old mailbox unread scanning must not be in the main path."
                )


class TestPerFileOldConsumption:
    """Specific files must not contain old mailbox consumption patterns."""

    def test_supervisor_has_no_mailbox_watcher(self) -> None:
        supervisor = TEAM_SRC / "execution" / "supervisor.py"
        text = supervisor.read_text(encoding="utf-8")
        for pattern in ("mailbox", "unread", "watch_mailbox", "acknowledge_lead"):
            if pattern in text:
                pytest.fail(
                    f"execution/supervisor.py contains '{pattern}'. "
                    "Lead supervisor must not use old mailbox watcher/polling."
                )

    def test_runtime_has_no_mailbox_unread_scan(self) -> None:
        runtime = TEAM_SRC / "execution" / "runtime.py"
        text = runtime.read_text(encoding="utf-8")
        for pattern in ("mailbox", "unread", "acknowledge_lead"):
            if pattern in text:
                pytest.fail(
                    f"execution/runtime.py contains '{pattern}'. "
                    "Member runtime must not use old mailbox unread scan."
                )

    def test_service_has_no_old_lead_consumption_api(self) -> None:
        service = TEAM_SRC / "application" / "service.py"
        text = service.read_text(encoding="utf-8")
        for pattern in ("lead_unread", "acknowledge_lead", "MailboxStore"):
            if pattern in text:
                pytest.fail(
                    f"application/service.py contains '{pattern}'. "
                    "Service must not expose old lead mailbox consumption API."
                )

    def test_backend_has_no_run_until_idle_worker(self) -> None:
        backend = TEAM_SRC / "execution" / "backends.py"
        text = backend.read_text(encoding="utf-8")
        if "run_until_idle" in text:
            pytest.fail(
                "execution/backends.py contains 'run_until_idle'. "
                "Backend must not use old mailbox run_until_idle worker."
            )
