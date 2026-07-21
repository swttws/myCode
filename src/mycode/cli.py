from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

from mycode.config import ConfigError, load_config
from mycode.dev_logging import configure_dev_logging_from_env
from mycode.agent import AgentLoop
from mycode.memory import InMemoryConversationMemory
from mycode.mcp import (
    MCPConfig,
    MCPConfigError,
    MCPDiagnostic,
    MCPServerPool,
    load_mcp_config,
    register_mcp_tools,
)
from mycode.permission.models import PermissionConfigError
from mycode.permission.service import PermissionInterceptor, PermissionService
from mycode.protocols import ProtocolError, create_llm
from mycode.session import ChatSession
from mycode.tool import ToolExecutor, create_default_tool_registry
from mycode.tui import ChatTUI


logger = logging.getLogger(__name__)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="mycode")
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="Path to myCode YAML config.",
    )
    parser.add_argument(
        "--mcp-config",
        type=Path,
        default=None,
        help="Path to MCP server YAML config.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    configure_dev_logging_from_env()
    args = build_parser().parse_args(argv)
    logger.info(
        "启动 myCode CLI，配置文件：%s，MCP 配置：%s，工作目录：%s",
        args.config or "自动查找",
        args.mcp_config or "自动查找",
        Path.cwd(),
    )
    try:
        config = load_config(args.config)
        mcp_config, mcp_config_diagnostics = load_mcp_config(args.mcp_config)
        llm = create_llm(config)
    except (ConfigError, ProtocolError) as exc:
        logger.error("myCode 配置错误：%s", exc)
        print(f"myCode 配置错误：{exc}", file=sys.stderr)
        return 1
    except MCPConfigError as exc:
        logger.error("myCode MCP 配置错误：%s", exc)
        print(f"myCode MCP 配置错误：{exc}", file=sys.stderr)
        return 1

    try:
        permissions = PermissionService.create(Path.cwd())
    except PermissionConfigError as exc:
        logger.error("myCode 权限配置错误：%s", exc)
        print(f"myCode 权限配置错误：{exc}", file=sys.stderr)
        return 1

    exit_code = asyncio.run(
        _run_application(
            config=config,
            llm=llm,
            permissions=permissions,
            mcp_config=mcp_config,
            mcp_config_diagnostics=mcp_config_diagnostics,
        )
    )
    logger.info("myCode CLI 退出，退出码：%s", exit_code)
    return exit_code


async def _run_application(
    *,
    config,
    llm,
    permissions,
    mcp_config: MCPConfig,
    mcp_config_diagnostics: tuple[MCPDiagnostic, ...],
) -> int:
    pool = MCPServerPool(mcp_config)
    try:
        # 初始化失败以诊断形式上报；可用 server 和本地工具仍可继续启动。
        connection_diagnostics = await pool.initialize_all()
        _report_mcp_diagnostics(mcp_config_diagnostics + connection_diagnostics)

        # 权限服务和文件工具必须共享同一 PathGuard，避免策略检查与实际执行使用不同边界。
        memory = InMemoryConversationMemory()
        tool_registry = create_default_tool_registry(
            Path.cwd(), path_guard=permissions.path_guard
        )
        # 注册当前远端工具，并通过 pool listener 持续同步重连后的工具变化。
        register_mcp_tools(pool, tool_registry)
        tool_executor = ToolExecutor(tool_registry)
        permission_interceptor = PermissionInterceptor(permissions)
        agent = AgentLoop(
            llm=llm,
            memory=memory,
            tool_executor=tool_executor,
            tool_registry=tool_registry,
            permission=permission_interceptor,
        )
        session = ChatSession(agent=agent, permissions=permissions)
        tui = ChatTUI(session=session, show_thinking=config.thinking.show)
        return await tui.run()
    finally:
        # 无论 TUI 正常退出、抛错还是被取消，都要回收 HTTP 流和 stdio 子进程。
        await pool.close()


def _report_mcp_diagnostics(diagnostics: tuple[MCPDiagnostic, ...]) -> None:
    for diagnostic in diagnostics:
        server = diagnostic.server_name or "配置文件"
        transport = (
            diagnostic.transport.value if diagnostic.transport is not None else "unknown"
        )
        logger.warning(
            "MCP 诊断：server=%s，类别=%s，transport=%s，原因=%s",
            server,
            diagnostic.category,
            transport,
            diagnostic.message,
        )
        print(
            f"myCode MCP 警告：{server}：category={diagnostic.category}，"
            f"transport={transport}，{diagnostic.message}",
            file=sys.stderr,
        )
