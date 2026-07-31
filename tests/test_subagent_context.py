import asyncio

import pytest

from mycode.llm import ChatMessage
from mycode.permission.models import PermissionMode
from mycode.prompt.models import PromptBuildMetadata, PromptBuildResult
from mycode.subagent.context import (
    ParentAgentSnapshotStore,
    build_defined_agent_messages,
    build_fork_prompt,
)
from mycode.subagent.models import (
    AgentModelTier,
    AgentPermissionMode,
    AgentRoleDefinition,
    AgentRoleMetadata,
    AgentRoleSource,
)
from mycode.tool import ToolDefinition, ToolKind


def make_request(messages=None, tools=None):
    return PromptBuildResult(
        messages=tuple(messages or (ChatMessage(role="user", content="hello"),)),
        tools=tuple(tools or (make_tool(),)),
        metadata=PromptBuildMetadata(
            enabled_module_ids=("core",),
            stable_prompt_sha256="sha",
            diagnostics=(),
        ),
    )


def make_tool(name="read_file"):
    return ToolDefinition(
        name=name,
        description="Read.",
        parameters={
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
        kind=ToolKind.READ,
    )


def make_role():
    return AgentRoleDefinition(
        metadata=AgentRoleMetadata(
            name="explore",
            description="探索",
            allowed_tools=("read_file",),
            denied_tools=("Agent",),
            model=AgentModelTier.INHERIT,
            max_rounds=8,
            permission_mode=AgentPermissionMode.STRICT,
        ),
        instruction="你是只读探索角色。",
        source=AgentRoleSource.BUILTIN,
        entry_path="explore.md",
        revision="abc",
    )


def test_parent_snapshot_store_uses_contextvar_and_deep_copies_tools():
    async def scenario():
        store = ParentAgentSnapshotStore()
        tool = make_tool()
        request = make_request(
            messages=(ChatMessage(role="user", content="parent"),),
            tools=(tool,),
        )
        store.update(
            request,
            model_id="model-a",
            max_rounds=5,
            permission_mode=PermissionMode.DEFAULT,
        )
        snapshot = store.current()
        tool.parameters["properties"]["path"]["description"] = "mutated"

        assert snapshot.messages == request.messages
        assert snapshot.tools[0].parameters["properties"]["path"] == {"type": "string"}
        assert snapshot.model_id == "model-a"
        assert snapshot.max_rounds == 5
        assert snapshot.permission_mode is PermissionMode.DEFAULT

        async def child(model_id):
            store.update(
                make_request(messages=(ChatMessage(role="user", content=model_id),)),
                model_id=model_id,
                max_rounds=1,
                permission_mode=PermissionMode.STRICT,
            )
            await asyncio.sleep(0)
            return store.current().model_id, store.current().messages[0].content

        assert await asyncio.gather(child("left"), child("right")) == [
            ("left", "left"),
            ("right", "right"),
        ]
        assert store.current().model_id == "model-a"

    asyncio.run(scenario())


def test_parent_snapshot_store_requires_current_snapshot():
    store = ParentAgentSnapshotStore()

    with pytest.raises(RuntimeError, match="parent snapshot"):
        store.current()


def test_defined_agent_messages_include_role_environment_project_and_task_only():
    role = make_role()

    messages = build_defined_agent_messages(
        role=role,
        task="请阅读 README。",
        workspace_environment="workspace=D:/repo",
        project_instructions=("项目规则 A", "项目规则 B"),
    )
    rendered = "\n".join(message.content for message in messages)

    assert "核心系统规则" in rendered
    assert "workspace=D:/repo" in rendered
    assert "项目规则 A" in rendered
    assert "项目规则 B" in rendered
    assert "你是只读探索角色。" in rendered
    assert "请阅读 README。" in rendered
    assert "父历史" not in rendered
    assert "父记忆" not in rendered
    assert "父 Skill" not in rendered
    assert "临时提醒" not in rendered


def test_fork_prompt_keeps_parent_messages_and_tools_prefix_unchanged():
    store = ParentAgentSnapshotStore()
    parent_messages = (
        ChatMessage(role="system", content="系统前缀"),
        ChatMessage(role="user", content="父历史"),
    )
    tool = make_tool()
    request = make_request(messages=parent_messages, tools=(tool,))
    store.update(
        request,
        model_id="model-parent",
        max_rounds=9,
        permission_mode=PermissionMode.PERMISSIVE,
    )
    snapshot = store.current()

    fork = build_fork_prompt(
        snapshot,
        task="调查失败原因",
        child_messages=(ChatMessage(role="assistant", content="子历史"),),
    )
    tool.parameters["properties"]["path"]["description"] = "mutated later"

    assert fork.messages[: len(parent_messages)] == parent_messages
    assert fork.messages[len(parent_messages)].role == "system"
    assert "Fork 子 Agent 任务" in fork.messages[len(parent_messages)].content
    assert "调查失败原因" in fork.messages[len(parent_messages)].content
    assert fork.messages[-1].content == "子历史"
    assert fork.tools == snapshot.tools
    assert fork.tools[0].parameters["properties"]["path"] == {"type": "string"}
