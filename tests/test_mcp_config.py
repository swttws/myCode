from __future__ import annotations

from pathlib import Path

import pytest

from mycode.mcp import MCPConfigError, MCPTransportKind, load_mcp_config


def write_config(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_load_mcp_config_returns_disabled_config_when_no_file_exists(tmp_path):
    config, diagnostics = load_mcp_config(cwd=tmp_path / "work", home=tmp_path / "home")

    assert config.servers == ()
    assert diagnostics == ()


def test_load_mcp_config_prefers_cwd_over_user_config(tmp_path):
    cwd = tmp_path / "work"
    home = tmp_path / "home"
    write_config(
        cwd / "mycode.mcp.yaml",
        """
servers:
  - name: workspace
    transport: stdio
    timeout_seconds: 5
    command: python
""",
    )
    write_config(
        home / ".mycode" / "mcp.yaml",
        """
servers:
  - name: user
    transport: stdio
    timeout_seconds: 5
    command: python
""",
    )

    config, _ = load_mcp_config(cwd=cwd, home=home)

    assert [server.name for server in config.servers] == ["workspace"]


def test_explicit_config_takes_priority_and_loads_both_transports(tmp_path):
    explicit = write_config(
        tmp_path / "explicit.yaml",
        """
servers:
  - name: files
    transport: stdio
    timeout_seconds: 7.5
    command: python
    args: [server.py, --stdio]
    env:
      MCP_TOKEN: ${STDIO_TOKEN}
    read_tools: [read_file]
  - name: remote
    transport: streamable_http
    timeout_seconds: 12
    url: https://example.invalid/mcp
    headers:
      Authorization: Bearer ${HTTP_TOKEN}
""",
    )

    config, diagnostics = load_mcp_config(
        explicit,
        cwd=tmp_path / "unused",
        home=tmp_path / "unused-home",
        environ={"STDIO_TOKEN": "stdio-secret", "HTTP_TOKEN": "http-secret"},
    )

    assert diagnostics == ()
    assert [server.transport for server in config.servers] == [
        MCPTransportKind.STDIO,
        MCPTransportKind.STREAMABLE_HTTP,
    ]
    assert config.servers[0].args == ("server.py", "--stdio")
    assert config.servers[0].env == {"MCP_TOKEN": "stdio-secret"}
    assert config.servers[0].read_tools == frozenset({"read_file"})
    assert config.servers[1].headers == {"Authorization": "Bearer http-secret"}


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ("servers: {}", "servers must be a list"),
        ("unknown: []", "unknown top-level field"),
        ("servers: []\nextra: true", "unknown top-level field"),
    ],
)
def test_file_level_shape_errors_are_fatal(tmp_path, body, message):
    path = write_config(tmp_path / "mcp.yaml", body)

    with pytest.raises(MCPConfigError, match=message):
        load_mcp_config(path)


def test_explicit_missing_and_invalid_yaml_are_fatal(tmp_path):
    with pytest.raises(MCPConfigError, match="not found"):
        load_mcp_config(tmp_path / "missing.yaml")

    invalid = write_config(tmp_path / "invalid.yaml", "servers: [")
    with pytest.raises(MCPConfigError, match="Invalid YAML"):
        load_mcp_config(invalid)


@pytest.mark.parametrize(
    "server_body",
    [
        "name: ''\n    transport: stdio\n    timeout_seconds: 5\n    command: python",
        "name: bad\n    transport: websocket\n    timeout_seconds: 5",
        "name: bad\n    transport: stdio\n    timeout_seconds: 0\n    command: python",
        "name: bad\n    transport: stdio\n    timeout_seconds: 5",
        "name: bad\n    transport: streamable_http\n    timeout_seconds: 5",
        "name: bad\n    transport: stdio\n    timeout_seconds: 5\n    command: python\n    url: https://example.invalid",
        "name: bad\n    transport: stdio\n    timeout_seconds: 5\n    command: python\n    unexpected: true",
    ],
)
def test_invalid_server_is_skipped_without_blocking_valid_server(tmp_path, server_body):
    path = write_config(
        tmp_path / "mcp.yaml",
        f"""
servers:
  - {server_body}
  - name: valid
    transport: stdio
    timeout_seconds: 5
    command: python
""",
    )

    config, diagnostics = load_mcp_config(path)

    assert [server.name for server in config.servers] == ["valid"]
    assert len(diagnostics) == 1
    assert diagnostics[0].category == "config"


def test_duplicate_server_name_skips_later_entry(tmp_path):
    path = write_config(
        tmp_path / "mcp.yaml",
        """
servers:
  - name: duplicate
    transport: stdio
    timeout_seconds: 5
    command: first
  - name: duplicate
    transport: stdio
    timeout_seconds: 5
    command: second
""",
    )

    config, diagnostics = load_mcp_config(path)

    assert len(config.servers) == 1
    assert config.servers[0].command == "first"
    assert diagnostics[0].server_name == "duplicate"
    assert "duplicate" in diagnostics[0].message


def test_missing_environment_variable_diagnostic_does_not_leak_other_values(tmp_path):
    path = write_config(
        tmp_path / "mcp.yaml",
        """
servers:
  - name: secure
    transport: streamable_http
    timeout_seconds: 5
    url: https://example.invalid/mcp
    headers:
      Authorization: Bearer ${MISSING_TOKEN}
      X-Other: ${PRESENT_TOKEN}
  - name: valid
    transport: stdio
    timeout_seconds: 5
    command: python
""",
    )

    config, diagnostics = load_mcp_config(path, environ={"PRESENT_TOKEN": "do-not-leak"})

    assert [server.name for server in config.servers] == ["valid"]
    diagnostic = diagnostics[0]
    assert diagnostic.server_name == "secure"
    assert "MISSING_TOKEN" in diagnostic.message
    assert "do-not-leak" not in diagnostic.message


def test_read_tools_require_exact_nonempty_names(tmp_path):
    path = write_config(
        tmp_path / "mcp.yaml",
        """
servers:
  - name: invalid
    transport: stdio
    timeout_seconds: 5
    command: python
    read_tools: [read_file, ""]
""",
    )

    config, diagnostics = load_mcp_config(path)

    assert config.servers == ()
    assert "read_tools" in diagnostics[0].message

