import math

import pytest

from mycode.config import ConfigError
from mycode.subagent.config import (
    parse_subagent_config,
    validate_subagent_tool_names,
)
from mycode.subagent.models import AgentModelTier


def test_parse_subagent_config_requires_complete_model_mapping():
    config = parse_subagent_config(
        {
            "models": {
                "haiku": "claude-haiku-test",
                "sonnet": "claude-sonnet-test",
                "opus": "claude-opus-test",
            }
        }
    )

    assert config.model_map == {
        AgentModelTier.HAIKU: "claude-haiku-test",
        AgentModelTier.SONNET: "claude-sonnet-test",
        AgentModelTier.OPUS: "claude-opus-test",
    }
    assert config.foreground_timeout_seconds == 120.0
    assert config.max_concurrency == 4
    assert config.background_allowed_tools == (
        "read_file",
        "find_files",
        "search_code",
    )
    assert config.max_task_bytes == 64 * 1024
    assert config.max_result_bytes == 128 * 1024
    assert config.max_notification_bytes == 4 * 1024
    assert config.max_queued_tasks == 64
    assert config.max_retained_tasks == 256


@pytest.mark.parametrize(
    "raw",
    [
        None,
        {},
        {"models": None},
        {"models": {"haiku": "h", "sonnet": "s"}},
        {"models": {"haiku": "h", "sonnet": "s", "opus": ""}},
        {"models": {"haiku": "h", "sonnet": "s", "opus": "o", "extra": "x"}},
    ],
)
def test_parse_subagent_config_rejects_missing_or_invalid_models(raw):
    with pytest.raises(ConfigError, match="sub_agent.models"):
        parse_subagent_config(raw)


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("foreground_timeout_seconds", True),
        ("foreground_timeout_seconds", 0),
        ("foreground_timeout_seconds", -1),
        ("foreground_timeout_seconds", math.inf),
        ("max_concurrency", True),
        ("max_concurrency", 0),
        ("max_concurrency", -1),
        ("max_task_bytes", True),
        ("max_task_bytes", 0),
        ("max_result_bytes", -1),
        ("max_notification_bytes", 0),
        ("max_queued_tasks", False),
        ("max_retained_tasks", 0),
    ],
)
def test_parse_subagent_config_rejects_bool_nonfinite_and_nonpositive_limits(
    field_name, value
):
    raw = {
        "models": {"haiku": "h", "sonnet": "s", "opus": "o"},
        field_name: value,
    }

    with pytest.raises(ConfigError, match=field_name):
        parse_subagent_config(raw)


def test_parse_subagent_config_rejects_invalid_background_allowed_tools():
    with pytest.raises(ConfigError, match="background_allowed_tools"):
        parse_subagent_config(
            {
                "models": {"haiku": "h", "sonnet": "s", "opus": "o"},
                "background_allowed_tools": ["read_file", "read_file"],
            }
        )

    with pytest.raises(ConfigError, match="background_allowed_tools"):
        parse_subagent_config(
            {
                "models": {"haiku": "h", "sonnet": "s", "opus": "o"},
                "background_allowed_tools": [],
            }
        )

    with pytest.raises(ConfigError, match="background_allowed_tools"):
        parse_subagent_config(
            {
                "models": {"haiku": "h", "sonnet": "s", "opus": "o"},
                "background_allowed_tools": ["*"],
            }
        )


def test_validate_subagent_tool_names_rejects_unknown_background_tools():
    config = parse_subagent_config(
        {
            "models": {"haiku": "h", "sonnet": "s", "opus": "o"},
            "background_allowed_tools": ["read_file", "missing_tool"],
        }
    )

    with pytest.raises(ConfigError, match="missing_tool"):
        validate_subagent_tool_names(config, {"read_file", "find_files", "search_code"})


def test_validate_subagent_tool_names_accepts_known_background_tools():
    config = parse_subagent_config(
        {
            "models": {"haiku": "h", "sonnet": "s", "opus": "o"},
            "background_allowed_tools": ["read_file", "search_code"],
        }
    )

    validate_subagent_tool_names(config, {"read_file", "find_files", "search_code"})
