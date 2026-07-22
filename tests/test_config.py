import os

import pytest

from mycode.config import ConfigError, load_config


def write_config(path, text):
    path.write_text(text, encoding="utf-8")


def test_loads_explicit_config_path(tmp_path):
    config_path = tmp_path / "custom.yaml"
    cwd = tmp_path / "cwd"
    home = tmp_path / "home"
    cwd.mkdir()
    home.mkdir()
    write_config(
        config_path,
        """
protocol: anthropic
model: claude-test
base_url: https://api.anthropic.com
api_key: sk-test-literal
compact:
  context_window_tokens: 128000
""",
    )
    write_config(
        cwd / "mycode.yaml",
        """
protocol: openai_chat
model: wrong
base_url: https://example.com
api_key: wrong
compact:
  context_window_tokens: 128000
""",
    )

    config = load_config(config_path, cwd=cwd, home=home, environ={})

    assert config.protocol == "anthropic"
    assert config.model == "claude-test"
    assert config.base_url == "https://api.anthropic.com"
    assert config.api_key == "sk-test-literal"


def test_loads_cwd_config_before_home(tmp_path):
    cwd = tmp_path / "cwd"
    home = tmp_path / "home"
    cwd.mkdir()
    (home / ".mycode").mkdir(parents=True)
    write_config(
        cwd / "mycode.yaml",
        """
protocol: openai_responses
model: gpt-test
base_url: https://api.openai.com/v1
api_key: sk-cwd
compact:
  context_window_tokens: 128000
""",
    )
    write_config(
        home / ".mycode" / "config.yaml",
        """
protocol: anthropic
model: wrong
base_url: https://example.com
api_key: wrong
compact:
  context_window_tokens: 128000
""",
    )

    config = load_config(None, cwd=cwd, home=home, environ={})

    assert config.protocol == "openai_responses"
    assert config.api_key == "sk-cwd"


def test_loads_home_config_when_no_explicit_or_cwd_config(tmp_path):
    cwd = tmp_path / "cwd"
    home = tmp_path / "home"
    cwd.mkdir()
    (home / ".mycode").mkdir(parents=True)
    write_config(
        home / ".mycode" / "config.yaml",
        """
protocol: openai_chat
model: gpt-test
base_url: https://api.openai.com/v1
api_key: sk-home
compact:
  context_window_tokens: 128000
""",
    )

    config = load_config(None, cwd=cwd, home=home, environ={})

    assert config.protocol == "openai_chat"
    assert config.api_key == "sk-home"


def test_requires_core_fields(tmp_path):
    config_path = tmp_path / "mycode.yaml"
    write_config(
        config_path,
        """
protocol: anthropic
model: claude-test
base_url: https://api.anthropic.com
compact:
  context_window_tokens: 128000
""",
    )

    with pytest.raises(ConfigError, match="api_key"):
        load_config(config_path, cwd=tmp_path, home=tmp_path, environ={})


def test_resolves_api_key_from_environment(tmp_path):
    config_path = tmp_path / "mycode.yaml"
    write_config(
        config_path,
        """
protocol: anthropic
model: claude-test
base_url: https://api.anthropic.com
api_key: ${MYCODE_TEST_API_KEY}
compact:
  context_window_tokens: 128000
""",
    )

    config = load_config(
        config_path,
        cwd=tmp_path,
        home=tmp_path,
        environ={"MYCODE_TEST_API_KEY": "sk-from-env"},
    )

    assert config.api_key == "sk-from-env"


def test_missing_environment_api_key_does_not_leak_secret_name_value(tmp_path, monkeypatch):
    config_path = tmp_path / "mycode.yaml"
    write_config(
        config_path,
        """
protocol: anthropic
model: claude-test
base_url: https://api.anthropic.com
api_key: ${MYCODE_MISSING_API_KEY}
compact:
  context_window_tokens: 128000
""",
    )
    monkeypatch.setitem(os.environ, "MYCODE_MISSING_API_KEY", "sk-real-secret")

    with pytest.raises(ConfigError) as exc:
        load_config(config_path, cwd=tmp_path, home=tmp_path, environ={})

    message = str(exc.value)
    assert "sk-real-secret" not in message
    assert "MYCODE_MISSING_API_KEY" in message


def test_loads_optional_thinking_config(tmp_path):
    config_path = tmp_path / "mycode.yaml"
    write_config(
        config_path,
        """
protocol: anthropic
model: claude-test
base_url: https://api.anthropic.com
api_key: sk-test
thinking:
  enabled: true
  budget_tokens: 2048
  show: true
compact:
  context_window_tokens: 128000
""",
    )

    config = load_config(config_path, cwd=tmp_path, home=tmp_path, environ={})

    assert config.thinking.enabled is True
    assert config.thinking.budget_tokens == 2048
    assert config.thinking.show is True


def test_loads_optional_usage_config_and_rejects_invalid_values(tmp_path):
    config_path = tmp_path / "mycode.yaml"
    write_config(
        config_path,
        """
protocol: openai_chat
model: gpt-test
base_url: https://api.openai.com/v1
api_key: sk-test
usage:
  request_stream_usage: true
compact:
  context_window_tokens: 128000
""",
    )

    assert load_config(config_path, cwd=tmp_path, home=tmp_path, environ={}).usage.request_stream_usage is True

    write_config(config_path, """
protocol: openai_chat
model: gpt-test
base_url: https://api.openai.com/v1
api_key: sk-test
usage: true
compact:
  context_window_tokens: 128000
""")
    with pytest.raises(ConfigError, match="usage"):
        load_config(config_path, cwd=tmp_path, home=tmp_path, environ={})


def test_requires_compact_config(tmp_path):
    config_path = tmp_path / "mycode.yaml"
    write_config(
        config_path,
        """
protocol: anthropic
model: claude-test
base_url: https://api.anthropic.com
api_key: sk-test
""",
    )

    with pytest.raises(ConfigError, match="compact"):
        load_config(config_path, cwd=tmp_path, home=tmp_path, environ={})


def test_requires_compact_context_window_tokens(tmp_path):
    config_path = tmp_path / "mycode.yaml"
    write_config(
        config_path,
        """
protocol: anthropic
model: claude-test
base_url: https://api.anthropic.com
api_key: sk-test
compact: {}
""",
    )

    with pytest.raises(ConfigError, match="context_window_tokens"):
        load_config(config_path, cwd=tmp_path, home=tmp_path, environ={})


def test_loads_compact_config_with_default_thresholds(tmp_path):
    config_path = tmp_path / "mycode.yaml"
    write_config(
        config_path,
        """
protocol: anthropic
model: claude-test
base_url: https://api.anthropic.com
api_key: sk-test
compact:
  context_window_tokens: 128000
""",
    )

    config = load_config(config_path, cwd=tmp_path, home=tmp_path, environ={})

    assert config.compact.context_window_tokens == 128000
    assert config.compact.tool_result_threshold_tokens == 8_000
    assert config.compact.tool_batch_threshold_tokens == 12_000


def test_loads_compact_config_with_explicit_threshold_overrides(tmp_path):
    config_path = tmp_path / "mycode.yaml"
    write_config(
        config_path,
        """
protocol: anthropic
model: claude-test
base_url: https://api.anthropic.com
api_key: sk-test
compact:
  context_window_tokens: 128000
  tool_result_threshold_tokens: 4000
  tool_batch_threshold_tokens: 6000
""",
    )

    config = load_config(config_path, cwd=tmp_path, home=tmp_path, environ={})

    assert config.compact.context_window_tokens == 128000
    assert config.compact.tool_result_threshold_tokens == 4000
    assert config.compact.tool_batch_threshold_tokens == 6000


def test_requires_compact_to_be_a_mapping(tmp_path):
    config_path = tmp_path / "mycode.yaml"
    write_config(
        config_path,
        """
protocol: anthropic
model: claude-test
base_url: https://api.anthropic.com
api_key: sk-test
compact: true
""",
    )

    with pytest.raises(ConfigError, match="compact"):
        load_config(config_path, cwd=tmp_path, home=tmp_path, environ={})


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("context_window_tokens", "true"),
        ("context_window_tokens", "1.5"),
        ("context_window_tokens", '"128000"'),
        ("tool_result_threshold_tokens", "true"),
        ("tool_result_threshold_tokens", "1.5"),
        ("tool_result_threshold_tokens", '"8000"'),
        ("tool_batch_threshold_tokens", "true"),
        ("tool_batch_threshold_tokens", "1.5"),
        ("tool_batch_threshold_tokens", '"12000"'),
    ],
)
def test_rejects_non_integer_compact_values(tmp_path, field_name, value):
    config_path = tmp_path / "mycode.yaml"
    write_config(
        config_path,
        f"""
protocol: anthropic
model: claude-test
base_url: https://api.anthropic.com
api_key: sk-test
compact:
  context_window_tokens: 128000
  {field_name}: {value}
""",
    )

    with pytest.raises(ConfigError, match="integer"):
        load_config(config_path, cwd=tmp_path, home=tmp_path, environ={})
