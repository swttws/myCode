from __future__ import annotations

import asyncio
from pathlib import Path

from mycode.agent import AgentConfig, AgentLoop, AgentMode, AgentEventType
from mycode.llm import BaseLLM, StreamEvent, StreamEventType
from mycode.memory import InMemoryConversationMemory
from mycode.permission.models import PermissionDecision, PermissionEffect, PermissionMode
from mycode.skill.catalog import SkillCatalog
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
    def __init__(self) -> None:
        self.calls = []

    async def before_tool(self, call, definition, *, plan_only, round_index):
        self.calls.append(call.name)
        return PermissionDecision(
            effect=PermissionEffect.ALLOW,
            reason_code="allow",
            message_zh="允许",
            mode=PermissionMode.DEFAULT,
            display_arguments={},
        )

    async def after_tool(self, call, result):
        return result


def make_loop(tmp_path: Path, llm: ScriptedLLM):
    workspace = tmp_path / "workspace"
    home = tmp_path / "home"
    builtin = tmp_path / "builtins"
    project_root = workspace / ".mycode" / "skills"
    for root in (project_root, home / ".mycode" / "skills", builtin):
        root.mkdir(parents=True)
    runtime = SkillRuntime(
        SkillCatalog(
            loader=SkillLoader(workspace_root=workspace, home=home, builtin_root=builtin),
            tool_names=lambda: frozenset({"read_file", "run_command", "load_skill"}),
            reserved_slash_names=frozenset({"help", "clear"}),
        )
    )
    runtime.refresh()
    registry = ToolRegistry(
        [
            FakeTool("read_file"),
            FakeTool("run_command"),
            SkillLoadTool(runtime=runtime),
        ]
    )
    memory = InMemoryConversationMemory()
    permission = AllowPermission()
    loop = AgentLoop(
        llm=llm,
        memory=memory,
        tool_executor=ToolExecutor(registry),
        tool_registry=registry,
        permission=permission,
        context_manager=PassthroughContextManager(memory),
        config=AgentConfig(max_rounds=3),
        skill_runtime=runtime,
    )
    return loop, runtime, project_root, permission


def tool_call(name: str, arguments: dict) -> StreamEvent:
    return StreamEvent(
        StreamEventType.TOOL_CALL,
        tool_call=ToolCall(id=f"call-{name}", name=name, arguments=arguments),
    )


def test_agent_loop_injects_catalog_then_active_sop_and_narrows_tools(tmp_path):
    llm = ScriptedLLM(
        [
            [tool_call("load_skill", {"name": "review", "arguments": "main"}), StreamEvent(StreamEventType.DONE)],
            [StreamEvent(StreamEventType.TEXT_DELTA, content="done"), StreamEvent(StreamEventType.DONE)],
        ]
    )
    loop, _, project_root, _ = make_loop(tmp_path, llm)
    write_skill(project_root, "review", body="秘密 SOP {{arguments}}", allowed_tools=("read_file",))

    events = asyncio.run(collect_async(loop.run("review please", mode=AgentMode())))

    assert events[-1].type is AgentEventType.FINAL_RESPONSE
    first_content = "\n".join(message.content for message in llm.requests[0])
    second_content = "\n".join(message.content for message in llm.requests[1])
    assert "review: 测试 Skill。" in first_content
    assert "秘密 SOP" not in first_content
    assert "秘密 SOP main" in second_content
    assert [definition.name for definition in llm.tool_requests[0]] == [
        "load_skill",
        "read_file",
        "run_command",
    ]
    assert [definition.name for definition in llm.tool_requests[1]] == ["load_skill", "read_file"]


def test_agent_loop_rejects_tool_outside_active_skill_allowlist_before_permission(tmp_path):
    llm = ScriptedLLM(
        [
            [tool_call("load_skill", {"name": "review"}), StreamEvent(StreamEventType.DONE)],
            [tool_call("run_command", {"command": "echo no"}), StreamEvent(StreamEventType.DONE)],
        ]
    )
    loop, _, project_root, permission = make_loop(tmp_path, llm)
    write_skill(project_root, "review", allowed_tools=("read_file",))

    events = asyncio.run(collect_async(loop.run("review please", mode=AgentMode())))

    assert events[-1].type is AgentEventType.ERROR
    assert "not allowed by active skill" in events[-1].content
    assert permission.calls == ["load_skill"]
