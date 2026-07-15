import asyncio

from mycode.agent import AgentEvent, AgentEventType, AgentMode
from mycode.session import ChatSession


class FakeAgent:
    def __init__(self, events=None) -> None:
        self.events = events or []
        self.runs = []
        self.clear_count = 0

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
    session = ChatSession(agent=FakeAgent(expected_events))

    events = asyncio.run(collect_async(session.send("hello")))

    assert events == expected_events


def test_chat_session_send_passes_mode_and_approval_provider():
    agent = FakeAgent()
    session = ChatSession(agent=agent)
    session.set_plan_only(True)

    async def approval_provider(request):
        raise AssertionError("not called")

    asyncio.run(collect_async(session.send("hello", approval_provider=approval_provider)))

    assert agent.runs[0]["user_text"] == "hello"
    assert agent.runs[0]["mode"].plan_only is True
    assert agent.runs[0]["approval_provider"] is approval_provider


def test_chat_session_toggles_plan_only():
    session = ChatSession(agent=FakeAgent())

    session.set_plan_only(True)

    assert session.is_plan_only() is True

    session.set_plan_only(False)

    assert session.is_plan_only() is False


def test_chat_session_clear_resets_memory_and_plan_only():
    agent = FakeAgent()
    session = ChatSession(agent=agent)
    session.set_plan_only(True)

    session.clear()

    assert agent.clear_count == 1
    assert session.is_plan_only() is False
