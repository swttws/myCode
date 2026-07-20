from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Mapping
from urllib.parse import urlparse

import yaml

from mycode.mcp.models import (
    MCPConfig,
    MCPDiagnostic,
    MCPServerConfig,
    MCPTransportKind,
)


ENV_REFERENCE_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
TOP_LEVEL_FIELDS = frozenset({"servers"})
COMMON_SERVER_FIELDS = frozenset({"name", "transport", "timeout_seconds", "read_tools"})
STDIO_FIELDS = COMMON_SERVER_FIELDS | {"command", "args", "env"}
HTTP_FIELDS = COMMON_SERVER_FIELDS | {"url", "headers"}


class MCPConfigError(ValueError):
    """MCP 配置文件无法定位、读取或解析。"""


def load_mcp_config(
    explicit_path: str | Path | None = None,
    *,
    cwd: str | Path | None = None,
    home: str | Path | None = None,
    environ: Mapping[str, str] | None = None,
) -> tuple[MCPConfig, tuple[MCPDiagnostic, ...]]:
    path = _resolve_config_path(explicit_path, cwd=cwd, home=home)
    if path is None:
        return MCPConfig(servers=()), ()

    raw = _read_config(path)
    env = os.environ if environ is None else environ
    servers: list[MCPServerConfig] = []
    diagnostics: list[MCPDiagnostic] = []
    known_names: set[str] = set()

    for index, item in enumerate(raw["servers"]):
        server_name = _diagnostic_server_name(item)
        try:
            server = _parse_server(item, env)
            if server.name in known_names:
                raise ValueError(f"duplicate MCP server name: {server.name}")
        except (TypeError, ValueError) as exc:
            diagnostics.append(
                MCPDiagnostic(
                    server_name=server_name,
                    category="config",
                    message=f"servers[{index}]: {exc}",
                )
            )
            continue

        known_names.add(server.name)
        servers.append(server)

    return MCPConfig(servers=tuple(servers)), tuple(diagnostics)


def _resolve_config_path(
    explicit_path: str | Path | None,
    *,
    cwd: str | Path | None,
    home: str | Path | None,
) -> Path | None:
    if explicit_path is not None:
        path = Path(explicit_path)
        if not path.is_file():
            raise MCPConfigError(f"MCP config file not found: {path}")
        return path

    current_dir = Path.cwd() if cwd is None else Path(cwd)
    workspace_path = current_dir / "mycode.mcp.yaml"
    if workspace_path.is_file():
        return workspace_path

    home_dir = Path.home() if home is None else Path(home)
    user_path = home_dir / ".mycode" / "mcp.yaml"
    if user_path.is_file():
        return user_path
    return None


def _read_config(path: Path) -> dict[str, list[object]]:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise MCPConfigError(f"Invalid YAML MCP config: {path}") from exc
    except OSError as exc:
        raise MCPConfigError(f"Unable to read MCP config: {path}") from exc

    if not isinstance(raw, dict):
        raise MCPConfigError("MCP config must be a YAML mapping")
    unknown = set(raw) - TOP_LEVEL_FIELDS
    if unknown:
        raise MCPConfigError(f"unknown top-level field: {sorted(unknown)[0]}")
    if not isinstance(raw.get("servers"), list):
        raise MCPConfigError("servers must be a list")
    return raw


def _parse_server(raw: object, environ: Mapping[str, str]) -> MCPServerConfig:
    if not isinstance(raw, dict):
        raise ValueError("server entry must be a mapping")

    name = raw.get("name")
    if not isinstance(name, str):
        raise ValueError("server name must be a string")

    transport_value = raw.get("transport")
    try:
        transport = MCPTransportKind(transport_value)
    except (TypeError, ValueError) as exc:
        raise ValueError("invalid MCP transport") from exc

    allowed_fields = STDIO_FIELDS if transport is MCPTransportKind.STDIO else HTTP_FIELDS
    unknown = set(raw) - allowed_fields
    if unknown:
        raise ValueError(f"unknown server field: {sorted(unknown)[0]}")

    timeout = raw.get("timeout_seconds")
    read_tools = _parse_string_list(raw.get("read_tools", []), field_name="read_tools")

    if transport is MCPTransportKind.STDIO:
        command = raw.get("command")
        if not isinstance(command, str) or not command.strip():
            raise ValueError("stdio command must be a non-empty string")
        args = _parse_string_list(raw.get("args", []), field_name="args")
        env = _parse_string_mapping(raw.get("env", {}), field_name="env", environ=environ)
        url = None
        headers: dict[str, str] = {}
    else:
        url_value = raw.get("url")
        if not isinstance(url_value, str) or not _is_http_url(url_value):
            raise ValueError("streamable_http url must be an http(s) URL")
        command = None
        args = ()
        env = {}
        url = url_value
        headers = _parse_string_mapping(
            raw.get("headers", {}), field_name="headers", environ=environ
        )

    return MCPServerConfig(
        name=name,
        transport=transport,
        timeout_seconds=timeout,
        command=command,
        args=tuple(args),
        env=env,
        url=url,
        headers=headers,
        read_tools=frozenset(read_tools),
    )


def _parse_string_list(raw: object, *, field_name: str) -> tuple[str, ...]:
    if not isinstance(raw, list):
        raise ValueError(f"{field_name} must be a list")
    if any(not isinstance(value, str) or not value for value in raw):
        raise ValueError(f"{field_name} must contain non-empty strings")
    if len(set(raw)) != len(raw):
        raise ValueError(f"{field_name} must not contain duplicates")
    return tuple(raw)


def _parse_string_mapping(
    raw: object,
    *,
    field_name: str,
    environ: Mapping[str, str],
) -> dict[str, str]:
    if not isinstance(raw, dict):
        raise ValueError(f"{field_name} must be a mapping")

    resolved: dict[str, str] = {}
    for key, value in raw.items():
        if not isinstance(key, str) or not key or not isinstance(value, str):
            raise ValueError(f"{field_name} keys and values must be strings")
        resolved[key] = _resolve_environment_references(value, environ)
    return resolved


def _resolve_environment_references(value: str, environ: Mapping[str, str]) -> str:
    def replace(match: re.Match[str]) -> str:
        name = match.group(1)
        replacement = environ.get(name)
        if not replacement:
            raise ValueError(f"environment variable is not set: {name}")
        return replacement

    return ENV_REFERENCE_PATTERN.sub(replace, value)


def _is_http_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _diagnostic_server_name(raw: object) -> str | None:
    if not isinstance(raw, dict):
        return None
    name = raw.get("name")
    return name if isinstance(name, str) and name else None
