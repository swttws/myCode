from __future__ import annotations

from collections.abc import Sequence
from html import escape

from mycode.prompt.models import SystemReminder


class ReminderPolicy:
    def __init__(self, full_interval_rounds: int) -> None:
        if full_interval_rounds < 1:
            raise ValueError("full_interval_rounds must be positive")
        self._full_interval_rounds = full_interval_rounds

    def mode_reminder(self, *, plan_only: bool) -> SystemReminder | None:
        if not plan_only:
            return None
        return SystemReminder(
            id="plan-only",
            full_content=(
                "Plan-only mode is active. Read tools are allowed; write tools require user approval."
            ),
            concise_content="Plan-only mode remains active; do not assume write approval.",
        )

    def render(self, reminders: Sequence[SystemReminder], round_index: int) -> str | None:
        if not reminders:
            return None
        if round_index < 1:
            raise ValueError("round_index must be positive")

        # 首轮及每个固定间隔重复完整提醒，避免长工具循环遗忘会话模式。
        use_full_content = (round_index - 1) % self._full_interval_rounds == 0
        contents = [
            reminder.full_content if use_full_content else reminder.concise_content
            for reminder in sorted(reminders, key=lambda reminder: reminder.id)
        ]
        # 外层 XML 标签由 Builder 负责；这里先转义每段可信提醒文本。
        return "\n".join(escape(content, quote=True) for content in contents)
