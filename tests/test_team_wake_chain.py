"""真实接线端到端测试：in_process 唤醒链（stage-19 T9 / AC1 硬标准）。

与 test_team_e2e.py 的区别：本文件走 service → backend → runtime 真实链路，
member 由共享 notifier 信号自动唤醒消费，测试体禁止调用 next_event /
run_until_idle 等人工拉取手段驱动事件流。
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from mycode.team import (
    MemberBackend,
    MemberState,
    MessageProtocol,
    TaskKind,
    TeamEventState,
    TeamMessage,
    TeamTask,
    TeamTaskState,
)
from mycode.team.execution.backends import BackendSelector, InProcessBackend
from mycode.team.infrastructure.config import TeamConfig
from mycode.team.infrastructure.context import JsonConversationMemory
from mycode.team.infrastructure.events import LEAD_ROLE_NAME, TeamEventStore
from mycode.team.execution.notifier import TeamEventNotifier
from mycode.team.execution.runtime import TeamMemberRuntime
from mycode.team.application.service import TeamService
from mycode.team.infrastructure.storage import TeamStore
from tests.test_team_service import FakeWorktreeService


# ── helpers ───────────────────────────────────────────────────────────


class ScriptedAgent:
    """预录行为的 fake agent；fail_on 非空时对包含该子串的 prompt 抛异常。"""

    def __init__(self, *, fail_on: str | None = None) -> None:
        self.fail_on = fail_on
        self.prompts: list[str] = []

    async def run(self, user_text, *, mode, approval_provider=None):
        self.prompts.append(user_text)
        if self.fail_on is not None and self.fail_on in user_text:
            raise RuntimeError(f"boom: {self.fail_on}")
            yield  # pragma: no cover - 保持 async generator 形态
        yield SimpleNamespace(type="final_response", content="done")


class RecordingInProcessBackend(InProcessBackend):
    """记录 start 返回的 handle，便于测试直接调用真实 wake 路径。"""

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.handles: dict[str, object] = {}

    async def start(self, spec):
        handle = await super().start(spec)
        self.handles[spec.member_name] = handle
        return handle


def _runtime_factory(home: Path, notifier: TeamEventNotifier, agent: ScriptedAgent):
    """与 worker.create_worker_runtime 同款的 runtime 构造（fake agent 替换真实 LLM）。"""

    def factory(spec):
        store = TeamStore(home=home)
        return TeamMemberRuntime(
            team_name=spec.team_name,
            member_name=spec.member_name,
            store=store,
            event_store=TeamEventStore(spec.team_name, store=store, config=TeamConfig()),
            notifier=notifier,
            memory=JsonConversationMemory(path=spec.context_path),
            agent=agent,
        )

    return factory


def _make_service(tmp_path: Path, notifier: TeamEventNotifier, backend) -> tuple[TeamService, TeamStore]:
    repository_root = tmp_path / "repo"
    repository_root.mkdir(exist_ok=True)
    store = TeamStore(home=tmp_path / "home")
    service = TeamService(
        store=store,
        repository_root=repository_root,
        repository_id="repo-123",
        target_branch="main",
        lead_owner="lead-1",
        config=TeamConfig(
            lock_retry_interval_seconds=0.01,
            lock_timeout_seconds=1.0,
            lock_stale_after_seconds=2.0,
        ),
        worktree_service=FakeWorktreeService(repository_root),
        backend_selector=BackendSelector(capability_probe=lambda backend, env: True),
        backend=backend,
        event_notifier=notifier,
    )
    return service, store


def _wired_backend(home: Path, notifier: TeamEventNotifier, agent: ScriptedAgent) -> RecordingInProcessBackend:
    return RecordingInProcessBackend(
        runtime_factory=_runtime_factory(home, notifier, agent),
        notifier=notifier,
    )


async def _wait_until(predicate, *, description: str, timeout: float = 5.0, interval: float = 0.05) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while True:
        if predicate():
            return
        if asyncio.get_running_loop().time() >= deadline:
            raise AssertionError(f"condition not met within {timeout}s: {description}")
        await asyncio.sleep(interval)


def _member_state(store: TeamStore, member_name: str) -> MemberState:
    snapshot = store.load("team-a")
    return next(m for m in snapshot.members if m.member_name == member_name).state


def _events_with(service: TeamService, role: str, message_id: str):
    return [e for e in service.event_store.events_for_role(role) if e.message.message_id == message_id]


async def _spawn_alpha(service: TeamService, *, task_id: str, goal: str) -> None:
    await service.create_or_attach("team-a")
    batch = await service.start_batch("wake-chain")
    service.task_board.create(
        TeamTask(
            task_id=task_id,
            batch_id=batch.batch_id,
            title="wake chain task",
            description="wake chain task",
            dependency_ids=(),
            kind=TaskKind.CODE,
        )
    )
    await service.spawn_member(
        member_name="alpha",
        role_name="general",
        role_revision=1,
        requested_backend=MemberBackend.IN_PROCESS,
        task_id=task_id,
        batch_id=batch.batch_id,
        goal=goal,
        read_only=False,
        approval_required=False,
    )


def _lead_message(message_id: str, body: str, *, target: str = "alpha") -> TeamMessage:
    return TeamMessage(
        message_id=message_id,
        protocol=MessageProtocol.MESSAGE,
        sender="lead",
        target_name=target,
        broadcast=False,
        body=body,
        summary=body,
        timestamp=datetime.now(timezone.utc),
    )


# ── 用例 1：真实接线端到端（AC1）────────────────────────────────────


def test_in_process_wakeup_chain_end_to_end(tmp_path: Path):
    async def scenario():
        notifier = TeamEventNotifier()
        agent = ScriptedAgent()
        backend = _wired_backend(tmp_path / "home", notifier, agent)
        service, store = _make_service(tmp_path, notifier, backend)
        await _spawn_alpha(service, task_id="task-wake", goal="wake chain")

        await service.send_message(_lead_message("wake-msg-1", "implement feature"))

        def consumed() -> bool:
            target = _events_with(service, "alpha", "wake-msg-1")
            lead_updates = [
                e
                for e in service.event_store.events_for_role(LEAD_ROLE_NAME)
                if e.message.protocol is MessageProtocol.STATUS_UPDATE
            ]
            return (
                bool(target)
                and target[0].state is TeamEventState.ACKED
                and _member_state(store, "alpha") is MemberState.IDLE
                and len(lead_updates) >= 1
            )

        await _wait_until(consumed, description="member auto-consumes and reports idle")

        # member 自动消费：assignment 与显式消息均 ACKED，agent 收到消息体
        assert "implement feature" in agent.prompts
        alpha_events = service.event_store.events_for_role("alpha")
        assert alpha_events
        assert all(e.state is TeamEventState.ACKED for e in alpha_events)
        # STATUS_UPDATE 落盘且 lead 队列收到信号（A3：member→Lead 投递后 notify）
        lead_queue = notifier.queue_for(LEAD_ROLE_NAME)
        assert lead_queue is not None
        assert not lead_queue.empty()

    asyncio.run(scenario())


# ── 用例 2：失败重试 3 次后终态失败（B1 端到端）─────────────────────


def test_member_failure_retries_then_terminal(tmp_path: Path):
    async def scenario():
        notifier = TeamEventNotifier()
        agent = ScriptedAgent(fail_on="boom")
        backend = _wired_backend(tmp_path / "home", notifier, agent)
        service, store = _make_service(tmp_path, notifier, backend)
        await _spawn_alpha(service, task_id="task-fail", goal="failure chain")

        # 等 spawn 自动下发的 assignment 被成功消费，避免与后续 claim/失败路径竞争
        def assignment_done() -> bool:
            return any(p.startswith("Task: task-fail") for p in agent.prompts) and (
                _member_state(store, "alpha") is MemberState.IDLE
            )

        await _wait_until(assignment_done, description="spawn assignment consumed")

        # claim 任务，使终态失败时 _set_task_failed 能把任务转 FAILED
        current = service.task_board.get("task-fail")
        service.claim_task("task-fail", "alpha", current.revision)

        await service.send_message(_lead_message("boom-1", "boom now"))

        def terminally_failed() -> bool:
            target = _events_with(service, "alpha", "boom-1")
            return (
                _member_state(store, "alpha") is MemberState.FAILED
                and bool(target)
                and target[0].state is TeamEventState.FAILED
            )

        await _wait_until(terminally_failed, description="event terminally failed after retries")

        # agent 恰好执行 3 次（事件层重试，无第 4 次）
        boom_calls = sum(1 for p in agent.prompts if "boom" in p)
        assert boom_calls == 3
        # 事件 FAILED、attempts=3
        event = _events_with(service, "alpha", "boom-1")[0]
        assert event.attempts == 3
        # member FAILED、task FAILED
        assert _member_state(store, "alpha") is MemberState.FAILED
        assert service.task_board.get("task-fail").state is TeamTaskState.FAILED
        # STATUS_UPDATE（member_failed）落盘给 lead
        lead_updates = [
            e
            for e in service.event_store.events_for_role(LEAD_ROLE_NAME)
            if e.message.protocol is MessageProtocol.STATUS_UPDATE and "member_failed" in e.message.body
        ]
        assert lead_updates
        # EventFailure 落盘：attempts=3、reason 含异常信息
        failures_path = store.event_failures_path("team-a")
        failures = [
            json.loads(line)
            for line in failures_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        assert any(
            f["event_id"] == event.event_id and f["attempts"] == 3 and "boom" in f["reason"]
            for f in failures
        )

    asyncio.run(scenario())


# ── 用例 3：wake 信号幂等（重复信号不导致重复消费）───────────────────


def test_wake_signal_is_idempotent(tmp_path: Path):
    async def scenario():
        notifier = TeamEventNotifier()
        agent = ScriptedAgent()
        backend = _wired_backend(tmp_path / "home", notifier, agent)
        service, _store = _make_service(tmp_path, notifier, backend)
        await _spawn_alpha(service, task_id="task-idem", goal="idempotent wake")

        await service.send_message(_lead_message("idem-1", "do once"))

        # 重复 wake + notify 轰炸（模拟 send_message 的 notify 与 wake 双信号叠加）
        handle = backend.handles["alpha"]
        for _ in range(5):
            await backend.wake(handle)
            await notifier.notify("alpha")

        def consumed() -> bool:
            target = _events_with(service, "alpha", "idem-1")
            return bool(target) and target[0].state is TeamEventState.ACKED

        await _wait_until(consumed, description="message consumed")

        # 留出时间窗，若信号不幂等导致重复消费会在此暴露
        await asyncio.sleep(0.3)

        body_calls = sum(1 for p in agent.prompts if p == "do once")
        assert body_calls == 1
        events = _events_with(service, "alpha", "idem-1")
        assert len(events) == 1
        assert events[0].state is TeamEventState.ACKED

    asyncio.run(scenario())
