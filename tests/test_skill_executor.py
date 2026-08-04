from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path

from mycode.agent import AgentConfig, AgentMode, make_assistant_text_message, make_user_message
from mycode.config import LLMConfig
from mycode.compact.models import CompactConfig
from mycode.llm import StreamEvent, StreamEventType
from mycode.memory import InMemoryConversationMemory
from mycode.permission.models import PermissionDecision, PermissionEffect, PermissionMode
from mycode.skill import (
    SkillContextPolicy,
    SkillContextStrategy,
    SkillDefinition,
    SkillMetadata,
    SkillMode,
    SkillRunContext,
    SkillSource,
)
from mycode.skill.catalog import SkillCatalog
from mycode.skill.executor import SkillExecutor
from mycode.skill.loader import SkillLoader
from mycode.skill.runtime import SkillRuntime
from mycode.tool import (
    ToolCall,
    ToolDefinition,
    ToolExecutor,
    ToolKind,
    ToolRegistry,
    ToolResult,
    ToolWorkspaceScope,
)
from tests.helpers import shared_workspace
from tests.skill_test_support import FakeLLM, fixed_tool_registry


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


class ToolCallingLLM:
    def __init__(self) -> None:
        self.requests = []

    async def stream_chat(self, messages, tools=None):
        self.requests.append({"messages": tuple(messages), "tools": tuple(tools or ())})
        if len(self.requests) == 1:
            yield StreamEvent(
                StreamEventType.TOOL_CALL,
                tool_call=ToolCall(id="call-read", name="read_file", arguments={}, raw_arguments="{}"),
            )
            yield StreamEvent(StreamEventType.DONE)
            return
        yield StreamEvent(StreamEventType.TEXT_DELTA, content="skill done")
        yield StreamEvent(StreamEventType.DONE)


class ContextRecordingTool:
    def __init__(self) -> None:
        self.contexts = []

    @property
    def definition(self):
        return ToolDefinition(
            name="read_file",
            description="Record workspace context.",
            parameters={"type": "object", "properties": {}, "required": []},
            kind=ToolKind.READ,
            workspace_scope=ToolWorkspaceScope.WORKSPACE_AWARE,
        )

    def execute(self, arguments, context):
        self.contexts.append(context)
        return ToolResult(ok=True, tool_name="read_file", content={"ok": True})


def llm_config(model: str = "main-model") -> LLMConfig:
    return LLMConfig(
        protocol="openai_chat",
        model=model,
        base_url="https://example.test",
        api_key="test-key",
        compact=CompactConfig(context_window_tokens=50_000),
    )


def make_definition(
    tmp_path: Path,
    *,
    strategy: SkillContextStrategy = SkillContextStrategy.NONE,
    turns: int = 0,
    model: str | None = None,
) -> SkillDefinition:
    return SkillDefinition(
        metadata=SkillMetadata(
            name="test",
            description="运行测试。",
            allowed_tools=("read_file",),
            mode=SkillMode.ISOLATED,
            context=SkillContextPolicy(strategy, turns=turns),
            model=model,
        ),
        instruction="执行 {{arguments}}",
        source=SkillSource.PROJECT,
        entry_path=tmp_path / "test" / "SKILL.md",
        package_root=tmp_path / "test",
        resources=(),
        revision="rev",
    )


def make_executor(
    tmp_path: Path,
    main_llm: FakeLLM,
    config: LLMConfig,
    factory_calls: list[LLMConfig],
    factory_llms: list[FakeLLM] | None = None,
):
    workspace = tmp_path / "workspace"
    home = tmp_path / "home"
    builtin = tmp_path / "builtins"
    for root in (workspace / ".mycode" / "skills", home / ".mycode" / "skills", builtin):
        root.mkdir(parents=True)
    runtime = SkillRuntime(
        SkillCatalog(
            loader=SkillLoader(workspace_root=workspace, home=home, builtin_root=builtin),
            tool_names=lambda: frozenset({"read_file", "load_skill"}),
            reserved_slash_names=frozenset(),
        )
    )
    registry = fixed_tool_registry("read_file", "load_skill")

    def factory(next_config: LLMConfig):
        factory_calls.append(next_config)
        llm = FakeLLM(["context summary", "override summary"])
        if factory_llms is not None:
            factory_llms.append(llm)
        return llm

    return SkillExecutor(
        runtime=runtime,
        main_llm=main_llm,
        llm_config=config,
        llm_factory=factory,
        tool_registry=registry,
        tool_executor=ToolExecutor(registry),
        permission=AllowPermission(),
        agent_config=AgentConfig(max_rounds=1),
        workspace_root=workspace,
    )


def test_execute_isolated_none_uses_empty_main_history_and_returns_final_summary(tmp_path):
    main_llm = FakeLLM(["isolated done"])
    factory_calls: list[LLMConfig] = []
    executor = make_executor(tmp_path, main_llm, llm_config(), factory_calls)
    definition = make_definition(tmp_path, strategy=SkillContextStrategy.NONE)
    run_context = SkillRunContext(
        history=(make_user_message("earlier"), make_assistant_text_message("answer")),
        framework_blocks=(),
        approval_provider=None,
        scope=None,
        isolated_depth=0,
    )

    result = asyncio.run(executor.execute_isolated(definition, "target", run_context=run_context, mode=AgentMode()))

    assert result.ok is True
    assert result.summary == "isolated done"
    assert factory_calls == []
    captured = main_llm.requests[0]["messages"]
    assert all(message.content != "earlier" for message in captured)
    assert any(message.content == "执行 target" for message in captured)


def test_execute_isolated_recent_carries_only_selected_completed_turns(tmp_path):
    main_llm = FakeLLM(["done"])
    executor = make_executor(tmp_path, main_llm, llm_config(), [])
    definition = make_definition(tmp_path, strategy=SkillContextStrategy.RECENT, turns=1)
    run_context = SkillRunContext(
        history=(
            make_user_message("u1"),
            make_assistant_text_message("a1"),
            make_user_message("u2"),
            make_assistant_text_message("a2"),
        ),
        framework_blocks=(),
        approval_provider=None,
        scope=None,
        isolated_depth=0,
    )

    asyncio.run(executor.execute_isolated(definition, "target", run_context=run_context, mode=AgentMode()))

    contents = [message.content for message in main_llm.requests[0]["messages"]]
    assert "u1" not in contents
    assert "a1" not in contents
    assert "u2" in contents
    assert "a2" in contents


def test_execute_isolated_summary_uses_selected_model_without_tools_then_runs_sop(tmp_path):
    main_llm = FakeLLM(["main should not be used"])
    factory_calls: list[LLMConfig] = []
    factory_llms: list[FakeLLM] = []
    config = llm_config()
    executor = make_executor(tmp_path, main_llm, config, factory_calls, factory_llms)
    definition = make_definition(tmp_path, strategy=SkillContextStrategy.SUMMARY, model="summary-model")
    run_context = SkillRunContext(
        history=(make_user_message("用户目标"), make_assistant_text_message("当前进展")),
        framework_blocks=(),
        approval_provider=None,
        scope=None,
        isolated_depth=0,
    )

    result = asyncio.run(executor.execute_isolated(definition, "target", run_context=run_context, mode=AgentMode()))

    assert result.ok is True
    assert result.summary == "override summary"
    assert factory_calls == [replace(config, model="summary-model")]
    override_llm = factory_llms[0]
    assert override_llm.requests[0]["tools"] == ()
    execution_text = "\n".join(message.content for message in override_llm.requests[1]["messages"])
    assert "context summary" in execution_text


def test_execute_isolated_passes_workspace_context_to_nested_agent_tools(tmp_path):
    workspace_root = tmp_path / "workspace"
    home = tmp_path / "home"
    builtin = tmp_path / "builtins"
    for root in (workspace_root / ".mycode" / "skills", home / ".mycode" / "skills", builtin):
        root.mkdir(parents=True)
    runtime = SkillRuntime(
        SkillCatalog(
            loader=SkillLoader(workspace_root=workspace_root, home=home, builtin_root=builtin),
            tool_names=lambda: frozenset({"read_file", "load_skill"}),
            reserved_slash_names=frozenset(),
        )
    )
    tool = ContextRecordingTool()
    registry = ToolRegistry([tool])
    llm = ToolCallingLLM()
    workspace = shared_workspace(workspace_root)
    executor = SkillExecutor(
        runtime=runtime,
        main_llm=llm,
        llm_config=llm_config(),
        llm_factory=lambda config: llm,
        tool_registry=registry,
        tool_executor=ToolExecutor(registry),
        permission=AllowPermission(),
        agent_config=AgentConfig(max_rounds=2),
        workspace=workspace,
    )
    definition = make_definition(workspace_root, strategy=SkillContextStrategy.NONE)
    run_context = SkillRunContext(
        history=(),
        framework_blocks=(),
        approval_provider=None,
        scope=None,
        isolated_depth=0,
    )

    result = asyncio.run(executor.execute_isolated(definition, "target", run_context=run_context, mode=AgentMode()))

    assert result.ok is True
    assert result.summary == "skill done"
    assert tool.contexts[0].workspace == workspace
