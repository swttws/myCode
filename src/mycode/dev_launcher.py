from __future__ import annotations

import argparse
import logging
import os
import tempfile
from collections.abc import Callable
from pathlib import Path

from mycode import cli
from mycode.dev_logging import configure_dev_logging


logger = logging.getLogger(__name__)


CliMain = Callable[[list[str]], int]


class DevLauncher:
    """供 IDEA 调试使用的开发启动类，直接复用 IDE 内部控制台运行 CLI。"""

    def __init__(
        self,
        *,
        workspace_root: str | Path | None = None,
        config_path: str | Path | None = None,
        log_file: str | Path | None = None,
        cli_main: CliMain | None = None,
    ) -> None:
        self.workspace_root = Path(workspace_root or Path.cwd()).resolve()
        self.config_path = _resolve_optional_path(config_path, self.workspace_root)
        self.log_file = Path(log_file or _default_log_file()).resolve()
        self._cli_main = cli_main or cli.main

    def run(self) -> int:
        self._prepare_log_file()
        configure_dev_logging(self.log_file, console=True)
        logger.info("启动 myCode 开发启动器，工作区：%s", self.workspace_root)
        logger.info("日志文件：%s", self.log_file)

        previous_cwd = Path.cwd()
        os.chdir(self.workspace_root)
        try:
            exit_code = self._cli_main(self._cli_args())
            logger.info("CLI 退出，退出码：%s", exit_code)
            return exit_code
        except KeyboardInterrupt:
            logger.info("收到中断信号，CLI 已停止。")
            return 130
        finally:
            os.chdir(previous_cwd)

    def _prepare_log_file(self) -> None:
        self.log_file.parent.mkdir(parents=True, exist_ok=True)
        self.log_file.write_text("", encoding="utf-8")

    def _cli_args(self) -> list[str]:
        command: list[str] = []
        if self.config_path is not None:
            command.extend(["--config", str(self.config_path)])
        return command


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="mycode-dev")
    parser.add_argument("--workspace", type=Path, default=Path.cwd(), help="CLI 的工作目录。")
    parser.add_argument("--config", type=Path, default=None, help="传给 CLI 的 myCode YAML 配置文件。")
    parser.add_argument("--log-file", type=Path, default=None, help="开发调试日志文件路径。")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    launcher = DevLauncher(
        workspace_root=args.workspace,
        config_path=args.config,
        log_file=args.log_file,
    )
    return launcher.run()


def _resolve_optional_path(path: str | Path | None, workspace_root: Path) -> Path | None:
    if path is None:
        return None
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = workspace_root / candidate
    return candidate.resolve()


def _default_log_file() -> Path:
    return Path(tempfile.gettempdir()) / "mycode-dev.log"


if __name__ == "__main__":
    raise SystemExit(main())
