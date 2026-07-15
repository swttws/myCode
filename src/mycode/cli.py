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
    return parser


def main(argv: list[str] | None = None) -> int:
    configure_dev_logging_from_env()
    args = build_parser().parse_args(argv)
    logger.info("启动 myCode CLI，配置文件：%s，工作目录：%s", args.config or "自动查找", Path.cwd())
    try:
        config = load_config(args.config)
        llm = create_llm(config)
    except (ConfigError, ProtocolError) as exc:
        logger.error("myCode 配置错误：%s", exc)
        print(f"myCode 配置错误：{exc}", file=sys.stderr)
        return 1

    # 主流程只组装抽象依赖，具体协议和记忆实现都被包在各自边界里。
    memory = InMemoryConversationMemory()
    tool_registry = create_default_tool_registry(Path.cwd())
    tool_executor = ToolExecutor(tool_registry)
    agent = AgentLoop(
        llm=llm,
        memory=memory,
        tool_executor=tool_executor,
        tool_registry=tool_registry,
    )
    session = ChatSession(agent=agent)
    tui = ChatTUI(session=session, show_thinking=config.thinking.show)
    exit_code = asyncio.run(tui.run())
    logger.info("myCode CLI 退出，退出码：%s", exit_code)
    return exit_code
