import pytest

from mycode.config import ConfigError
from mycode.team import MemberBackend
from mycode.team.infrastructure.config import TeamConfig, coordinator_enabled_from_env, parse_team_config


def test_parse_team_config_uses_safe_defaults():
    config = parse_team_config(None)

    assert config == TeamConfig()
    assert config.max_members == 16
    assert config.max_active_members == 4
    assert config.lock_retry_interval_seconds == 0.1
    assert config.lock_timeout_seconds == 5.0
    assert config.lock_stale_after_seconds == 30.0
    assert config.context_max_bytes == 4 * 1024 * 1024
    assert config.backend_priority == (
        MemberBackend.IN_PROCESS,
        MemberBackend.TMUX,
        MemberBackend.TERMINAL,
    )
    assert config.coordinator_capability_enabled is False
    assert config.graceful_shutdown_timeout_seconds == 10.0


def test_parse_team_config_accepts_explicit_values():
    config = parse_team_config(
        {
            "max_members": 8,
            "max_active_members": 3,
            "lock_retry_interval_seconds": 0.25,
            "lock_timeout_seconds": 3.0,
            "lock_stale_after_seconds": 12.0,
            "context_max_bytes": 2 * 1024 * 1024,
            "backend_priority": ["in_process", "tmux"],
            "coordinator_capability_enabled": True,
            "graceful_shutdown_timeout_seconds": 7.5,
        }
    )

    assert config.max_members == 8
    assert config.max_active_members == 3
    assert config.backend_priority == (MemberBackend.IN_PROCESS, MemberBackend.TMUX)
    assert config.coordinator_capability_enabled is True
    assert config.graceful_shutdown_timeout_seconds == 7.5


@pytest.mark.parametrize("value", [None, "", "0", "false", "true", "yes"])
def test_coordinator_env_requires_config_and_exact_environment_lock(value):
    enabled = TeamConfig(coordinator_capability_enabled=True)
    disabled = TeamConfig(coordinator_capability_enabled=False)
    environ = {} if value is None else {"MYCODE_COORDINATOR": value}

    assert coordinator_enabled_from_env(enabled, environ=environ) is False
    assert coordinator_enabled_from_env(disabled, environ={"MYCODE_COORDINATOR": "1"}) is False

    assert coordinator_enabled_from_env(enabled, environ={"MYCODE_COORDINATOR": "1"}) is True


@pytest.mark.parametrize(
    ("raw", "field_name"),
    [
        (True, "team"),
        ({"max_members": 0}, "max_members"),
        ({"max_members": 65}, "max_members"),
        ({"max_active_members": 0}, "max_active_members"),
        ({"max_active_members": 17}, "max_active_members"),
        ({"max_members": 2, "max_active_members": 3}, "max_active_members"),
        ({"lock_retry_interval_seconds": 0}, "lock_retry_interval_seconds"),
        ({"lock_timeout_seconds": 0}, "lock_timeout_seconds"),
        ({"lock_stale_after_seconds": 0}, "lock_stale_after_seconds"),
        (
            {"lock_retry_interval_seconds": 6.0, "lock_timeout_seconds": 5.0},
            "lock_timeout_seconds",
        ),
        (
            {"lock_timeout_seconds": 31.0, "lock_stale_after_seconds": 30.0},
            "lock_stale_after_seconds",
        ),
        ({"context_max_bytes": 0}, "context_max_bytes"),
        ({"backend_priority": []}, "backend_priority"),
        ({"backend_priority": ["tmux", "bogus"]}, "backend_priority"),
        ({"backend_priority": ["auto"]}, "backend_priority"),
        ({"coordinator_capability_enabled": "true"}, "coordinator_capability_enabled"),
    ],
)
def test_parse_team_config_rejects_invalid_values(raw, field_name):
    with pytest.raises(ConfigError, match=field_name):
        parse_team_config(raw)


def test_direct_team_config_construction_validates_invariants():
    with pytest.raises(ValueError, match="max_active_members"):
        TeamConfig(max_members=2, max_active_members=3)


# ── T16: Event-driven backend priority tests ──────────────────────────


def test_default_backend_priority_puts_in_process_first():
    """Default priority ensures in_process is tried before tmux/terminal."""
    config = TeamConfig()
    assert config.backend_priority[0] == MemberBackend.IN_PROCESS
    assert MemberBackend.IN_PROCESS in config.backend_priority
    # tmux and terminal are present as fallback extensions only
    assert MemberBackend.TMUX in config.backend_priority
    assert MemberBackend.TERMINAL in config.backend_priority


def test_parse_team_config_allows_in_process_only_backend_priority():
    """Users can configure in_process-only backend_priority for event-driven mode."""
    config = parse_team_config({"backend_priority": ["in_process"]})
    assert config.backend_priority == (MemberBackend.IN_PROCESS,)


def test_parse_team_config_allows_in_process_tmux_backend_priority():
    """Users can configure in_process + tmux as explicit fallback."""
    config = parse_team_config({"backend_priority": ["in_process", "tmux"]})
    assert config.backend_priority == (MemberBackend.IN_PROCESS, MemberBackend.TMUX)
