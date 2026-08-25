from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace

from mycode.agent import AgentEventType
from mycode.team.domain.models import TeamTaskState
from mycode.team.infrastructure.requests import TeamRequestState
from mycode.team.execution.supervisor import LeadSupervisor
from mycode.team.domain.state import SupervisorState
from mycode.dev_logging import configure_dev_logging


class FakeLeadAgent:
    def __init__(self) -> None:
        self.prompts: list[str] = []

    async def run(self, prompt, *, mode, approval_provider=None):
        self.prompts.append(prompt)
        yield SimpleNamespace(type=AgentEventType.FINAL_RESPONSE, content="done")


class FakeEventStore:
    """Minimal fake for TeamEventStore used by RoleEventConsumer."""

    def __init__(self) -> None:
        self.acked: list[str] = []
        self.begun: list[str] = []
        self.pending: list = []

    def next_event(self, role_name: str):
        if self.pending:
            return self.pending.pop(0)
        return None

    def begin_event(self, role_name: str, event_id: str):
        self.begun.append(event_id)

    def ack_event(self, role_name: str, event_id: str):
        self.acked.append(event_id)

    def fail_event(self, role_name: str, event_id: str, reason: str):
        return None


class FakeEventNotifier:
    """Minimal fake for TeamEventNotifier used by RoleEventConsumer."""

    def __init__(self) -> None:
        self._queues: dict[str, asyncio.Queue] = {}

    def register_queue(self, role_name: str) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue()
        self._queues[role_name] = queue
        return queue

    async def notify(self, role_name: str) -> None:
        queue = self._queues.get(role_name)
        if queue is not None:
            queue.put_nowait(None)


class FakeSupervisorService:
    def __init__(self) -> None:
        self.pending: list = []
        self._event_store = FakeEventStore()
        self._event_notifier = FakeEventNotifier()

    @property
    def team_name(self):
        return None

    @property
    def event_store(self):
        return self._event_store

    @property
    def event_notifier(self):
        return self._event_notifier

    def list_requests(self, *, state=None):
        if state is None:
            return tuple(self.pending)
        return tuple(item for item in self.pending if item.state is state)

    async def resolve_request(self, request_id, **kwargs):
        return None

    async def status(self):
        return SimpleNamespace(batches=[SimpleNamespace(state="active")])

    def list_tasks(self):
        return (SimpleNamespace(state=TeamTaskState.RUNNING),)


def test_supervisor_runs_one_lead_and_waits_for_member_event():
    """Lead 处理用户目标后进入 WAITING_MEMBER，收到 member 事件后继续处理。"""
    agent = FakeLeadAgent()
    service = FakeSupervisorService()
    supervisor = LeadSupervisor(service, agent)

    async def scenario():
        await supervisor.start()
        await supervisor.submit_user_goal("ship the batch")
        for _ in range(100):
            if supervisor.state is SupervisorState.WAITING_MEMBER:
                break
            await asyncio.sleep(0.01)
        assert supervisor.state is SupervisorState.WAITING_MEMBER

        service.event_store.pending.append(
            SimpleNamespace(
                event_id="member-1",
                message=SimpleNamespace(
                    sender="dev",
                    summary="idle",
                    body="idle",
                    task_id=None,
                    batch_id=None,
                ),
            )
        )
        await service.event_notifier.notify("lead")
        for _ in range(100):
            if len(agent.prompts) >= 2:
                break
            await asyncio.sleep(0.01)
        await supervisor.stop()

    asyncio.run(scenario())
    assert agent.prompts[0] == "ship the batch"
    assert len(agent.prompts) >= 2


def test_supervisor_enters_waiting_user_for_pending_request():
    """Lead 处理后有 pending request 时进入 WAITING_USER。"""
    agent = FakeLeadAgent()
    service = FakeSupervisorService()
    service.pending.append(SimpleNamespace(state=TeamRequestState.PENDING))
    supervisor = LeadSupervisor(service, agent)

    async def scenario():
        await supervisor.start()
        await supervisor.submit_user_goal("decide behavior")
        for _ in range(100):
            if supervisor.state is SupervisorState.WAITING_USER:
                break
            await asyncio.sleep(0.01)
        assert supervisor.state is SupervisorState.WAITING_USER
        await supervisor.stop()

    asyncio.run(scenario())


def test_supervisor_user_decision_resumes_lead():
    """用户决策提交后 Lead 恢复处理。"""
    agent = FakeLeadAgent()
    service = FakeSupervisorService()
    service.pending.append(SimpleNamespace(state=TeamRequestState.PENDING, request_id="req-1"))
    supervisor = LeadSupervisor(service, agent)

    async def scenario():
        await supervisor.start()
        await supervisor.submit_user_goal("decide behavior")
        for _ in range(100):
            if supervisor.state is SupervisorState.WAITING_USER:
                break
            await asyncio.sleep(0.01)
        assert supervisor.state is SupervisorState.WAITING_USER

        await supervisor.resolve_user_request("req-1", "approved")
        for _ in range(100):
            if len(agent.prompts) >= 2:
                break
            await asyncio.sleep(0.01)
        await supervisor.stop()

    asyncio.run(scenario())
    assert len(agent.prompts) >= 2
    assert "approved" in agent.prompts[1]


def test_supervisor_stop_gracefully():
    """stop() 后 supervisor 状态为 STOPPING。"""
    agent = FakeLeadAgent()
    service = FakeSupervisorService()
    supervisor = LeadSupervisor(service, agent)

    async def scenario():
        await supervisor.start()
        await supervisor.stop()

    asyncio.run(scenario())
    assert supervisor.state is SupervisorState.STOPPING


def test_supervisor_agent_logs_include_lead_identity(tmp_path):
    class LoggingLeadAgent(FakeLeadAgent):
        async def run(self, prompt, *, mode, approval_provider=None):
            logging.getLogger("mycode.agent.loop").info("lead deep event")
            async for event in super().run(prompt, mode=mode, approval_provider=approval_provider):
                yield event

    agent = LoggingLeadAgent()
    service = FakeSupervisorService()
    log_file = tmp_path / "team.log"
    configure_dev_logging(log_file)
    supervisor = LeadSupervisor(service, agent)

    async def scenario():
        await supervisor.start()
        await supervisor.submit_user_goal("ship the batch")
        for _ in range(100):
            if agent.prompts:
                break
            await asyncio.sleep(0.01)
        await supervisor.stop()

    asyncio.run(scenario())

    event_line = next(
        line for line in log_file.read_text(encoding="utf-8").splitlines()
        if "lead deep event" in line
    )
    assert "角色=lead" in event_line
