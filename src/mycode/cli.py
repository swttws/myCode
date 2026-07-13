from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from mycode.config import ConfigError, load_config
from mycode.memory import InMemoryConversationMemory
from mycode.protocols import ProtocolError, create_llm
from mycode.session import ChatSession
from mycode.tool import ToolExecutor, create_default_tool_registry
from mycode.tui import ChatTUI


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
    args = build_parser().parse_args(argv)
    try:
        config = load_config(args.config)
        llm = create_llm(config)
    except (ConfigError, ProtocolError) as exc:
        print(f"myCode 配置错误：{exc}", file=sys.stderr)
        return 1

    # 主流程只组装抽象依赖，具体协议和记忆实现都被包在各自边界里。
    memory = InMemoryConversationMemory()
    tool_registry = create_default_tool_registry(Path.cwd())
    tool_executor = ToolExecutor(tool_registry)
    session = ChatSession(llm=llm, memory=memory, tool_executor=tool_executor)
    tui = ChatTUI(session=session, show_thinking=config.thinking.show)
    return asyncio.run(tui.run())
