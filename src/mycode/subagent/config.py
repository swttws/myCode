from __future__ import annotations

import math
from typing import Mapping

from mycode.config import ConfigError
from mycode.subagent.models import (
    DEFAULT_BACKGROUND_ALLOWED_TOOLS,
    DEFAULT_FOREGROUND_TIMEOUT_SECONDS,
    DEFAULT_MAX_CONCURRENCY,
    DEFAULT_MAX_NOTIFICATION_BYTES,
    DEFAULT_MAX_QUEUED_TASKS,
    DEFAULT_MAX_RESULT_BYTES,
    DEFAULT_MAX_RETAINED_TASKS,
    DEFAULT_MAX_TASK_BYTES,
    AgentModelTier,
    SubAgentConfig,
)
from mycode.team.tool_names import LEGACY_TEAM_TOOL_NAMES


_REQUIRED_MODEL_TIERS = (AgentModelTier.HAIKU, AgentModelTier.SONNET, AgentModelTier.OPUS)


def parse_subagent_config(raw: object) -> SubAgentConfig:
    if not isinstance(raw, Mapping):
        raise ConfigError("sub_agent.models must declare haiku, sonnet and opus.")

    raw_models = raw.get("models")
    if not isinstance(raw_models, Mapping):
        raise ConfigError("sub_agent.models must be a YAML mapping.")
    model_map = _parse_model_map(raw_models)

    return SubAgentConfig(
        model_map=model_map,
        foreground_timeout_seconds=_positive_number(
            raw.get("foreground_timeout_seconds", DEFAULT_FOREGROUND_TIMEOUT_SECONDS),
            "sub_agent.foreground_timeout_seconds",
        ),
        max_concurrency=_positive_int(
            raw.get("max_concurrency", DEFAULT_MAX_CONCURRENCY),
            "sub_agent.max_concurrency",
        ),
        background_allowed_tools=_background_tools(
            raw.get("background_allowed_tools", DEFAULT_BACKGROUND_ALLOWED_TOOLS)
        ),
        max_task_bytes=_positive_int(
            raw.get("max_task_bytes", DEFAULT_MAX_TASK_BYTES),
            "sub_agent.max_task_bytes",
        ),
        max_result_bytes=_positive_int(
            raw.get("max_result_bytes", DEFAULT_MAX_RESULT_BYTES),
            "sub_agent.max_result_bytes",
        ),
        max_notification_bytes=_positive_int(
            raw.get("max_notification_bytes", DEFAULT_MAX_NOTIFICATION_BYTES),
            "sub_agent.max_notification_bytes",
        ),
        max_queued_tasks=_positive_int(
            raw.get("max_queued_tasks", DEFAULT_MAX_QUEUED_TASKS),
            "sub_agent.max_queued_tasks",
        ),
        max_retained_tasks=_positive_int(
            raw.get("max_retained_tasks", DEFAULT_MAX_RETAINED_TASKS),
            "sub_agent.max_retained_tasks",
        ),
    )


def validate_subagent_tool_names(config: SubAgentConfig, available_tool_names: set[str]) -> None:
    unknown = [
        tool_name
        for tool_name in config.background_allowed_tools
        if tool_name not in available_tool_names
    ]
    if unknown:
        legacy = sorted(set(unknown) & LEGACY_TEAM_TOOL_NAMES)
        if legacy:
            raise ConfigError(
                "sub_agent.background_allowed_tools 包含已移除的旧团队工具："
                + ", ".join(legacy)
                + "；请改用新的 team_* 工具名"
            )
        raise ConfigError(
            "sub_agent.background_allowed_tools contains unknown tool: "
            + ", ".join(sorted(unknown))
        )


def _parse_model_map(raw_models: Mapping[object, object]) -> dict[AgentModelTier, str]:
    valid_keys = {tier.value for tier in _REQUIRED_MODEL_TIERS}
    keys = {str(key) for key in raw_models}
    missing = sorted(valid_keys - keys)
    unknown = sorted(keys - valid_keys)
    if missing:
        raise ConfigError("sub_agent.models missing required tier: " + ", ".join(missing))
    if unknown:
        raise ConfigError("sub_agent.models contains unknown tier: " + ", ".join(unknown))

    model_map: dict[AgentModelTier, str] = {}
    for tier in _REQUIRED_MODEL_TIERS:
        value = raw_models[tier.value]
        if type(value) is not str or not value:
            raise ConfigError(f"sub_agent.models.{tier.value} must be a non-empty string.")
        model_map[tier] = value
    return model_map


def _positive_number(value: object, field_name: str) -> float:
    if isinstance(value, bool) or type(value) not in (int, float):
        raise ConfigError(f"{field_name} must be a positive number.")
    result = float(value)
    if not math.isfinite(result) or result <= 0:
        raise ConfigError(f"{field_name} must be a positive finite number.")
    return result


def _positive_int(value: object, field_name: str) -> int:
    if type(value) is not int:
        raise ConfigError(f"{field_name} must be a positive integer.")
    if value <= 0:
        raise ConfigError(f"{field_name} must be greater than zero.")
    return value


def _background_tools(value: object) -> tuple[str, ...]:
    if not isinstance(value, list | tuple):
        raise ConfigError("sub_agent.background_allowed_tools must be a list.")
    tools: list[str] = []
    seen: set[str] = set()
    for tool_name in value:
        if type(tool_name) is not str or not tool_name:
            raise ConfigError("sub_agent.background_allowed_tools must contain tool names.")
        if tool_name == "*":
            raise ConfigError('sub_agent.background_allowed_tools must not contain "*".')
        if tool_name in seen:
            raise ConfigError(
                "sub_agent.background_allowed_tools contains duplicate tool: "
                + tool_name
            )
        seen.add(tool_name)
        tools.append(tool_name)
    if not tools:
        raise ConfigError("sub_agent.background_allowed_tools must not be empty.")
    return tuple(tools)
