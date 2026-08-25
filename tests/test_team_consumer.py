import asyncio
from pathlib import Path

from mycode.team.domain.models import TeamEventState
from mycode.team.execution.consumer import RoleEventConsumer
from mycode.team.execution.notifier import TeamEventNotifier
from tests.test_team_events import make_event_store, make_message


def test_consumer_processes_role_events_serially_in_sequence_order(tmp_path: Path):
    async def scenario():
        events = make_event_store(tmp_path)
        events.register_role("dev")
        events.append_message(make_message("one"), recipients=("dev",))
        events.append_message(make_message("two"), recipients=("dev",))
        notifier = TeamEventNotifier()
        seen = []

        async def handler(event):
            seen.append(event.message.message_id)

        consumer = RoleEventConsumer("dev", events=events, notifier=notifier, handler=handler)
        await notifier.notify("dev")
        task = asyncio.create_task(consumer.run())
        await asyncio.sleep(0.05)
        await consumer.stop()
        await asyncio.wait_for(task, timeout=1)
        assert seen == ["one", "two"]

    asyncio.run(scenario())


def test_consumer_records_terminal_failure_after_three_attempts(tmp_path: Path):
    async def scenario():
        events = make_event_store(tmp_path)
        events.register_role("dev")
        events.append_message(make_message("one"), recipients=("dev",))
        notifier = TeamEventNotifier()
        failures = []

        async def handler(_event):
            raise RuntimeError("broken")

        async def terminal(failure):
            failures.append(failure)

        consumer = RoleEventConsumer("dev", events=events, notifier=notifier, handler=handler, on_terminal_failure=terminal)
        await notifier.notify("dev")
        task = asyncio.create_task(consumer.run())
        await asyncio.sleep(0.15)
        await consumer.stop()
        await asyncio.wait_for(task, timeout=1)
        assert len(failures) == 1

    asyncio.run(scenario())


def test_consumer_retries_when_acknowledgement_fails(tmp_path: Path):
    async def scenario():
        events = make_event_store(tmp_path)
        events.register_role("dev")
        events.append_message(make_message("one"), recipients=("dev",))
        notifier = TeamEventNotifier()
        handled = []
        failures = []

        async def handler(event):
            handled.append(event.message.message_id)

        def failing_ack(_role_name: str, _event_id: str):
            raise OSError("event log is temporarily unavailable")

        async def terminal(failure):
            failures.append(failure)

        events.ack_event = failing_ack
        consumer = RoleEventConsumer(
            "dev",
            events=events,
            notifier=notifier,
            handler=handler,
            on_terminal_failure=terminal,
        )

        await consumer.run_until_idle()

        stored = events.events_for_role("dev")[0]
        assert handled == ["one", "one", "one"]
        assert stored.state is TeamEventState.FAILED
        assert stored.attempts == 3
        assert len(failures) == 1
        assert failures[0].reason_code == "ack_error"

    asyncio.run(scenario())
