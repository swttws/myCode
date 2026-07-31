import asyncio
from dataclasses import replace

import pytest

from mycode.config import LLMConfig
from mycode.compact.models import CompactConfig
from mycode.hook.models import HookTriggerResult
from mycode.llm import BaseLLM, ChatMessage, LLMError, StreamEvent, StreamEventType, UsageObservation
from mycode.permission.models import PermissionDecision, PermissionEffect, PermissionMode
from mycode.subagent.models import (
    AgentModelTier,
    AgentPermissionMode,
    AgentRoleDefinition,
    AgentRoleMetadata,
    AgentRoleSource,
    ParentAgentSnapshot,
    SubAgentConfig,
    SubAgentKind,
    SubAgentLaunchRequest,
    SubAgentTaskState,
)
from mycode.subagent.runtime import SubAgentRuntimeFactory
from mycode.subagent.tooling import TaskToolRuntime
from mycode.tool import ToolCall, ToolDefinition, ToolExecutor, ToolKind, ToolRegistry, ToolResult


class ScriptedLLM(BaseLLM):
    def __init__(self, scripts):
        self.scripts = list(scripts)
        self.requests = []
        self.tool_requests = []

    async def stream_chat(self, messages, tools=None):
        self.requests.append(tuple(messages))
        self.tool_requests.append(tuple(tools or ()))
        script = self.scripts.pop(0)
        if isinstance(script, BaseException):
            raise script
        for event in script:
            yield event


class BlockingLLM(BaseLLM):
    def __init__(self):
        self.started = asyncio.Event()
        self.cancelled = asyncio.Event()

    async def stream_chat(self, messages, tools=None):
        self.started.set()
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            self.cancelled.set()
            raise
        yield StreamEvent(StreamEventType.DONE)


class RecordingLLMFactory:
    def __init__(self, llms):
        self.llms = {name: list(values) for name, values in llms.items()}
        self.configs = []

    def __call__(self, config):
        self.configs.append(config)
        queue = self.llms[config.model]
        if len(queue) == 1:
            return queue[0]
        return queue.pop(0)


class EchoTool:
    def __init__(self):
        self.calls = []

    @property
    def definition(self):
        return ToolDefinition(
            name="echo",
            description="Echo text.",
            parameters={
                "type": "object",
                "properties": {"text": {"type": "string"}},
                "required": ["text"],
            },
            kind=ToolKind.READ,
        )

    def execute(self, arguments):
        self.calls.append(arguments)
        return ToolResult(ok=True, tool_name="echo", content={"text": arguments["text"]})


class SimpleTaskToolRegistryFactory:
    def create(self, parent_registry):
        return TaskToolRuntime(
            registry=parent_registry,
            executor=ToolExecutor(parent_registry),
        )


class AllowPermission:
    async def before_tool(self, call, definition, *, plan_only, round_index):
        return PermissionDecision(
            effect=PermissionEffect.ALLOW,
            reason_code="allow",
            message_zh="允许执行。",
            mode=PermissionMode.DEFAULT,
            display_arguments={},
        )

    def denied_result(self, call, decision):
        return ToolResult(
            ok=False,
            tool_name=call.name,
            content={"reason_code": decision.reason_code},
            error=decision.message_zh,
        )

    async def after_tool(self, call, result):
        return result


class RecordingHookRuntime:
    def __init__(self):
        self.events = []
        self.before_tool_calls = []
        self.after_tool_calls = []
        self.clear_calls = 0

    async def trigger(self, context):
        self.events.append(context.event)
        return HookTriggerResult(actions=())

    async def before_tool(self, *, call, definition, round_index, turn_id, plan_only):
        self.before_tool_calls.append(call.name)
        return HookTriggerResult(actions=())

    async def after_tool(self, *, call, definition, result, round_index, turn_id, plan_only):
        self.after_tool_calls.append(call.name)
        return HookTriggerResult(actions=())

    def prompt_blocks(self):
        return ()

    def clear_request_state(self):
        self.clear_calls += 1


class FakeCatalog:
    def __init__(self, *roles):
        self._roles = {role.metadata.name: role for role in roles}

    def get(self, name):
        return self._roles[name]


def subagent_config():
    return SubAgentConfig(
        model_map={
            AgentModelTier.HAIKU: "haiku-child",
            AgentModelTier.SONNET: "sonnet-child",
            AgentModelTier.OPUS: "opus-child",
        }
    )


def llm_config(config=None):
    return LLMConfig(
        protocol="openai_chat",
        model="parent-model",
        base_url="https://example.invalid",
        api_key="test-key",
        compact=CompactConfig(context_window_tokens=30_000),
        sub_agent=config or subagent_config(),
    )


def role(
    *,
    name="general",
    model=AgentModelTier.SONNET,
    max_rounds=4,
    allowed_tools=("echo",),
    permission_mode=AgentPermissionMode.INHERIT,
):
    return AgentRoleDefinition(
        metadata=AgentRoleMetadata(
            name=name,
            description="通用执行角色",
            allowed_tools=tuple(allowed_tools),
            denied_tools=("Agent",),
            model=model,
            max_rounds=max_rounds,
            permission_mode=permission_mode,
        ),
        instruction="你是测试用子 Agent，请独立完成任务。",
        source=AgentRoleSource.BUILTIN,
        entry_path="general.md",
        revision="rev-1",
    )


def parent_snapshot(*, model_id="parent-model", max_rounds=7, tools=()):
    return ParentAgentSnapshot(
        messages=(
            ChatMessage(role="system", content="父系统前缀"),
            ChatMessage(role="user", content="父历史"),
        ),
        tools=tuple(tool.definition for tool in tools),
        model_id=model_id,
        max_rounds=max_rounds,
        permission_mode=PermissionMode.DEFAULT,
    )


def request(*, kind=SubAgentKind.DEFINED, task="请完成子任务", role_name="general", parent=None):
    return SubAgentLaunchRequest(
        kind=kind,
        task=task,
        role_name=role_name if kind is SubAgentKind.DEFINED else None,
        requested_background=(kind is SubAgentKind.FORK),
        parent=parent or parent_snapshot(),
    )


def make_factory(tmp_path, *, llm_factory, catalog, parent_registry=None, hook_factory=None):
    return SubAgentRuntimeFactory(
        config=subagent_config(),
        llm_config=llm_config(),
        llm_factory=llm_factory,
        catalog=catalog,
        parent_tool_registry=parent_registry or ToolRegistry(),
        task_tool_registry_factory=SimpleTaskToolRegistryFactory(),
        permission_factory=lambda mode: AllowPermission(),
        hook_runtime_factory=hook_factory or (lambda: RecordingHookRuntime()),
        workspace_root=tmp_path,
        workspace_environment=f"workspace={tmp_path}",
        project_instructions=("项目规则 A",),
    )


async def run_report(runtime):
    return await runtime.run(asyncio.Event())


def test_runtime_direct_text_completes_with_rounds_result_and_usage(tmp_path):
    usage = UsageObservation(
        provider="fake",
        input_tokens=3,
        output_tokens=4,
        total_tokens=7,
        cache_read_tokens=11,
        cache_write_tokens=13,
    )
    llm = ScriptedLLM(
        [[StreamEvent(StreamEventType.TEXT_DELTA, "完成"), StreamEvent(StreamEventType.DONE, usage=usage)]]
    )
    llm_factory = RecordingLLMFactory({"sonnet-child": [llm]})
    runtime = make_factory(
        tmp_path,
        llm_factory=llm_factory,
        catalog=FakeCatalog(role(model=AgentModelTier.SONNET)),
    ).create(request(task="读取 README 并总结。"), detached=False)

    report = asyncio.run(run_report(runtime))

    assert report.state is SubAgentTaskState.COMPLETED
    assert report.rounds == 1
    assert report.result.detail == "完成"
    assert report.result.summary == "完成"
    assert report.usage.input_tokens == 3
    assert report.usage.output_tokens == 4
    assert report.usage.total_tokens == 7
    assert report.usage.cache_read_tokens == 11
    assert report.usage.cache_write_tokens == 13
    assert llm_factory.configs[0].model == "sonnet-child"
    rendered = "\n".join(message.content for message in llm.requests[0])
    assert "项目规则 A" in rendered
    assert "读取 README 并总结。" in rendered
    assert "你是测试用子 Agent" in rendered


def test_runtime_uses_real_agent_loop_for_tool_round_memory_and_hook(tmp_path):
    first_usage = UsageObservation(provider="fake", input_tokens=1, output_tokens=2, total_tokens=3)
    second_usage = UsageObservation(provider="fake", input_tokens=5, output_tokens=None, total_tokens=8)
    tool_call = ToolCall(id="call-1", name="echo", arguments={"text": "hi"}, raw_arguments='{"text":"hi"}')
    llm = ScriptedLLM(
        [
            [StreamEvent(StreamEventType.TOOL_CALL, tool_call=tool_call), StreamEvent(StreamEventType.DONE, usage=first_usage)],
            [StreamEvent(StreamEventType.TEXT_DELTA, "工具完成"), StreamEvent(StreamEventType.DONE, usage=second_usage)],
        ]
    )
    echo = EchoTool()
    hook = RecordingHookRuntime()
    factory = make_factory(
        tmp_path,
        llm_factory=RecordingLLMFactory({"sonnet-child": [llm]}),
        catalog=FakeCatalog(role(max_rounds=4)),
        parent_registry=ToolRegistry([echo]),
        hook_factory=lambda: hook,
    )

    report = asyncio.run(run_report(factory.create(request(parent=parent_snapshot(tools=(echo,))), detached=False)))

    assert report.state is SubAgentTaskState.COMPLETED
    assert report.rounds == 2
    assert report.result.detail == "工具完成"
    assert report.usage.input_tokens == 6
    assert report.usage.output_tokens is None
    assert report.usage.total_tokens == 11
    assert echo.calls == [{"text": "hi"}]
    assert len(llm.requests) == 2
    assert llm.requests[1][-2].role == "assistant"
    assert llm.requests[1][-1].role == "tool"
    assert hook.before_tool_calls == ["echo"]
    assert hook.after_tool_calls == ["echo"]
    assert hook.clear_calls == 1


def test_factory_selects_model_and_max_rounds_for_defined_inherit_and_fork(tmp_path):
    sonnet_runtime = make_factory(
        tmp_path,
        llm_factory=RecordingLLMFactory({"sonnet-child": [ScriptedLLM([])]}),
        catalog=FakeCatalog(role(model=AgentModelTier.SONNET, max_rounds=3)),
    ).create(request(), detached=False)
    inherit_runtime = make_factory(
        tmp_path,
        llm_factory=RecordingLLMFactory({"parent-model": [ScriptedLLM([])]}),
        catalog=FakeCatalog(role(model=AgentModelTier.INHERIT, max_rounds=5)),
    ).create(request(), detached=False)
    fork_runtime = make_factory(
        tmp_path,
        llm_factory=RecordingLLMFactory({"parent-model": [ScriptedLLM([])]}),
        catalog=FakeCatalog(role()),
    ).create(
        request(kind=SubAgentKind.FORK, task="继续父任务", parent=parent_snapshot(max_rounds=9)),
        detached=True,
    )

    assert sonnet_runtime.model_id == "sonnet-child"
    assert sonnet_runtime.max_rounds == 3
    assert inherit_runtime.model_id == "parent-model"
    assert inherit_runtime.max_rounds == 5
    assert fork_runtime.model_id == "parent-model"
    assert fork_runtime.max_rounds == 9


@pytest.mark.parametrize(
    ("script", "expected_code"),
    [
        ([StreamEvent(StreamEventType.DONE)], "empty_final_response"),
        (LLMError("network failed"), "llm_error"),
    ],
)
def test_runtime_normalizes_empty_final_text_and_llm_errors(tmp_path, script, expected_code):
    llm = ScriptedLLM([script])
    runtime = make_factory(
        tmp_path,
        llm_factory=RecordingLLMFactory({"sonnet-child": [llm]}),
        catalog=FakeCatalog(role()),
    ).create(request(), detached=False)

    report = asyncio.run(run_report(runtime))

    assert report.state is SubAgentTaskState.FAILED
    assert report.error_code == expected_code
    assert report.result is None
    assert "Traceback" not in (report.error_message or "")


def test_runtime_reports_max_rounds_and_cancellation_as_terminal_reports(tmp_path):
    echo = EchoTool()
    repeat_call = ToolCall(id="call-1", name="echo", arguments={"text": "loop"}, raw_arguments='{"text":"loop"}')
    max_rounds_llm = ScriptedLLM(
        [[StreamEvent(StreamEventType.TOOL_CALL, tool_call=repeat_call), StreamEvent(StreamEventType.DONE)]]
    )
    max_rounds_runtime = make_factory(
        tmp_path,
        llm_factory=RecordingLLMFactory({"sonnet-child": [max_rounds_llm]}),
        catalog=FakeCatalog(role(max_rounds=1)),
        parent_registry=ToolRegistry([echo]),
    ).create(request(parent=parent_snapshot(tools=(echo,))), detached=False)

    max_rounds_report = asyncio.run(run_report(max_rounds_runtime))

    assert max_rounds_report.state is SubAgentTaskState.FAILED
    assert max_rounds_report.error_code == "max_rounds_exceeded"

    async def cancel_scenario():
        blocking_llm = BlockingLLM()
        cancel_runtime = make_factory(
            tmp_path,
            llm_factory=RecordingLLMFactory({"sonnet-child": [blocking_llm]}),
            catalog=FakeCatalog(role()),
        ).create(request(), detached=False)
        cancel_event = asyncio.Event()
        task = asyncio.create_task(cancel_runtime.run(cancel_event))
        await blocking_llm.started.wait()
        cancel_event.set()
        report = await asyncio.wait_for(task, timeout=1)
        return report, blocking_llm.cancelled.is_set()

    cancel_report, llm_cancelled = asyncio.run(cancel_scenario())

    assert cancel_report.state is SubAgentTaskState.CANCELLED
    assert cancel_report.error_code == "cancelled"
    assert llm_cancelled is True


def test_factory_creates_isolated_runtime_state_for_concurrent_runs(tmp_path):
    left_llm = ScriptedLLM(
        [[StreamEvent(StreamEventType.TEXT_DELTA, "left"), StreamEvent(StreamEventType.DONE, usage=UsageObservation(provider="fake", input_tokens=1))]]
    )
    right_llm = ScriptedLLM(
        [[StreamEvent(StreamEventType.TEXT_DELTA, "right"), StreamEvent(StreamEventType.DONE, usage=UsageObservation(provider="fake", input_tokens=9))]]
    )
    factory = make_factory(
        tmp_path,
        llm_factory=RecordingLLMFactory({"sonnet-child": [left_llm, right_llm]}),
        catalog=FakeCatalog(role()),
    )
    left = factory.create(request(task="left task"), detached=False)
    right = factory.create(request(task="right task"), detached=False)

    async def scenario():
        return await asyncio.gather(left.run(asyncio.Event()), right.run(asyncio.Event()))

    left_report, right_report = asyncio.run(scenario())

    assert left_report.result.detail == "left"
    assert right_report.result.detail == "right"
    assert left_report.usage.input_tokens == 1
    assert right_report.usage.input_tokens == 9
    assert "right task" not in "\n".join(message.content for message in left_llm.requests[0])
    assert "left task" not in "\n".join(message.content for message in right_llm.requests[0])
