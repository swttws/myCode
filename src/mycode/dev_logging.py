from __future__ import annotations

import logging
import os
import sys
from pathlib import Path


LOG_FILE_ENV = "MYCODE_LOG_FILE"
LOG_LEVEL_ENV = "MYCODE_LOG_LEVEL"


def configure_dev_logging(log_file: str | Path, *, console: bool = False) -> Path:
    log_path = Path(log_file).resolve()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    level = _log_level_from_env()
    formatter = _DevLogFormatter(
        "%(asctime)s [%(levelname)s] %(name)s%(subagent_identity)s: %(message)s"
    )
    handlers: list[logging.Handler] = [logging.FileHandler(log_path, encoding="utf-8")]
    if console:
        handlers.append(logging.StreamHandler(sys.stderr))
    for handler in handlers:
        handler.setFormatter(formatter)

    logging.basicConfig(level=level, handlers=handlers, force=True)
    return log_path


def configure_dev_logging_from_env(*, console: bool = False) -> Path | None:
    log_file = os.environ.get(LOG_FILE_ENV)
    if not log_file:
        return None
    return configure_dev_logging(log_file, console=console)


def _log_level_from_env() -> int:
    level_name = os.environ.get(LOG_LEVEL_ENV, "INFO").upper()
    return int(getattr(logging, level_name, logging.INFO))


class _DevLogFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        record.subagent_identity = _format_subagent_identity(record)  # type: ignore[attr-defined]
        return super().format(record)


def _format_subagent_identity(record: logging.LogRecord) -> str:
    agent_type = getattr(record, "agent_type", None)
    role_name = getattr(record, "role_name", None)
    task_id = getattr(record, "task_id", None)
    sequence = getattr(record, "sequence", None)
    if not any((agent_type, role_name, task_id, sequence)):
        return ""
    parts = []
    if agent_type:
        parts.append(f"agent_type={agent_type}")
    if role_name:
        parts.append(f"role_name={role_name}")
    if task_id:
        parts.append(f"task_id={task_id}")
    if sequence not in (None, 0):
        parts.append(f"sequence={sequence}")
    return " [" + " ".join(parts) + "]"
