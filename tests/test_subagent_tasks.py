import asyncio
from pathlib import Path

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
from mycode.subagent.tasks import SubAgentTaskManager
from mycode.workspace import (
    WorkspaceContext,
    WorkspaceKind,
    WorkspaceLease,
    WorkspacePreparation,
    WorkspaceTaskIdentity,
)
from mycode.worktree.models import WorktreeDisposition, WorktreeDispositionResult


def config(*, max_concurrency=4, max_queued_tasks=64, max_retained_tasks=256):
    return SubAgentConfig(
        model_map={
            AgentModelTier.HAIKU: "h",
            AgentModelTier.SONNET: "s",
            AgentModelTier.OPUS: "o",
        },
        max_concurrency=max_concurrency,
        max_queued_tasks=max_queued_tasks,
        max_retained_tasks=max_retained_tasks,
    )


def request(*, background=False):
    return SubAgentLaunchRequest(
        kind=SubAgentKind.DEFINED,
        task="task",
        role_name="general",
        requested_background=background,
        parent=ParentAgentSnapshot(
            messages=(),
            tools=(),
            model_id="model",
            max_rounds=8,
            permission_mode=PermissionMode.DEFAULT,
        ),
    )


class ControlledRunner:
    def __init__(self, name):
        self.name = name
        self.started = asyncio.Event()
        self.finish = asyncio.Event()
        self.calls = 0

    async def __call__(self, cancel_event):
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
            return SubAgentExecutionReport(
                state=SubAgentTaskState.CANCELLED,
                rounds=0,
                result=None,
                error_code="cancelled",
                error_message="cancelled",
                usage=SubAgentUsage(),
            )
        return SubAgentExecutionReport(
            state=SubAgentTaskState.COMPLETED,
            rounds=1,
            result=SubAgentResult(detail=f"{self.name} detail", summary=f"{self.name} summary"),
            error_code=None,
            error_message=None,
            usage=SubAgentUsage(input_tokens=1),
        )


class InstantRunner:
    def __init__(self, name, *, disposition=None):
        self.name = name
        self.calls = 0
        self.disposition = disposition

    async def __call__(self, cancel_event):
        self.calls += 1
        return SubAgentExecutionReport(
            state=SubAgentTaskState.COMPLETED,
            rounds=1,
            result=SubAgentResult(detail=self.name, summary=self.name),
            error_code=None,
            error_message=None,
            usage=SubAgentUsage(input_tokens=1),
            disposition=self.disposition,
        )


class CancelAwareRunner:
    def __init__(self):
        self.started = asyncio.Event()
        self.cancel_seen = asyncio.Event()

    async def __call__(self, cancel_event):
        self.started.set()
        await cancel_event.wait()
        self.cancel_seen.set()
        return SubAgentExecutionReport(
            state=SubAgentTaskState.CANCELLED,
            rounds=0,
            result=None,
            error_code="cancelled",
            error_message="cancelled",
            usage=SubAgentUsage(),
        )


async def wait_started(runner):
    await asyncio.wait_for(runner.started.wait(), timeout=1)


async def wait_state(manager, task_id, state):
    for _ in range(50):
        if manager.get(task_id).state is state:
            return
        await asyncio.sleep(0.01)
    raise AssertionError(f"{task_id} did not reach {state}")


def test_task_manager_uses_four_slots_and_starts_fifth_fifo():
    async def scenario():
        manager = SubAgentTaskManager(
            config=config(max_concurrency=4),
            notification_inbox=SubAgentNotificationInbox(),
        )
        runners = [ControlledRunner(str(index)) for index in range(5)]

        snapshots = [await manager.submit(request(), runner) for runner in runners]
        await asyncio.gather(*(wait_started(runner) for runner in runners[:4]))

        assert [snapshot.state for snapshot in snapshots] == [
            SubAgentTaskState.RUNNING,
            SubAgentTaskState.RUNNING,
            SubAgentTaskState.RUNNING,
            SubAgentTaskState.RUNNING,
            SubAgentTaskState.QUEUED,
        ]
        assert runners[4].calls == 0

        runners[0].finish.set()
        await wait_started(runners[4])
        await asyncio.sleep(0)

        assert runners[4].calls == 1
        assert manager.get("task-000005").state is SubAgentTaskState.RUNNING
        await manager.cancel_all_and_clear()

    asyncio.run(scenario())


def test_task_manager_uses_strict_fifo_for_multiple_queued_tasks():
    async def scenario():
        manager = SubAgentTaskManager(
            config=config(max_concurrency=1),
            notification_inbox=SubAgentNotificationInbox(),
        )
        runners = [ControlledRunner(str(index)) for index in range(4)]
        for runner in runners:
            await manager.submit(request(), runner)
        await wait_started(runners[0])

        for index in range(1, 4):
            runners[index - 1].finish.set()
            await wait_started(runners[index])
            assert manager.get(f"task-00000{index + 1}").state is SubAgentTaskState.RUNNING
        await manager.cancel_all_and_clear()

    asyncio.run(scenario())


def test_task_manager_foreground_and_background_share_same_slots():
    async def scenario():
        manager = SubAgentTaskManager(
            config=config(max_concurrency=1),
            notification_inbox=SubAgentNotificationInbox(),
        )
        first = ControlledRunner("foreground")
        second = ControlledRunner("background")

        first_snapshot = await manager.submit(request(background=False), first)
        second_snapshot = await manager.submit(request(background=True), second)

        assert first_snapshot.state is SubAgentTaskState.RUNNING
        assert second_snapshot.state is SubAgentTaskState.QUEUED
        await manager.cancel_all_and_clear()

    asyncio.run(scenario())


def test_task_manager_rejects_submit_when_queue_is_full_without_allocating_id():
    async def scenario():
        manager = SubAgentTaskManager(
            config=config(max_concurrency=1, max_queued_tasks=1),
            notification_inbox=SubAgentNotificationInbox(),
        )
        await manager.submit(request(), ControlledRunner("running"))
        await manager.submit(request(), ControlledRunner("queued"))

        with pytest.raises(RuntimeError, match="task_queue_full"):
            await manager.submit(request(), ControlledRunner("rejected"))

        assert [summary.id for summary in manager.list()] == ["task-000001", "task-000002"]
        await manager.cancel_all_and_clear()

    asyncio.run(scenario())


def test_task_manager_ids_are_monotonic_and_list_returns_lightweight_summary():
    async def scenario():
        manager = SubAgentTaskManager(
            config=config(max_concurrency=1),
            notification_inbox=SubAgentNotificationInbox(),
        )
        await manager.submit(request(), ControlledRunner("a"))
        await manager.submit(request(), ControlledRunner("b"))

        summaries = manager.list()

        assert [summary.id for summary in summaries] == ["task-000001", "task-000002"]
        assert [summary.task_token for summary in summaries] == ["task-000001", "task-000002"]
        assert [summary.sequence for summary in summaries] == [1, 2]
        assert not hasattr(summaries[0], "result")
        await manager.cancel_all_and_clear()

    asyncio.run(scenario())


def test_task_manager_binds_workspace_lease_to_snapshot_and_summary():
    async def scenario():
        manager = SubAgentTaskManager(
            config=config(max_concurrency=1),
            notification_inbox=SubAgentNotificationInbox(),
        )
        reserved = await manager.reserve(request())
        lease = _worktree_lease(reserved.id, reserved.task_token)

        snapshot = await manager.bind_workspace(reserved.id, lease)
        summary = manager.list()[0]

        assert snapshot.workspace_root == lease.context.root
        assert snapshot.branch_name == lease.context.branch_name
        assert snapshot.workspace_preparation is WorkspacePreparation.CREATED
        assert snapshot.initialized_rules == ("hooks:.mycode/hooks",)
        assert snapshot.isolation.value == "worktree"
        assert summary.workspace_root == snapshot.workspace_root
        assert summary.branch_name == snapshot.branch_name

    asyncio.run(scenario())


def test_task_manager_active_query_tracks_bound_worktree_until_terminal():
    async def scenario():
        manager = SubAgentTaskManager(
            config=config(max_concurrency=1),
            notification_inbox=SubAgentNotificationInbox(),
        )
        reserved = await manager.reserve(request())
        lease = _worktree_lease(reserved.id, reserved.task_token)
        await manager.bind_workspace(reserved.id, lease)
        assert manager.is_workspace_active(lease.context.task_identity) is True

        await manager.start_reserved(reserved.id, InstantRunner("done"))
        await wait_state(manager, reserved.id, SubAgentTaskState.COMPLETED)

        assert manager.is_workspace_active(lease.context.task_identity) is False

    asyncio.run(scenario())


def test_task_manager_records_disposition_in_snapshot_summary_and_notification():
    async def scenario():
        inbox = SubAgentNotificationInbox()
        manager = SubAgentTaskManager(config=config(), notification_inbox=inbox)
        reserved = await manager.reserve(request(background=True))
        lease = _worktree_lease(reserved.id, reserved.task_token)
        disposition = WorktreeDispositionResult(
            disposition=WorktreeDisposition.RETAINED,
            workspace_root=lease.context.root,
            branch_name=lease.context.branch_name,
            reasons=("未推送提交",),
        )
        await manager.bind_workspace(reserved.id, lease)
        await manager.start_reserved(reserved.id, InstantRunner("done", disposition=disposition))
        await wait_state(manager, reserved.id, SubAgentTaskState.COMPLETED)

        snapshot = manager.get(reserved.id)
        summary = manager.list()[0]
        notification = inbox.reserve().notifications[0]

        assert snapshot.disposition == disposition
        assert summary.disposition == disposition
        assert notification.disposition == disposition
        assert notification.workspace_root == lease.context.root

    asyncio.run(scenario())


def test_task_manager_detach_is_idempotent_and_keeps_queued_position():
    async def scenario():
        manager = SubAgentTaskManager(
            config=config(max_concurrency=1),
            notification_inbox=SubAgentNotificationInbox(),
        )
        first = ControlledRunner("first")
        second = ControlledRunner("second")
        await manager.submit(request(), first)
        await manager.submit(request(), second)

        detached_once = await manager.detach("task-000002")
        detached_twice = await manager.detach("task-000002")

        assert detached_once.detached is True
        assert detached_twice.detached is True
        assert detached_twice.state is SubAgentTaskState.QUEUED
        first.finish.set()
        await wait_started(second)
        assert manager.get("task-000002").state is SubAgentTaskState.RUNNING
        await manager.cancel_all_and_clear()

    asyncio.run(scenario())


def test_task_manager_detached_completion_enqueues_one_notification():
    async def scenario():
        inbox = SubAgentNotificationInbox()
        manager = SubAgentTaskManager(config=config(), notification_inbox=inbox)
        runner = InstantRunner("done")

        await manager.submit(request(background=True), runner)
        await wait_state(manager, "task-000001", SubAgentTaskState.COMPLETED)

        reservation = inbox.reserve()
        assert [item.task_id for item in reservation.notifications] == ["task-000001"]
        assert inbox.reserve() is None

    asyncio.run(scenario())


def test_task_manager_retains_only_newest_terminal_records(tmp_path):
    async def scenario():
        manager = SubAgentTaskManager(
            config=config(max_concurrency=3, max_retained_tasks=2),
            notification_inbox=SubAgentNotificationInbox(),
        )
        for index in range(3):
            await manager.submit(request(), InstantRunner(str(index)))
        await wait_state(manager, "task-000003", SubAgentTaskState.COMPLETED)

        with pytest.raises(KeyError, match="task_not_found"):
            manager.get("task-000001")
        assert [summary.id for summary in manager.list()] == ["task-000002", "task-000003"]

    asyncio.run(scenario())


def test_task_manager_cancel_all_and_clear_cancels_running_and_resets_session_ids():
    async def scenario():
        manager = SubAgentTaskManager(
            config=config(max_concurrency=1),
            notification_inbox=SubAgentNotificationInbox(),
        )
        running = CancelAwareRunner()
        await manager.submit(request(), running)
        await manager.submit(request(), ControlledRunner("queued"))
        await wait_started(running)

        await manager.cancel_all_and_clear()

        assert running.cancel_seen.is_set()
        assert manager.list() == ()
        snapshot = await manager.submit(request(), InstantRunner("new"))
        assert snapshot.id == "task-000001"

    asyncio.run(scenario())


def _worktree_lease(task_id: str, task_token: str | None) -> WorkspaceLease:
    assert task_token is not None
    identity = WorkspaceTaskIdentity(
        repository_id="repo-123",
        task_id=task_id,
        role_name="general",
        task_token=task_token,
        relative_name=f"general/{task_token}",
        branch_name=f"mycode/worktree/general/{task_token}",
        base_commit="a" * 40,
    )
    return WorkspaceLease(
        context=WorkspaceContext(
            kind=WorkspaceKind.WORKTREE,
            root=Path(f"C:/repo/.worktrees/general/{task_token}"),
            repository_root=Path("C:/repo"),
            repository_id=identity.repository_id,
            task_identity=identity,
            branch_name=identity.branch_name,
            hooks_path=None,
        ),
        preparation=WorkspacePreparation.CREATED,
        metadata_path=Path(f"C:/repo/.worktrees/.metadata/general/{task_token}.json"),
        initialized_rules=("hooks:.mycode/hooks",),
    )
