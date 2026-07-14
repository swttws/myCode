from __future__ import annotations

import logging
import os
import sys
from pathlib import Path


LOG_FILE_ENV = "MYCODE_LOG_FILE"
LOG_LEVEL_ENV = "MYCODE_LOG_LEVEL"


def configure_dev_logging(log_file: str | Path, *, console: bool = False) -> Path:
    """配置开发调试日志，把运行信息写入指定文件。"""

    log_path = Path(log_file).resolve()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    level = _log_level_from_env()
    formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    handlers: list[logging.Handler] = [logging.FileHandler(log_path, encoding="utf-8")]
    if console:
        handlers.append(logging.StreamHandler(sys.stderr))
    for handler in handlers:
        handler.setFormatter(formatter)

    logging.basicConfig(level=level, handlers=handlers, force=True)
    return log_path


def configure_dev_logging_from_env(*, console: bool = False) -> Path | None:
    """如果环境变量指定了日志文件，就启用开发调试日志。"""

    log_file = os.environ.get(LOG_FILE_ENV)
    if not log_file:
        return None
    return configure_dev_logging(log_file, console=console)


def _log_level_from_env() -> int:
    level_name = os.environ.get(LOG_LEVEL_ENV, "INFO").upper()
    return int(getattr(logging, level_name, logging.INFO))
