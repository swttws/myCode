import asyncio

from mycode.team.execution.notifier import TeamEventNotifier


def test_notify_wakes_registered_role_queue():
    async def scenario():
        notifier = TeamEventNotifier()
        queue = notifier.register_queue("dev")
        assert await notifier.notify("dev") is True
        assert await asyncio.wait_for(queue.get(), timeout=0.1) is None

    asyncio.run(scenario())


def test_duplicate_notify_is_one_wakeup_signal():
    async def scenario():
        notifier = TeamEventNotifier()
        queue = notifier.register_queue("dev")
        await notifier.notify("dev")
        await notifier.notify("dev")
        assert queue.qsize() == 1

    asyncio.run(scenario())


def test_unknown_and_unregistered_roles_are_ignored():
    async def scenario():
        notifier = TeamEventNotifier()
        assert await notifier.notify("missing") is False
        notifier.register_queue("dev")
        notifier.unregister_queue("dev")
        assert await notifier.notify("dev") is False

    asyncio.run(scenario())
