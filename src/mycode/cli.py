from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

from mycode.agent import AgentConfig, AgentLoop
from mycode.compact import create_context_manager
from mycode.config import ConfigError, load_config
from mycode.dev_logging import configure_dev_logging_from_env
from mycode.memory import InMemoryConversationMemory, create_project_memory_manager
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
from mycode.skill.catalog import SkillCatalog
from mycode.skill.executor import SkillExecutor
from mycode.skill.load_tool import SkillLoadTool
from mycode.skill.loader import SkillLoader
from mycode.skill.models import SkillDiagnostic, SkillStartupError
from mycode.skill.runtime import SkillRuntime
from mycode.skill.slash import SkillSlashBridge
from mycode.slash import (
    SlashCommandCompleter,
    SlashCommandDispatcher,
    SlashCommandRegistrationError,
    create_default_slash_registry,
)
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
    workspace_root = Path.cwd()
    logger.info(
        "启动 myCode CLI，配置文件：%s，MCP 配置：%s，工作目录：%s",
        args.config or "自动查找",
        args.mcp_config or "自动查找",
        workspace_root,
    )
    try:
        config = load_config(args.config)
        mcp_config, mcp_config_diagnostics = load_mcp_config(args.mcp_config)
        llm = create_llm(config)
        registry = create_default_slash_registry()
    except (ConfigError, ProtocolError) as exc:
        logger.error("myCode 配置错误：%s", exc)
        print(f"myCode 配置错误：{exc}", file=sys.stderr)
        return 1
    except MCPConfigError as exc:
        logger.error("myCode MCP 配置错误：%s", exc)
        print(f"myCode MCP 配置错误：{exc}", file=sys.stderr)
        return 1
    except SlashCommandRegistrationError as exc:
        logger.error("myCode slash 命令注册冲突：%s", exc)
        print(f"myCode slash 命令注册冲突：{exc}", file=sys.stderr)
        return 1

    try:
        permissions = PermissionService.create(workspace_root)
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
            workspace_root=workspace_root,
            registry=registry,
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
    workspace_root: Path,
    registry,
) -> int:
    pool = MCPServerPool(mcp_config)
    context_manager = None
    project_memory = None
    try:
        memory = InMemoryConversationMemory()
        tool_registry = create_default_tool_registry(
            workspace_root,
            path_guard=permissions.path_guard,
        )
        agent_config = AgentConfig()
        try:
            context_manager = create_context_manager(
                workspace_root=workspace_root,
                home=Path.home(),
                llm=llm,
                memory=memory,
                config=config.compact,
                model_timeout_seconds=agent_config.model_timeout_seconds,
            )
        except (OSError, RuntimeError, ValueError) as exc:
            logger.error("myCode 上下文缓存错误：%s", exc)
            print(f"myCode 上下文缓存错误：{exc}", file=sys.stderr)
            return 1
        tool_registry.register(context_manager.artifact_tool)
        try:
            project_memory = create_project_memory_manager(
                workspace_root=workspace_root,
                home=Path.home(),
                llm=llm,
                memory=memory,
                now=lambda: datetime.now(timezone.utc),
            )
        except (OSError, RuntimeError, ValueError) as exc:
            logger.error("myCode 项目记忆错误：%s", exc)
            print(f"myCode 项目记忆错误：{exc}", file=sys.stderr)
            return 1

        tool_registry.register(project_memory.memory_note_tool)
        connection_diagnostics = await pool.initialize_all()
        _report_mcp_diagnostics(mcp_config_diagnostics + connection_diagnostics)

        register_mcp_tools(pool, tool_registry)
        tool_executor = ToolExecutor(tool_registry)
        permission_interceptor = PermissionInterceptor(permissions)
        skill_loader = SkillLoader(
            workspace_root=workspace_root,
            home=Path.home(),
            builtin_root=_builtin_skill_root(),
        )
        skill_catalog = SkillCatalog(
            loader=skill_loader,
            tool_names=lambda: _tool_names(tool_registry),
            reserved_slash_names=_reserved_slash_names(registry),
        )
        skill_runtime = SkillRuntime(skill_catalog)
        skill_executor = SkillExecutor(
            runtime=skill_runtime,
            main_llm=llm,
            llm_config=config,
            llm_factory=create_llm,
            tool_registry=tool_registry,
            tool_executor=tool_executor,
            permission=permission_interceptor,
            agent_config=agent_config,
            workspace_root=workspace_root,
        )
        tool_registry.register(SkillLoadTool(runtime=skill_runtime, executor=skill_executor))
        try:
            skill_catalog.initialize()
            skill_slash_bridge = SkillSlashBridge(runtime=skill_runtime, registry=registry)
            _report_skill_diagnostics(skill_slash_bridge.refresh())
            dispatcher = SlashCommandDispatcher(registry, before_dispatch=skill_slash_bridge.refresh)
            completer = SlashCommandCompleter(registry, before_complete=skill_slash_bridge.refresh_silent)
        except SkillStartupError as exc:
            logger.error("myCode Skill 配置错误：%s", exc)
            print(f"myCode Skill 配置错误：{exc}", file=sys.stderr)
            return 1
        except SlashCommandRegistrationError as exc:
            logger.error("myCode slash 命令注册冲突：%s", exc)
            print(f"myCode slash 命令注册冲突：{exc}", file=sys.stderr)
            return 1
        agent = AgentLoop(
            llm=llm,
            memory=memory,
            tool_executor=tool_executor,
            tool_registry=tool_registry,
            permission=permission_interceptor,
            context_manager=context_manager,
            config=agent_config,
            project_memory=project_memory,
            skill_runtime=skill_runtime,
        )
        session = ChatSession(
            agent=agent,
            permissions=permissions,
            skill_runtime=skill_runtime,
            skill_executor=skill_executor,
        )
        tui = ChatTUI(
            session=session,
            dispatcher=dispatcher,
            registry=registry,
            completer=completer,
            mcp_pool=pool,
            workspace_root=workspace_root,
            show_thinking=config.thinking.show,
        )
        return await tui.run()
    finally:
        try:
            if project_memory is not None:
                await project_memory.close()
        finally:
            try:
                if context_manager is not None:
                    context_manager.close()
            finally:
                await pool.close()


def _builtin_skill_root() -> Path:
    return Path(__file__).resolve().parent / "skill" / "builtins"


def _tool_names(registry) -> frozenset[str]:
    return frozenset(definition.name for definition in registry.definitions())


def _reserved_slash_names(registry) -> frozenset[str]:
    commands = getattr(registry, "_static_commands", None)
    if commands is None:
        public_commands = getattr(registry, "public_commands", None)
        commands = public_commands() if callable(public_commands) else ()
    names: set[str] = set()
    for command in commands:
        names.add(command.name)
        names.update(command.aliases)
    return frozenset(names)


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
            f"myCode MCP 警告：{server}，category={diagnostic.category}，"
            f"transport={transport}，{diagnostic.message}",
            file=sys.stderr,
        )


def _report_skill_diagnostics(diagnostics: tuple[SkillDiagnostic, ...]) -> None:
    for diagnostic in diagnostics:
        logger.warning(
            "Skill 诊断：skill=%s，code=%s，path=%s，reason=%s",
            diagnostic.skill_name or "unknown",
            diagnostic.code,
            diagnostic.path,
            diagnostic.message,
        )
