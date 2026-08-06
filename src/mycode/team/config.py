from __future__ import annotations

import math
import os
from collections.abc import Mapping
from dataclasses import dataclass

from mycode.team.models import MemberBackend


MAX_MEMBERS_LIMIT = 64
MAX_ACTIVE_MEMBERS_LIMIT = 16
MAX_MAILBOX_MESSAGE_BYTES = 1024 * 1024
MAX_MAILBOX_SUMMARY_BYTES = 64 * 1024
MAX_CONTEXT_BYTES = 64 * 1024 * 1024
COORDINATOR_ENV_VAR = "MYCODE_COORDINATOR"
DEFAULT_BACKEND_PRIORITY = (
    MemberBackend.TMUX,
    MemberBackend.TERMINAL,
    MemberBackend.IN_PROCESS,
)


@dataclass(frozen=True)
class TeamConfig:
    max_members: int = 16
    max_active_members: int = 4
    lock_retry_interval_seconds: float = 0.1
    lock_timeout_seconds: float = 5.0
    lock_stale_after_seconds: float = 30.0
    mailbox_message_max_bytes: int = 64 * 1024
    mailbox_summary_max_bytes: int = 4 * 1024
    context_max_bytes: int = 4 * 1024 * 1024
    backend_priority: tuple[MemberBackend, ...] = DEFAULT_BACKEND_PRIORITY
    coordinator_capability_enabled: bool = False
    graceful_shutdown_timeout_seconds: float = 10.0

    def __post_init__(self) -> None:
        _positive_int(
            self.max_members,
            "max_members",
            maximum=MAX_MEMBERS_LIMIT,
        )
        _positive_int(
            self.max_active_members,
            "max_active_members",
            maximum=MAX_ACTIVE_MEMBERS_LIMIT,
        )
        if self.max_active_members > self.max_members:
            raise ValueError("max_active_members must be less than or equal to max_members")
        _positive_number(self.lock_retry_interval_seconds, "lock_retry_interval_seconds")
        _positive_number(self.lock_timeout_seconds, "lock_timeout_seconds")
        _positive_number(self.lock_stale_after_seconds, "lock_stale_after_seconds")
        if self.lock_timeout_seconds < self.lock_retry_interval_seconds:
            raise ValueError("lock_timeout_seconds must be at least lock_retry_interval_seconds")
        if self.lock_stale_after_seconds < self.lock_timeout_seconds:
            raise ValueError("lock_stale_after_seconds must be at least lock_timeout_seconds")
        _positive_int(
            self.mailbox_message_max_bytes,
            "mailbox_message_max_bytes",
            maximum=MAX_MAILBOX_MESSAGE_BYTES,
        )
        _positive_int(
            self.mailbox_summary_max_bytes,
            "mailbox_summary_max_bytes",
            maximum=MAX_MAILBOX_SUMMARY_BYTES,
        )
        if self.mailbox_summary_max_bytes > self.mailbox_message_max_bytes:
            raise ValueError("mailbox_summary_max_bytes must not exceed mailbox_message_max_bytes")
        _positive_int(
            self.context_max_bytes,
            "context_max_bytes",
            maximum=MAX_CONTEXT_BYTES,
        )
        _normalize_backend_priority(self)
        if type(self.coordinator_capability_enabled) is not bool:
            raise ValueError("coordinator_capability_enabled must be a boolean")
        _positive_number(
            self.graceful_shutdown_timeout_seconds,
            "graceful_shutdown_timeout_seconds",
        )


def parse_team_config(raw: object) -> TeamConfig:
    if raw is None:
        return TeamConfig()
    if not isinstance(raw, Mapping):
        raise _config_error("team must be a YAML mapping.")
    try:
        return TeamConfig(
            max_members=_int_value(raw, "max_members", TeamConfig.max_members),
            max_active_members=_int_value(
                raw,
                "max_active_members",
                TeamConfig.max_active_members,
            ),
            lock_retry_interval_seconds=_number_value(
                raw,
                "lock_retry_interval_seconds",
                TeamConfig.lock_retry_interval_seconds,
            ),
            lock_timeout_seconds=_number_value(
                raw,
                "lock_timeout_seconds",
                TeamConfig.lock_timeout_seconds,
            ),
            lock_stale_after_seconds=_number_value(
                raw,
                "lock_stale_after_seconds",
                TeamConfig.lock_stale_after_seconds,
            ),
            mailbox_message_max_bytes=_int_value(
                raw,
                "mailbox_message_max_bytes",
                TeamConfig.mailbox_message_max_bytes,
            ),
            mailbox_summary_max_bytes=_int_value(
                raw,
                "mailbox_summary_max_bytes",
                TeamConfig.mailbox_summary_max_bytes,
            ),
            context_max_bytes=_int_value(
                raw,
                "context_max_bytes",
                TeamConfig.context_max_bytes,
            ),
            backend_priority=_backend_priority_value(raw.get("backend_priority")),
            coordinator_capability_enabled=_bool_value(
                raw,
                "coordinator_capability_enabled",
                TeamConfig.coordinator_capability_enabled,
            ),
            graceful_shutdown_timeout_seconds=_number_value(
                raw,
                "graceful_shutdown_timeout_seconds",
                TeamConfig.graceful_shutdown_timeout_seconds,
            ),
        )
    except ValueError as exc:
        raise _config_error(str(exc)) from exc


def coordinator_enabled_from_env(
    config: TeamConfig | None = None,
    environ: Mapping[str, str] | None = None,
) -> bool:
    if config is None:
        config = TeamConfig()
    if not isinstance(config, TeamConfig):
        raise ValueError("config must be a TeamConfig")
    env = os.environ if environ is None else environ
    return config.coordinator_capability_enabled and env.get(COORDINATOR_ENV_VAR) == "1"


def _int_value(raw: Mapping[object, object], field_name: str, default: int) -> int:
    value = raw.get(field_name, default)
    if type(value) is not int:
        raise _config_error(f"team.{field_name} must be a positive integer.")
    return value


def _number_value(raw: Mapping[object, object], field_name: str, default: float) -> float:
    value = raw.get(field_name, default)
    if isinstance(value, bool) or type(value) not in (int, float):
        raise _config_error(f"team.{field_name} must be a positive number.")
    return float(value)


def _bool_value(raw: Mapping[object, object], field_name: str, default: bool) -> bool:
    value = raw.get(field_name, default)
    if type(value) is not bool:
        raise _config_error(f"team.{field_name} must be a boolean.")
    return value


def _backend_priority_value(raw: object) -> tuple[MemberBackend, ...]:
    if raw is None:
        return DEFAULT_BACKEND_PRIORITY
    if not isinstance(raw, list | tuple):
        raise _config_error("team.backend_priority must be a list.")
    if not raw:
        raise _config_error("team.backend_priority must not be empty.")
    values: list[MemberBackend] = []
    seen: set[MemberBackend] = set()
    for item in raw:
        if type(item) is not str or not item:
            raise _config_error("team.backend_priority must contain backend names.")
        try:
            backend = MemberBackend(item)
        except ValueError as exc:
            raise _config_error(f"team.backend_priority contains unknown backend: {item}") from exc
        if backend is MemberBackend.AUTO:
            raise _config_error("team.backend_priority must not contain auto.")
        if backend in seen:
            raise _config_error(f"team.backend_priority contains duplicate backend: {item}")
        seen.add(backend)
        values.append(backend)
    return tuple(values)


def _normalize_backend_priority(config: TeamConfig) -> None:
    value = config.backend_priority
    if not isinstance(value, tuple):
        value = tuple(value)
        object.__setattr__(config, "backend_priority", value)
    if not value:
        raise ValueError("backend_priority must not be empty")
    seen: set[MemberBackend] = set()
    for item in value:
        if not isinstance(item, MemberBackend):
            raise ValueError("backend_priority must contain MemberBackend values")
        if item is MemberBackend.AUTO:
            raise ValueError("backend_priority must not contain auto")
        if item in seen:
            raise ValueError("backend_priority must not contain duplicate values")
        seen.add(item)


def _positive_int(value: object, field_name: str, *, maximum: int | None = None) -> None:
    if type(value) is not int:
        raise ValueError(f"{field_name} must be a positive integer")
    if value <= 0:
        raise ValueError(f"{field_name} must be greater than zero")
    if maximum is not None and value > maximum:
        raise ValueError(f"{field_name} must be at most {maximum}")


def _positive_number(value: object, field_name: str) -> None:
    if isinstance(value, bool) or type(value) not in (int, float):
        raise ValueError(f"{field_name} must be a positive number")
    result = float(value)
    if not math.isfinite(result) or result <= 0:
        raise ValueError(f"{field_name} must be a positive finite number")


def _config_error(message: str):
    from mycode.config import ConfigError

    return ConfigError(message)
