import pytest

from mycode.subagent.models import (
    RESULT_TRUNCATED_MARKER,
    SubAgentNotification,
    SubAgentTaskState,
    SubAgentUsage,
)
from mycode.subagent.notifications import SubAgentNotificationInbox


def notification(task_id, state=SubAgentTaskState.COMPLETED, summary="完成"):
    return SubAgentNotification(
        task_id=task_id,
        state=state,
        summary=summary,
        summary_truncated=False,
        usage=SubAgentUsage(input_tokens=1, output_tokens=2),
    )


def test_notification_inbox_reserves_notifications_in_sequence_order_with_chinese_status():
    inbox = SubAgentNotificationInbox()
    inbox.enqueue(sequence=3, notification=notification("task-3", SubAgentTaskState.CANCELLED, "取消"))
    inbox.enqueue(sequence=1, notification=notification("task-1", SubAgentTaskState.COMPLETED, "完成"))
    inbox.enqueue(sequence=2, notification=notification("task-2", SubAgentTaskState.FAILED, "失败"))

    reservation = inbox.reserve()

    assert [item.task_id for item in reservation.notifications] == [
        "task-1",
        "task-2",
        "task-3",
    ]
    assert "已完成" in reservation.block.content
    assert "失败" in reservation.block.content
    assert "已取消" in reservation.block.content


def test_notification_inbox_truncates_single_summary_on_utf8_boundary():
    inbox = SubAgentNotificationInbox(max_notification_bytes=64)
    inbox.enqueue(sequence=1, notification=notification("task-1", summary="甲" * 200))

    reservation = inbox.reserve()
    [item] = reservation.notifications

    assert item.summary_truncated is True
    assert item.summary.endswith(RESULT_TRUNCATED_MARKER)
    assert len(item.summary.encode("utf-8")) <= 64
    assert "\ufffd" not in item.summary


def test_notification_inbox_reserve_limits_batch_count_and_block_bytes():
    inbox = SubAgentNotificationInbox(max_per_reservation=2, max_block_bytes=180)
    for index in range(5):
        inbox.enqueue(sequence=index, notification=notification(f"task-{index}", summary="x" * 80))

    first = inbox.reserve()

    assert len(first.notifications) <= 2
    assert len(first.block.content.encode("utf-8")) <= 180
    assert inbox.pending_count == 5 - len(first.notifications)


def test_notification_inbox_reservation_must_commit_or_release_before_reuse():
    inbox = SubAgentNotificationInbox()
    inbox.enqueue(sequence=1, notification=notification("task-1"))

    first = inbox.reserve()
    second = inbox.reserve()

    assert first is not None
    assert second is None
    inbox.commit(first.id)
    assert inbox.reserve() is None


def test_notification_inbox_release_restores_order_and_unknown_id_is_stable():
    inbox = SubAgentNotificationInbox()
    inbox.enqueue(sequence=1, notification=notification("task-1"))
    inbox.enqueue(sequence=2, notification=notification("task-2"))
    first = inbox.reserve()

    inbox.release(first.id)
    second = inbox.reserve()

    assert [item.task_id for item in second.notifications] == ["task-1", "task-2"]
    with pytest.raises(KeyError, match="notification_reservation_not_found"):
        inbox.commit("missing")
    with pytest.raises(KeyError, match="notification_reservation_not_found"):
        inbox.release("missing")


def test_notification_inbox_overflow_drops_oldest_and_reports_count():
    inbox = SubAgentNotificationInbox(max_pending=3)
    for index in range(5):
        inbox.enqueue(sequence=index, notification=notification(f"task-{index}"))

    reservation = inbox.reserve()

    assert [item.task_id for item in reservation.notifications] == [
        "task-2",
        "task-3",
        "task-4",
    ]
    assert reservation.dropped_count == 2
    assert "另有 2 条较早通知因留存上限被丢弃。" in reservation.block.content


def test_notification_inbox_clear_discards_pending_reserved_and_dropped_count():
    inbox = SubAgentNotificationInbox(max_pending=1)
    inbox.enqueue(sequence=1, notification=notification("task-1"))
    inbox.enqueue(sequence=2, notification=notification("task-2"))
    assert inbox.reserve() is not None

    inbox.clear()

    assert inbox.pending_count == 0
    assert inbox.reserve() is None
