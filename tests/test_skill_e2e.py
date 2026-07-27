from __future__ import annotations

import asyncio
from pathlib import Path

from mycode.agent import AgentConfig, AgentEventType, AgentMode, make_assistant_text_message, make_user_message
from mycode.config import LLMConfig
from mycode.compact.models import CompactConfig
from mycode.llm import BaseLLM, StreamEvent, StreamEventType
from mycode.memory import InMemoryConversationMemory
from mycode.permission.models import PermissionDecision, PermissionEffect, PermissionMode, RuleSource
from mycode.session import ChatSession
from mycode.skill.catalog import SkillCatalog
from mycode.skill.executor import SkillExecutor
from mycode.skill.load_tool import SkillLoadTool
from mycode.skill.loader import SkillLoader
from mycode.skill.runtime import SkillRuntime
from mycode.tool import ToolCall, ToolExecutor, ToolRegistry
from tests.helpers import PassthroughContextManager, collect_async
from tests.skill_test_support import FakeTool, write_skill


class ScriptedLLM(BaseLLM):
    def __init__(self, scripts):
        self.scripts = list(scripts)
        self.requests = []
        self.tool_requests = []

    async def stream_chat(self, messages, tools=None):
        self.requests.append(tuple(messages))
        self.tool_requests.append(tuple(tools or ()))
        for event in self.scripts.pop(0):
            yield event


class AllowPermission:
    async def before_tool(self, call, definition, *, plan_only, round_index):
        return PermissionDecision(
            effect=PermissionEffect.ALLOW,
            reason_code="allow",
            message_zh="允许",
            mode=PermissionMode.DEFAULT,
            display_arguments={},
        )

    async def after_tool(self, call, result):
        return result


class FakePermissionService:
    def effective_mode(self):
        return (PermissionMode.DEFAULT, RuleSource.SESSION)

    def set_session_mode(self, mode):
        pass

    def clear_session(self):
        pass


def _tool_call(name: str, arguments: dict) -> StreamEvent:
    return StreamEvent(
        StreamEventType.TOOL_CALL,
        tool_call=ToolCall(id=f"call-{name}", name=name, arguments=arguments),
    )


def _llm_config() -> LLMConfig:
    return LLMConfig(
        protocol="openai_chat",
        model="main-model",
        base_url="https://example.test",
        api_key="test-key",
        compact=CompactConfig(context_window_tokens=50_000),
    )


def _make_skill_stack(tmp_path: Path, llm: BaseLLM, *, max_rounds: int = 4):
    workspace = tmp_path / "workspace"
    home = tmp_path / "home"
    builtin = tmp_path / "builtins"
    project_root = workspace / ".mycode" / "skills"
    for root in (project_root, home / ".mycode" / "skills", builtin):
        root.mkdir(parents=True)

    runtime = SkillRuntime(
        SkillCatalog(
            loader=SkillLoader(workspace_root=workspace, home=home, builtin_root=builtin),
            tool_names=lambda: frozenset({"read_file", "load_skill"}),
            reserved_slash_names=frozenset({"help", "clear"}),
        )
    )
    runtime.refresh()
    registry = ToolRegistry([FakeTool("read_file")])
    memory = InMemoryConversationMemory()
    permission = AllowPermission()
    agent_config = AgentConfig(max_rounds=max_rounds)
    tool_executor = ToolExecutor(registry)
    executor = SkillExecutor(
        runtime=runtime,
        main_llm=llm,
        llm_config=_llm_config(),
        llm_factory=lambda config: llm,
        tool_registry=registry,
        tool_executor=tool_executor,
        permission=permission,
        agent_config=agent_config,
        workspace_root=workspace,
    )
    registry.register(SkillLoadTool(runtime=runtime, executor=executor))
    agent = __import__("mycode.agent", fromlist=["AgentLoop"]).AgentLoop(
        llm=llm,
        memory=memory,
        tool_executor=tool_executor,
        tool_registry=registry,
        permission=permission,
        context_manager=PassthroughContextManager(memory),
        config=agent_config,
        skill_runtime=runtime,
    )
    session = ChatSession(
        agent=agent,
        permissions=FakePermissionService(),
        skill_runtime=runtime,
        skill_executor=executor,
    )
    return session, memory, project_root, runtime


def test_shared_skill_load_hot_update_and_clear_rediscover_flow(tmp_path):
    llm = ScriptedLLM(
        [
            [_tool_call("load_skill", {"name": "review", "arguments": "main"}), StreamEvent(StreamEventType.DONE)],
            [StreamEvent(StreamEventType.TEXT_DELTA, content="first done"), StreamEvent(StreamEventType.DONE)],
            [StreamEvent(StreamEventType.TEXT_DELTA, content="second done"), StreamEvent(StreamEventType.DONE)],
            [StreamEvent(StreamEventType.TEXT_DELTA, content="after clear"), StreamEvent(StreamEventType.DONE)],
        ]
    )
    session, _, project_root, _ = _make_skill_stack(tmp_path, llm)
    write_skill(project_root, "review", body="OLD SOP {{arguments}}", allowed_tools=("read_file",))

    first_events = asyncio.run(collect_async(session.send("please review")))
    write_skill(project_root, "review", body="NEW SOP {{arguments}}", allowed_tools=("read_file",))
    second_events = asyncio.run(collect_async(session.send("continue")))
    session.clear()
    third_events = asyncio.run(collect_async(session.send("rediscover")))

    assert first_events[-1].type is AgentEventType.FINAL_RESPONSE
    assert second_events[-1].content == "second done"
    assert third_events[-1].content == "after clear"
    first_request = "\n".join(message.content for message in llm.requests[0])
    second_request = "\n".join(message.content for message in llm.requests[1])
    hot_request = "\n".join(message.content for message in llm.requests[2])
    clear_request = "\n".join(message.content for message in llm.requests[3])
    assert "review: 测试 Skill。" in first_request
    assert "OLD SOP" not in first_request
    assert "OLD SOP main" in second_request
    assert "NEW SOP main" in hot_request
    assert "NEW SOP" not in clear_request
    assert "review: 测试 Skill。" in clear_request


def test_isolated_skill_execution_keeps_temp_history_out_of_main_memory(tmp_path):
    llm = ScriptedLLM([[StreamEvent(StreamEventType.TEXT_DELTA, content="isolated summary"), StreamEvent(StreamEventType.DONE)]])
    session, memory, project_root, _ = _make_skill_stack(tmp_path, llm, max_rounds=1)
    write_skill(
        project_root,
        "test",
        body="TEMP SOP {{arguments}}",
        allowed_tools=(),
        mode="isolated",
        context={"strategy": "recent", "turns": 3},
    )
    for index in range(1, 5):
        memory.append(make_user_message(f"u{index}"))
        memory.append(make_assistant_text_message(f"a{index}"))

    events = asyncio.run(collect_async(session.send_skill("test", "target")))

    assert events[-1].type is AgentEventType.FINAL_RESPONSE
    assert events[-1].content == "isolated summary"
    temp_request_text = "\n".join(message.content for message in llm.requests[0])
    assert "u1" not in temp_request_text
    assert "u2" in temp_request_text
    assert "u4" in temp_request_text
    main_history = [message.content for message in memory.messages()]
    assert main_history[-2:] == ["/test target", "isolated summary"]
    assert "TEMP SOP target" not in main_history
