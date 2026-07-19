import asyncio

from mycode.agent import AgentEvent, AgentEventType, AgentMode
from mycode.permission.models import PermissionMode, RuleSource
from mycode.session import ChatSession


class FakeAgent:
    def __init__(self, events=None, operations=None) -> None:
        self.events = events or []
        self.runs = []
        self.clear_count = 0
        self.operations = operations

    async def run(self, user_text, *, mode, approval_provider=None):
        self.runs.append(
            {
                "user_text": user_text,
                "mode": mode,
                "approval_provider": approval_provider,
            }
        )
        for event in self.events:
            yield event

    def clear_memory(self):
        self.clear_count += 1
        if self.operations is not None:
            self.operations.append("memory")


class FakePermissions:
    def __init__(self, operations=None):
        self.mode = (PermissionMode.DEFAULT, None)
        self.clear_count = 0
        self.operations = operations

    def effective_mode(self):
        return self.mode

    def set_session_mode(self, mode):
        self.mode = (mode, RuleSource.SESSION)

    def clear_session(self):
        self.clear_count += 1
        self.mode = (PermissionMode.DEFAULT, None)
        if self.operations is not None:
            self.operations.append("permissions")


async def collect_async(async_iterable):
    items = []
    async for item in async_iterable:
        items.append(item)
    return items


def test_chat_session_forwards_agent_events():
    expected_events = [
        AgentEvent(AgentEventType.USER_MESSAGE, content="hello"),
        AgentEvent(AgentEventType.FINAL_RESPONSE, content="hi"),
    ]
    session = ChatSession(agent=FakeAgent(expected_events), permissions=FakePermissions())

    events = asyncio.run(collect_async(session.send("hello")))

    assert events == expected_events


def test_chat_session_send_passes_mode_and_approval_provider():
    agent = FakeAgent()
    session = ChatSession(agent=agent, permissions=FakePermissions())
    session.set_plan_only(True)

    async def approval_provider(request):
        raise AssertionError("not called")

    asyncio.run(collect_async(session.send("hello", approval_provider=approval_provider)))

    assert agent.runs[0]["user_text"] == "hello"
    assert agent.runs[0]["mode"].plan_only is True
    assert agent.runs[0]["approval_provider"] is approval_provider


def test_chat_session_toggles_plan_only():
    session = ChatSession(agent=FakeAgent(), permissions=FakePermissions())

    session.set_plan_only(True)

    assert session.is_plan_only() is True

    session.set_plan_only(False)

    assert session.is_plan_only() is False


def test_chat_session_clear_resets_memory_and_plan_only():
    operations = []
    agent = FakeAgent(operations=operations)
    permissions = FakePermissions(operations)
    session = ChatSession(agent=agent, permissions=permissions)
    session.set_plan_only(True)

    session.clear()

    assert agent.clear_count == 1
    assert session.is_plan_only() is False
    assert permissions.clear_count == 1
    assert operations == ["memory", "permissions"]


def test_chat_session_queries_and_sets_permission_mode():
    permissions = FakePermissions()
    session = ChatSession(agent=FakeAgent(), permissions=permissions)

    assert session.permission_mode() == (PermissionMode.DEFAULT, None)

    session.set_permission_mode(PermissionMode.STRICT)

    assert session.permission_mode() == (PermissionMode.STRICT, RuleSource.SESSION)
