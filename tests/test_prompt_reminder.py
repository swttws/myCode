from mycode.prompt.models import SystemReminder
from mycode.prompt.reminder import ReminderPolicy


def test_plan_only_mode_reminder_is_absent_when_disabled_and_repeats_every_four_rounds():
    policy = ReminderPolicy(4)

    assert policy.mode_reminder(plan_only=False) is None
    reminder = policy.mode_reminder(plan_only=True)
    assert reminder is not None
    assert reminder.full_content == "当前处于仅计划模式。允许使用读取工具；写入工具需要用户批准。"
    assert reminder.concise_content == "仅计划模式仍处于启用状态；不要假定已获写入批准。"
    assert policy.render((reminder,), 1) == reminder.full_content
    assert policy.render((reminder,), 2) == reminder.concise_content
    assert policy.render((reminder,), 5) == reminder.full_content
    assert policy.render((reminder,), 9) == reminder.full_content


def test_reminders_are_sorted_merged_and_xml_escaped():
    policy = ReminderPolicy(2)
    reminders = (
        SystemReminder("zeta", "z < full", "z < short"),
        SystemReminder("alpha", "a & full", "a & short"),
    )

    assert policy.render(reminders, 1) == "a &amp; full\nz &lt; full"
    assert policy.render(reminders, 2) == "a &amp; short\nz &lt; short"
