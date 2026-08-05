from typing import TYPE_CHECKING

from mycode.mcp.config import MCPConfigError, load_mcp_config
from mycode.mcp.models import (
    DeferredToolSummary,
    MCPConfig,
    MCPDiagnostic,
    MCPServerConfig,
    MCPServerState,
    MCPTransportKind,
    RemoteTool,
)
from mycode.mcp.tools import MCPToolWrapper, ToolSearch, register_mcp_tools

if TYPE_CHECKING:
    from mycode.mcp.pool import MCPServerPool

__all__ = [
    "DeferredToolSummary",
    "MCPConfig",
    "MCPConfigError",
    "MCPDiagnostic",
    "MCPServerConfig",
    "MCPServerPool",
    "MCPServerState",
    "MCPTransportKind",
    "MCPToolWrapper",
    "RemoteTool",
    "ToolSearch",
    "load_mcp_config",
    "register_mcp_tools",
]


def __getattr__(name: str):
    if name == "MCPServerPool":
        from mycode.mcp.pool import MCPServerPool

        return MCPServerPool
    raise AttributeError(name)
