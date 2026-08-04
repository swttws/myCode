from __future__ import annotations

from dataclasses import replace

from mycode.prompt.models import PromptContextBlock
from mycode.subagent.models import (
    SubAgentNotification,
    SubAgentTaskState,
    NotificationReservation,
    truncate_utf8_bytes,
)


class SubAgentNotificationInbox:
    def __init__(
        self,
        *,
        max_pending: int = 256,
        max_per_reservation: int = 16,
        max_notification_bytes: int = 4 * 1024,
        max_block_bytes: int = 32 * 1024,
    ) -> None:
        self._max_pending = max_pending
        self._max_per_reservation = max_per_reservation
        self._max_notification_bytes = max_notification_bytes
        self._max_block_bytes = max_block_bytes
        self._pending: list[tuple[int, SubAgentNotification]] = []
        self._reserved: dict[str, tuple[tuple[int, SubAgentNotification], ...]] = {}
        self._reserved_dropped: dict[str, int] = {}
        self._dropped_count = 0
        self._next_reservation = 1

    @property
    def pending_count(self) -> int:
        return len(self._pending)

    def enqueue(self, *, sequence: int, notification: SubAgentNotification) -> None:
        if len(self._pending) >= self._max_pending:
            self._pending.sort(key=lambda item: item[0])
            self._pending.pop(0)
            self._dropped_count += 1
        summary, truncated = truncate_utf8_bytes(
            notification.summary,
            self._max_notification_bytes,
        )
        normalized = replace(
            notification,
            summary=summary,
            summary_truncated=notification.summary_truncated or truncated,
        )
        self._pending.append((sequence, normalized))
        self._pending.sort(key=lambda item: item[0])

    def reserve(self) -> NotificationReservation | None:
        if self._reserved or not self._pending:
            return None

        selected = self._select_batch()
        if not selected:
            return None
        for item in selected:
            self._pending.remove(item)

        reservation_id = f"subagent-notification-{self._next_reservation:06d}"
        self._next_reservation += 1
        dropped_count = self._dropped_count
        self._dropped_count = 0
        self._reserved[reservation_id] = tuple(selected)
        self._reserved_dropped[reservation_id] = dropped_count
        notifications = tuple(notification for _sequence, notification in selected)
        return NotificationReservation(
            id=reservation_id,
            notifications=notifications,
            dropped_count=dropped_count,
            block=self._render_block(reservation_id, notifications, dropped_count),
        )

    def commit(self, reservation_id: str) -> None:
        if reservation_id not in self._reserved:
            raise KeyError(f"notification_reservation_not_found: {reservation_id}")
        self._reserved.pop(reservation_id, None)
        self._reserved_dropped.pop(reservation_id, None)

    def release(self, reservation_id: str) -> None:
        entries = self._reserved.pop(reservation_id, None)
        if entries is None:
            raise KeyError(f"notification_reservation_not_found: {reservation_id}")
        self._dropped_count += self._reserved_dropped.pop(reservation_id, 0)
        self._pending.extend(entries)
        self._pending.sort(key=lambda item: item[0])

    def clear(self) -> None:
        self._pending.clear()
        self._reserved.clear()
        self._reserved_dropped.clear()
        self._dropped_count = 0

    def _select_batch(self) -> tuple[tuple[int, SubAgentNotification], ...]:
        selected: list[tuple[int, SubAgentNotification]] = []
        for entry in self._pending:
            candidate = (*selected, entry)
            notifications = tuple(notification for _sequence, notification in candidate)
            block = self._render_block("preview", notifications, self._dropped_count)
            if (
                selected
                and len(block.content.encode("utf-8")) > self._max_block_bytes
            ):
                break
            selected.append(entry)
            if len(selected) >= self._max_per_reservation:
                break
        return tuple(selected)

    def _render_block(
        self,
        reservation_id: str,
        notifications: tuple[SubAgentNotification, ...],
        dropped_count: int,
    ) -> PromptContextBlock:
        lines = ["子 Agent 后台任务完成通知："]
        if dropped_count:
            lines.append(f"另有 {dropped_count} 条较早通知因留存上限被丢弃。")
        for notification in notifications:
            lines.append(
                " - "
                f"{_identity_label(notification)} ({notification.task_id}) {_state_zh(notification.state)}："
                f"{notification.summary}；usage={_usage_text(notification)}"
            )
        first_notification_line = len(lines) - len(notifications)
        for index, notification in enumerate(notifications, start=first_notification_line):
            lines[index] += _workspace_text(notification)
        content = "\n".join(lines)
        if len(content.encode("utf-8")) > self._max_block_bytes:
            content, _truncated = truncate_utf8_bytes(content, self._max_block_bytes)
        return PromptContextBlock(
            id=reservation_id,
            kind="subagent_notifications",
            priority=80,
            content=content,
        )


def _state_zh(state: SubAgentTaskState) -> str:
    return {
        SubAgentTaskState.COMPLETED: "已完成",
        SubAgentTaskState.FAILED: "失败",
        SubAgentTaskState.CANCELLED: "已取消",
        SubAgentTaskState.RUNNING: "运行中",
        SubAgentTaskState.QUEUED: "排队中",
    }[state]


def _usage_text(notification: SubAgentNotification) -> str:
    usage = notification.usage
    return (
        f"input={_value(usage.input_tokens)}, "
        f"output={_value(usage.output_tokens)}, "
        f"total={_value(usage.total_tokens)}, "
        f"cache-read={_value(usage.cache_read_tokens)}, "
        f"cache-write={_value(usage.cache_write_tokens)}"
    )


def _value(value: int | None) -> str:
    return "未知" if value is None else str(value)


def _identity_label(notification: SubAgentNotification) -> str:
    role_name = notification.role_name or "fork"
    suffix = notification.task_id.rsplit("-", 1)[-1] if "-" in notification.task_id else notification.task_id
    return f"{role_name}#{suffix}"


def _workspace_text(notification: SubAgentNotification) -> str:
    if (
        notification.isolation.value == "shared"
        and notification.workspace_root is None
        and notification.branch_name is None
        and notification.workspace_preparation is None
        and not notification.initialized_rules
        and notification.disposition is None
    ):
        return ""

    parts = [
        f"isolation={notification.isolation.value}",
        f"workspace={_optional_path(notification.workspace_root)}",
        f"branch={_optional_text(notification.branch_name)}",
        "preparation="
        + (
            notification.workspace_preparation.value
            if notification.workspace_preparation is not None
            else "未知"
        ),
        "rules=" + _list_text(notification.initialized_rules),
    ]
    if notification.disposition is not None:
        parts.append(f"disposition={notification.disposition.disposition.value}")
        parts.append("reasons=" + _list_text(notification.disposition.reasons))
    return "；" + ", ".join(parts)


def _optional_path(value) -> str:
    return "未知" if value is None else str(value)


def _optional_text(value: str | None) -> str:
    return "未知" if value is None else value


def _list_text(values: tuple[str, ...]) -> str:
    return ", ".join(values) if values else "无"
