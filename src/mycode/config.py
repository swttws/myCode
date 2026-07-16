from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping

import yaml


class ConfigError(ValueError):
    """配置加载或校验失败。"""


@dataclass(frozen=True)
class ThinkingConfig:
    enabled: bool = False
    budget_tokens: int | None = None
    show: bool = False


@dataclass(frozen=True)
class UsageConfig:
    request_stream_usage: bool = False


@dataclass(frozen=True)
class LLMConfig:
    protocol: str
    model: str
    base_url: str
    api_key: str
    thinking: ThinkingConfig = field(default_factory=ThinkingConfig)
    usage: UsageConfig = field(default_factory=UsageConfig)


ENV_VAR_PATTERN = re.compile(r"^\$\{([A-Za-z_][A-Za-z0-9_]*)\}$")
CORE_FIELDS = ("protocol", "model", "base_url", "api_key")


def load_config(
    explicit_path: str | Path | None = None,
    *,
    cwd: str | Path | None = None,
    home: str | Path | None = None,
    environ: Mapping[str, str] | None = None,
) -> LLMConfig:
    config_path = _resolve_config_path(explicit_path, cwd=cwd, home=home)
    raw = _read_yaml_mapping(config_path)
    _validate_core_fields(raw)

    env = os.environ if environ is None else environ
    api_key = _resolve_api_key(str(raw["api_key"]), env)
    thinking = _parse_thinking(raw.get("thinking"))
    usage = _parse_usage(raw.get("usage"))

    return LLMConfig(
        protocol=str(raw["protocol"]),
        model=str(raw["model"]),
        base_url=str(raw["base_url"]),
        api_key=api_key,
        thinking=thinking,
        usage=usage,
    )


def _resolve_config_path(
    explicit_path: str | Path | None,
    *,
    cwd: str | Path | None,
    home: str | Path | None,
) -> Path:
    # 配置查找顺序固定：显式路径优先，其次当前目录，最后用户目录。
    if explicit_path is not None:
        path = Path(explicit_path)
        if not path.exists():
            raise ConfigError(f"Config file not found: {path}")
        return path

    current_dir = Path.cwd() if cwd is None else Path(cwd)
    cwd_config = current_dir / "mycode.yaml"
    if cwd_config.exists():
        return cwd_config

    home_dir = Path.home() if home is None else Path(home)
    home_config = home_dir / ".mycode" / "config.yaml"
    if home_config.exists():
        return home_config

    raise ConfigError("Config file not found. Use --config or create mycode.yaml.")


def _read_yaml_mapping(path: Path) -> dict[str, object]:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigError(f"Invalid YAML config: {path}") from exc

    if not isinstance(data, dict):
        raise ConfigError("Config file must contain a YAML mapping.")
    return data


def _validate_core_fields(raw: Mapping[str, object]) -> None:
    missing = [field_name for field_name in CORE_FIELDS if not raw.get(field_name)]
    if missing:
        raise ConfigError(f"Missing required config field: {', '.join(missing)}")


def _resolve_api_key(value: str, environ: Mapping[str, str]) -> str:
    # api_key 可以是字面值，也可以是 ${ENV_NAME} 形式的环境变量引用。
    match = ENV_VAR_PATTERN.match(value)
    if match is None:
        return value

    env_name = match.group(1)
    if env_name not in environ or not environ[env_name]:
        raise ConfigError(f"Environment variable is not set for api_key: {env_name}")
    return environ[env_name]


def _parse_thinking(raw: object) -> ThinkingConfig:
    if raw is None:
        return ThinkingConfig()
    if not isinstance(raw, dict):
        raise ConfigError("thinking must be a YAML mapping.")

    return ThinkingConfig(
        enabled=bool(raw.get("enabled", False)),
        budget_tokens=_optional_int(raw.get("budget_tokens")),
        show=bool(raw.get("show", False)),
    )


def _parse_usage(raw: object) -> UsageConfig:
    if raw is None:
        return UsageConfig()
    if not isinstance(raw, dict):
        raise ConfigError("usage must be a YAML mapping.")
    value = raw.get("request_stream_usage", False)
    if not isinstance(value, bool):
        raise ConfigError("usage.request_stream_usage must be a boolean.")
    return UsageConfig(request_stream_usage=value)


def _optional_int(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise ConfigError("thinking.budget_tokens must be an integer.")
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ConfigError("thinking.budget_tokens must be an integer.") from exc
