import asyncio

import pytest

from mycode.permission.models import PermissionMode
from mycode.subagent.models import (
    AgentModelTier,
    ParentAgentSnapshot,
    SubAgentConfig,
    SubAgentExecutionReport,
    SubAgentKind,
    SubAgentLaunchRequest,
    SubAgentResult,
    SubAgentTaskState,
    SubAgentUsage,
)
from mycode.subagent.notifications import SubAgentNotificationInbox
from mycode.subagent.service import (
    ForegroundWaitOutcome,
    ForegroundWaitResult,
    SubAgentService,
)
from mycode.subagent.tasks import SubAgentTaskManager


def config(*, max_concurrency=4):
    return SubAgentConfig(
        model_map={
            AgentModelTier.HAIKU: "haiku",
            AgentModelTier.SONNET: "sonnet",
            AgentModelTier.OPUS: "opus",
        },
        foreground_timeout_seconds=0.2,
        max_concurrency=max_concurrency,
    )


def parent_snapshot():
    return ParentAgentSnapshot(
        messages=(),
        tools=(),
        model_id="parent-model",
        max_rounds=8,
        permission_mode=PermissionMode.DEFAULT,
    )


def request(*, background=False, kind=SubAgentKind.DEFINED, role_name="general", task="task"):
    return SubAgentLaunchRequest(
        kind=kind,
        task=task,
        role_name=role_name if kind is SubAgentKind.DEFINED else None,
        requested_background=background or kind is SubAgentKind.FORK,
        parent=parent_snapshot(),
    )


def completed_report(name="done"):
    return SubAgentExecutionReport(
        state=SubAgentTaskState.COMPLETED,
        rounds=1,
        result=SubAgentResult(detail=f"{name} detail", summary=f"{name} summary"),
        error_code=None,
        error_message=None,
        usage=SubAgentUsage(input_tokens=1),
    )


class InstantRuntime:
    def __init__(self, name="done"):
        self.name = name
        self.calls = 0

    async def run(self, cancel_event):
        self.calls += 1
        return completed_report(self.name)


class ControlledRuntime:
    def __init__(self, name="controlled"):
        self.name = name
        self.started = asyncio.Event()
        self.finish = asyncio.Event()
        self.cancel_seen = asyncio.Event()
        self.calls = 0

    async def run(self, cancel_event):
        self.calls += 1
        self.started.set()
        finish_wait = asyncio.create_task(self.finish.wait())
        cancel_wait = asyncio.create_task(cancel_event.wait())
        done, pending = await asyncio.wait(
            (finish_wait, cancel_wait),
            return_when=asyncio.FIRST_COMPLETED,
        )
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        if cancel_wait in done:
            self.cancel_seen.set()
            return SubAgentExecutionReport(
                state=SubAgentTaskState.CANCELLED,
                rounds=0,
                result=None,
                error_code="cancelled",
                error_message="cancelled",
                usage=SubAgentUsage(),
            )
        return completed_report(self.name)


class FakeRuntimeFactory:
    def __init__(self, *runtimes):
        self.runtimes = list(runtimes)
        self.calls = []

    def create(self, launch_request, *, detached):
        self.calls.append((launch_request, detached))
        return self.runtimes.pop(0)


class ImmediateTerminalWaiter:
    async def wait(self, *, manager, task_id, timeout_seconds, detach_event):
        for _ in range(50):
            snapshot = manager.get(task_id)
            if snapshot.state in {
                SubAgentTaskState.COMPLETED,
                SubAgentTaskState.FAILED,
                SubAgentTaskState.CANCELLED,
            }:
                return ForegroundWaitResult(ForegroundWaitOutcome.TERMINAL, snapshot)
            await asyncio.sleep(0.01)
        raise AssertionError("task did not reach terminal state")


class TimeoutWaiter:
    def __init__(self):
        self.calls = []

    async def wait(self, *, manager, task_id, timeout_seconds, detach_event):
        self.calls.append((task_id, timeout_seconds))
        return ForegroundWaitResult(ForegroundWaitOutcome.TIMEOUT, manager.get(task_id))


class DetachWaiter:
    def __init__(self):
        self.started = asyncio.Event()

    async def wait(self, *, manager, task_id, timeout_seconds, detach_event):
        self.started.set()
        await detach_event.wait()
        return ForegroundWaitResult(ForegroundWaitOutcome.DETACHED, manager.get(task_id))


def make_service(*, cfg=None, inbox=None, runtimes, waiter=None):
    cfg = cfg or config()
    inbox = inbox or SubAgentNotificationInbox()
    manager = SubAgentTaskManager(config=cfg, notification_inbox=inbox)
    service = SubAgentService(
        config=cfg,
        runtime_factory=FakeRuntimeFactory(*runtimes),
        task_manager=manager,
        foreground_waiter=waiter or ImmediateTerminalWaiter(),
    )
    return service, manager, inbox


def test_service_returns_inline_result_when_foreground_finishes_before_threshold():
    async def scenario():
        service, _manager, inbox = make_service(runtimes=(InstantRuntime("inline"),))

        response = await service.run(request())

        assert response.inline is True
        assert response.task.state is SubAgentTaskState.COMPLETED
        assert response.task.result.detail == "inline detail"
        assert inbox.reserve() is None

    asyncio.run(scenario())


def test_service_detaches_explicit_background_and_fork_immediately():
    async def scenario():
        defined_runtime = ControlledRuntime("defined")
        fork_runtime = ControlledRuntime("fork")
        service, manager, _inbox = make_service(
            cfg=config(max_concurrency=1),
            runtimes=(defined_runtime, fork_runtime),
        )

        defined = await service.run(request(background=True))
        fork = await service.run(request(kind=SubAgentKind.FORK, task="fork task"))

        assert defined.inline is False
        assert defined.task.detached is True
        assert defined.task.state is SubAgentTaskState.RUNNING
        assert fork.inline is False
        assert fork.task.detached is True
        assert fork.task.state is SubAgentTaskState.QUEUED
        assert [summary.id for summary in service.list_tasks()] == ["task-000001", "task-000002"]
        await manager.cancel_all_and_clear()

    asyncio.run(scenario())


def test_service_foreground_timeout_detaches_without_cancelling_running_task():
    async def scenario():
        runtime = ControlledRuntime("slow")
        waiter = TimeoutWaiter()
        service, manager, inbox = make_service(runtimes=(runtime,), waiter=waiter)

        response = await service.run(request())

        assert response.inline is False
        assert response.task.detached is True
        assert response.task.state is SubAgentTaskState.RUNNING
        assert waiter.calls == [("task-000001", 0.2)]
        assert runtime.cancel_seen.is_set() is False

        runtime.finish.set()
        for _ in range(50):
            if manager.get("task-000001").state is SubAgentTaskState.COMPLETED:
                break
            await asyncio.sleep(0.01)
        assert inbox.reserve().notifications[0].task_id == "task-000001"

    asyncio.run(scenario())


def test_service_detach_active_releases_wait_and_allows_only_one_attached_foreground():
    async def scenario():
        first_runtime = ControlledRuntime("first")
        second_runtime = ControlledRuntime("second")
        waiter = DetachWaiter()
        service, manager, _inbox = make_service(
            runtimes=(first_runtime, second_runtime),
            waiter=waiter,
        )

        first_task = asyncio.create_task(service.run(request(task="first")))
        await waiter.started.wait()
        with pytest.raises(RuntimeError, match="foreground_task_already_active"):
            await service.run(request(task="second"))

        detached = await service.detach_active()
        first_response = await asyncio.wait_for(first_task, timeout=1)

        assert detached.id == "task-000001"
        assert detached.detached is True
        assert first_response.inline is False
        assert first_response.task.detached is True
        assert await service.detach_active() is None
        await manager.cancel_all_and_clear()

    asyncio.run(scenario())


def test_service_list_get_are_realtime_and_unknown_task_is_stable_error():
    async def scenario():
        service, _manager, _inbox = make_service(runtimes=(InstantRuntime("done"),))

        response = await service.run(request(background=True))
        for _ in range(50):
            detail = service.get_task(response.task.id)
            if detail.state is SubAgentTaskState.COMPLETED:
                break
            await asyncio.sleep(0.01)

        summaries = service.list_tasks()
        assert [summary.id for summary in summaries] == ["task-000001"]
        assert summaries[0].state is SubAgentTaskState.COMPLETED
        assert service.get_task("task-000001").result.summary == "done summary"
        with pytest.raises(KeyError, match="task_not_found"):
            service.get_task("task-unknown")

    asyncio.run(scenario())


def test_service_clear_resets_session_and_close_permanently_rejects_new_runs():
    async def scenario():
        running = ControlledRuntime("running")
        next_runtime = InstantRuntime("next")
        service, manager, _inbox = make_service(
            cfg=config(max_concurrency=1),
            runtimes=(running, next_runtime),
        )
        await service.run(request(background=True))
        await running.started.wait()

        await service.clear()

        assert running.cancel_seen.is_set()
        assert service.list_tasks() == ()
        response = await service.run(request(background=True))
        assert response.task.id == "task-000001"

        await service.close()
        await service.close()
        with pytest.raises(RuntimeError, match="subagent_service_closed"):
            await service.run(request(background=True))
        await manager.cancel_all_and_clear()

    asyncio.run(scenario())
