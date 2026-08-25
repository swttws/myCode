from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

from mycode.log_context import current_log_identity


LOG_FILE_ENV = "MYCODE_LOG_FILE"
LOG_LEVEL_ENV = "MYCODE_LOG_LEVEL"


def configure_dev_logging(log_file: str | Path, *, console: bool = False) -> Path:
    log_path = Path(log_file).resolve()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    level = _log_level_from_env()
    formatter = _DevLogFormatter(
        "%(asctime)s [%(levelname)s] %(name)s%(subagent_identity)s%(team_context)s: %(message_zh)s"
    )
    handlers: list[logging.Handler] = [logging.FileHandler(log_path, encoding="utf-8")]
    if console:
        handlers.append(logging.StreamHandler(sys.stderr))
    for handler in handlers:
        handler.setFormatter(formatter)
        handler.addFilter(_TeamNoiseFilter())

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
        record.team_context = _format_team_context(record)  # type: ignore[attr-defined]
        record.message_zh = _format_message_zh(record)  # type: ignore[attr-defined]
        return super().format(record)


class _TeamNoiseFilter(logging.Filter):
    """Keep configured Team logs focused on work and returned results."""

    _NOISY_EVENTS = {
        "team.lock.acquire.started",
        "team.lock.acquired",
        "team.lock.released",
        "team.lock.reclaimed.missing",
        "team.lock.reclaimed.dead_owner",
        "team.status.started",
        "team.status.completed",
        "team.activate.started",
        "team.message.send.started",
        "team.member.wake.started",
        "team.member.wake.completed",
        "team.runtime.started",
        "team.runtime.idle",
        "team.runtime.message.started",
        "team.runtime.message.completed",
        "team.worker.runtime.created",
        "team.worker.runtime.started",
        "team.worker.runtime.completed",
    }

    def filter(self, record: logging.LogRecord) -> bool:
        if (
            record.getMessage() == "team.runtime.message.failed"
            and getattr(record, "task_id", None)
        ):
            return False
        if record.levelno >= logging.WARNING or not record.name.startswith("mycode.team"):
            return True
        return record.getMessage() not in self._NOISY_EVENTS


def _format_subagent_identity(record: logging.LogRecord) -> str:
    identity = current_log_identity()
    agent_type = getattr(record, "agent_type", None)
    role_name = getattr(record, "role_name", None)
    agent_role = getattr(record, "agent_role", None) or identity.agent_role
    task_id = getattr(record, "task_id", None) or identity.task_id
    sequence = getattr(record, "sequence", None)
    if not any((agent_role, agent_type, role_name, task_id, sequence)):
        return ""
    parts = []
    if agent_role:
        parts.append(f"角色={agent_role}")
    if agent_type:
        parts.append(f"代理类型={agent_type}")
    if role_name:
        parts.append(f"角色名称={role_name}")
    if task_id and not identity.team_name and not getattr(record, "team_name", None):
        parts.append(f"任务={task_id}")
    if sequence not in (None, 0):
        parts.append(f"序号={sequence}")
    return " [" + " ".join(parts) + "]"


def _format_team_context(record: logging.LogRecord) -> str:
    identity = current_log_identity()
    fields = (
        "team_name",
        "member_name",
        "role_name",
        "batch_id",
        "task_id",
        "tool_name",
        "message_id",
        "event_id",
        "target_name",
        "recipient_name",
        "protocol",
        "action",
        "phase",
        "state",
        "status",
        "duration_ms",
        "error_code",
        "reason_code",
        "task_summary",
        "result_summary",
        "path",
    )
    labels = {
        "team_name": "团队",
        "member_name": "成员",
        "role_name": "角色名称",
        "batch_id": "批次",
        "task_id": "任务",
        "tool_name": "工具",
        "message_id": "消息",
        "event_id": "事件编号",
        "target_name": "目标",
        "recipient_name": "接收方",
        "protocol": "协议",
        "action": "操作",
        "phase": "阶段",
        "state": "状态",
        "status": "状态",
        "duration_ms": "耗时毫秒",
        "error_code": "错误码",
        "reason_code": "原因码",
        "task_summary": "任务摘要",
        "result_summary": "结果",
        "path": "路径",
    }
    parts: list[str] = []
    for field in fields:
        value = getattr(record, field, None)
        if value is None or value == "":
            value = getattr(identity, field, None)
        if value is None or value == "":
            continue
        if field == "phase":
            value = _TEAM_PHASE_ZH.get(str(value), value)
        elif field in {"state", "status"}:
            value = _TEAM_STATE_ZH.get(str(value), value)
        parts.append(f"{labels[field]}={value}")
    if not parts:
        return ""
    return " [" + " ".join(parts) + "]"


_TEAM_EVENT_ZH = {
    "team": "团队",
    "member": "成员",
    "batch": "批次",
    "runtime": "运行时",
    "message": "消息",
    "task": "任务",
    "lead": "Lead",
    "result": "返回结果",
    "dispatch": "分配",
    "worker": "工作进程",
    "lock": "锁",
    "activate": "激活",
    "archive": "归档",
    "session": "会话",
    "status": "状态",
    "send": "发送",
    "integrate": "整合",
    "spawn": "派生",
    "terminate": "终止",
    "wake": "唤醒",
    "started": "开始",
    "completed": "已完成",
    "failed": "失败",
    "created": "已创建",
    "acquired": "已获取",
    "released": "已释放",
    "reclaimed": "已回收",
    "cleared": "已清理",
    "sent": "已发送",
    "idle": "空闲",
    "blocked": "已阻塞",
}
_TEAM_EVENT_MESSAGE_ZH = {
    "team.task.started": "任务开始执行",
    "team.task.result": "任务返回结果",
    "team.task.completed": "任务已完成",
    "team.task.failed": "任务执行失败",
    "team.lead.started": "Lead开始执行",
    "team.lead.result": "Lead返回结果",
    "team.lead.failed": "Lead执行失败",
    "team.message.sent": "团队消息已发送",
    "team.runtime.message.failed": "成员消息处理失败",
    "team.consumer.event.failed": "团队事件处理失败",
}
_TEAM_PHASE_ZH = {
    "inactive": "未激活",
    "lead_ready": "Lead 就绪",
    "task_planning": "任务规划",
    "dispatch_ready": "可派生成员",
    "executing": "成员执行",
    "integrating": "本地整合",
    "archived": "已归档",
    "running": "运行中",
    "spawn": "成员派生",
    "message": "消息处理",
}
_TEAM_STATE_ZH = {
    "active": "活动",
    "pending": "待处理",
    "running": "运行中",
    "completed": "已完成",
    "failed": "失败",
    "blocked": "已阻塞",
    "archived": "已归档",
}


def _format_message_zh(record: logging.LogRecord) -> str:
    message = record.getMessage()
    if not message.startswith("team."):
        return message
    words = message.split(".")
    translated = _TEAM_EVENT_MESSAGE_ZH.get(
        message,
        "·".join(_TEAM_EVENT_ZH.get(word, word) for word in words),
    )
    record.event_code = message  # type: ignore[attr-defined]
    return f"事件={translated} 事件码={message}"
