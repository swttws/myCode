from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from mycode.mcp import (
    DeferredToolSummary,
    MCPConfig,
    MCPDiagnostic,
    MCPServerConfig,
    MCPServerState,
    MCPTransportKind,
    RemoteTool,
)
from mycode.tool import ToolKind


def make_stdio_config(**overrides) -> MCPServerConfig:
    values = {
        "name": "files",
        "transport": MCPTransportKind.STDIO,
        "timeout_seconds": 10.0,
        "command": "python",
        "args": ("server.py",),
        "env": {"TOKEN": "secret"},
        "url": None,
        "headers": {},
        "read_tools": frozenset({"read_file"}),
    }
    values.update(overrides)
    return MCPServerConfig(**values)


def test_mcp_domain_models_are_available_from_package_boundary():
    config = make_stdio_config()
    remote_tool = RemoteTool(
        server_name="files",
        remote_name="read_file",
        public_name="files__read_file",
        description="Read a file.",
        parameters={"type": "object"},
        kind=ToolKind.READ,
    )

    assert MCPConfig(servers=(config,)).servers == (config,)
    assert MCPDiagnostic("files", "connection", "connection failed").category == "connection"
    assert MCPServerState.READY.value == "ready"
    assert DeferredToolSummary("files__read_file", "Read a file.").name == remote_tool.public_name


def test_mcp_diagnostic_accepts_optional_transport_metadata():
    diagnostic = MCPDiagnostic(
        "files",
        "connection",
        "connection failed",
        transport=MCPTransportKind.STDIO,
    )

    assert diagnostic.transport is MCPTransportKind.STDIO
    assert MCPDiagnostic(None, "config", "invalid entry").transport is None


@pytest.mark.parametrize("name", ["", "with space", "bad/name", "__private"])
def test_server_config_rejects_invalid_stable_names(name):
    with pytest.raises(ValueError, match="server name"):
        make_stdio_config(name=name)


@pytest.mark.parametrize("timeout", [0, -1, float("inf"), float("nan"), True])
def test_server_config_rejects_invalid_timeouts(timeout):
    with pytest.raises(ValueError, match="timeout"):
        make_stdio_config(timeout_seconds=timeout)


def test_server_config_rejects_invalid_transport():
    with pytest.raises(ValueError, match="transport"):
        make_stdio_config(transport="websocket")


def test_server_config_copies_and_freezes_mapping_fields():
    source_env = {"TOKEN": "secret"}
    source_headers = {"Authorization": "Bearer secret"}
    config = make_stdio_config(env=source_env, headers=source_headers)

    source_env["TOKEN"] = "changed"
    source_headers["Authorization"] = "changed"

    assert config.env["TOKEN"] == "secret"
    assert config.headers["Authorization"] == "Bearer secret"
    with pytest.raises(TypeError):
        config.env["OTHER"] = "value"
    with pytest.raises(TypeError):
        config.headers["Other"] = "value"


def test_domain_models_are_frozen():
    config = make_stdio_config()

    with pytest.raises(FrozenInstanceError):
        config.name = "changed"
