import asyncio
from dataclasses import dataclass

from mycode.agent import AgentConfig, AgentLoop, AgentMode
from mycode.agent.events import AgentEventType
from mycode.compact.models import CompactConfig
from mycode.config import LLMConfig
from mycode.llm import BaseLLM, ChatMessage, MessageOrigin, StreamEvent, StreamEventType, UsageObservation
from mycode.memory import InMemoryConversationMemory
from mycode.permission.models import PermissionDecision, PermissionEffect, PermissionMode
from mycode.prompt.models import PromptBuildMetadata, PromptBuildResult
from mycode.subagent.context import ParentAgentSnapshotStore
from mycode.subagent.models import (
    AgentModelTier,
    AgentPermissionMode,
    AgentRoleDefinition,
    AgentRoleMetadata,
    AgentRoleSource,
    SubAgentConfig,
    SubAgentExecutionReport,
    SubAgentKind,
    SubAgentResult,
    SubAgentTaskState,
    SubAgentUsage,
)
from mycode.subagent.notifications import SubAgentNotificationInbox
from mycode.subagent.runtime import SubAgentRuntimeFactory
from mycode.subagent.service import SubAgentService
from mycode.subagent.tasks import SubAgentTaskManager
from mycode.subagent.tool import AgentTool
from mycode.subagent.tooling import TaskToolRegistryFactory
from mycode.tool import ToolExecutor, ToolRegistry, ToolResult
from tests.helpers import PassthroughContextManager, collect_async


class ScriptedLLM(BaseLLM):
    def __init__(self, scripts):
        self.scripts = list(scripts)
        self.requests = []
        self.tool_requests = []

    async def stream_chat(self, messages, tools=None):
        self.requests.append(tuple(messages))
        self.tool_requests.append(tuple(tools or ()))
        script = self.scripts.pop(0)
        for event in script:
            yield event


class RecordingLLMFactory:
    def __init__(self, mapping):
        self.mapping = {model: list(llms) for model, llms in mapping.items()}
        self.configs = []

    def __call__(self, config):
        self.configs.append(config)
        queue = self.mapping[config.model]
        if len(queue) == 1:
            return queue[0]
        return queue.pop(0)


class FakeCatalog:
    def __init__(self, *roles):
        self.roles = {role.metadata.name: role for role in roles}

    def get(self, name):
        return self.roles[name]


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


@dataclass(frozen=True)
class PromptTurn:
    framework_blocks: tuple = ()


class RecordingPromptBuilder:
    def __init__(self):
        self.framework_blocks_seen = []

    def begin_turn(self, *, turn_id, plan_only, reminders=(), framework_blocks=()):
        return PromptTurn(framework_blocks=tuple(framework_blocks))

    def build(self, *, history, tools, turn, round_index):
        self.framework_blocks_seen.append(tuple(turn.framework_blocks))
        messages = list(history)
        if turn.framework_blocks:
            messages.append(
                ChatMessage(
                    role="user",
                    content="\n".join(block.content for block in turn.framework_blocks),
                    origin=MessageOrigin.FRAMEWORK_CONTEXT,
                )
            )
        return PromptBuildResult(
            messages=tuple(messages),
            tools=tuple(tools),
            metadata=PromptBuildMetadata(("e2e",), "sha", ()),
        )


class ControlledRuntime:
    def __init__(self, name):
        self.name = name
        self.started = asyncio.Event()
        self.finish = asyncio.Event()
        self.cancelled = asyncio.Event()
        self.calls = 0

    async def run(self, cancel_event):
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
            self.cancelled.set()
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
            result=SubAgentResult(
                detail=f"{self.name} detail",
                summary=f"{self.name} summary",
            ),
            error_code=None,
            error_message=None,
            usage=SubAgentUsage(input_tokens=1),
        )


class FakeRuntimeFactory:
    def __init__(self, runtimes):
        self.runtimes = list(runtimes)
        self.calls = []

    def create(self, launch_request, *, detached, task_id, workspace_lease):
        self.calls.append((launch_request, detached, task_id, workspace_lease))
        return self.runtimes.pop(0)


def subagent_config(*, foreground_timeout_seconds=0.5, max_concurrency=4):
    return SubAgentConfig(
        model_map={
            AgentModelTier.HAIKU: "haiku-child",
            AgentModelTier.SONNET: "sonnet-child",
            AgentModelTier.OPUS: "opus-child",
        },
        foreground_timeout_seconds=foreground_timeout_seconds,
        max_concurrency=max_concurrency,
    )


def llm_config(cfg):
    return LLMConfig(
        protocol="openai_chat",
        model="parent-model",
        base_url="https://example.invalid",
        api_key="test-key",
        compact=CompactConfig(context_window_tokens=30_000),
        sub_agent=cfg,
    )


def role(name="general", *, allowed_tools=(), model=AgentModelTier.SONNET):
    return AgentRoleDefinition(
        metadata=AgentRoleMetadata(
            name=name,
            description="General integration role.",
            allowed_tools=tuple(allowed_tools),
            denied_tools=("Agent",),
            model=model,
            max_rounds=4,
            permission_mode=AgentPermissionMode.INHERIT,
        ),
        instruction="You are the e2e child agent. Finish without asking questions.",
        source=AgentRoleSource.BUILTIN,
        entry_path="general.md",
        revision="rev-1",
    )


def make_parent_snapshot_store(parent_registry, *, model_id="parent-model", max_rounds=3):
    store = ParentAgentSnapshotStore()
    store.update(
        PromptBuildResult(
            messages=(
                ChatMessage(role="system", content="parent system prefix"),
                ChatMessage(role="user", content="parent message prefix"),
            ),
            tools=tuple(parent_registry.definitions()),
            metadata=PromptBuildMetadata(("parent",), "sha", ()),
        ),
        model_id=model_id,
        max_rounds=max_rounds,
        permission_mode=PermissionMode.DEFAULT,
        plan_only=False,
    )
    return store


def make_real_agent_tool(tmp_path, *, child_factory, cfg=None, inbox=None):
    cfg = cfg or subagent_config()
    inbox = inbox or SubAgentNotificationInbox()
    parent_registry = ToolRegistry()
    snapshot_store = make_parent_snapshot_store(parent_registry)
    runtime_factory = SubAgentRuntimeFactory(
        config=cfg,
        llm_config=llm_config(cfg),
        llm_factory=child_factory,
        catalog=FakeCatalog(role()),
        parent_tool_registry=parent_registry,
        task_tool_registry_factory=TaskToolRegistryFactory(workspace_root=tmp_path),
        permission_factory=lambda mode: AllowPermission(),
        workspace_root=tmp_path,
        workspace_environment=f"workspace={tmp_path}",
        project_instructions=("project rule for e2e",),
    )
    manager = SubAgentTaskManager(config=cfg, notification_inbox=inbox)
    service = SubAgentService(
        config=cfg,
        runtime_factory=runtime_factory,
        task_manager=manager,
    )
    agent_tool = AgentTool(
        service=service,
        snapshot_store=snapshot_store,
        config=cfg,
    )
    parent_registry.register(agent_tool)
    snapshot_store.update(
        PromptBuildResult(
            messages=(
                ChatMessage(role="system", content="parent system prefix"),
                ChatMessage(role="user", content="parent message prefix"),
            ),
            tools=tuple(parent_registry.definitions()),
            metadata=PromptBuildMetadata(("parent",), "sha", ()),
        ),
        model_id="parent-model",
        max_rounds=3,
        permission_mode=PermissionMode.DEFAULT,
        plan_only=False,
    )
    return agent_tool, service, inbox


async def wait_for_terminal(service, task_id):
    for _ in range(100):
        snapshot = service.get_task(task_id)
        if snapshot.state in {
            SubAgentTaskState.COMPLETED,
            SubAgentTaskState.FAILED,
            SubAgentTaskState.CANCELLED,
        }:
            return snapshot
        await asyncio.sleep(0.01)
    raise AssertionError(f"{task_id} did not finish")


def test_agent_tool_defined_foreground_runs_real_child_loop_and_queries_live_state(tmp_path):
    child_llm = ScriptedLLM(
        [[
            StreamEvent(StreamEventType.TEXT_DELTA, "defined child done"),
            StreamEvent(
                StreamEventType.DONE,
                usage=UsageObservation(
                    provider="fake",
                    input_tokens=3,
                    output_tokens=4,
                    total_tokens=7,
                    cache_read_tokens=1,
                    cache_write_tokens=2,
                ),
            ),
        ]]
    )
    child_factory = RecordingLLMFactory({"sonnet-child": [child_llm]})

    async def scenario():
        agent_tool, service, _inbox = make_real_agent_tool(
            tmp_path,
            child_factory=child_factory,
        )

        run_result = await agent_tool.execute_async(
            {
                "action": "run",
                "type": "defined",
                "role": "general",
                "task": "summarize integration state",
                "background": False,
            }
        )

        assert run_result.ok is True
        assert run_result.content["inline"] is True
        assert run_result.content["task"]["id"] == "task-000001"
        assert run_result.content["task"]["state"] == "completed"
        assert run_result.content["task"]["result"]["detail"] == "defined child done"
        assert run_result.content["task"]["usage"]["cache_read_tokens"] == 1

        list_result = await agent_tool.execute_async({"action": "list"})
        get_result = await agent_tool.execute_async(
            {"action": "get", "task_id": "task-000001"}
        )

        assert list_result.content["tasks"][0]["state"] == "completed"
        assert get_result.content["task"]["result"]["summary"] == "defined child done"
        assert child_factory.configs[0].model == "sonnet-child"
        rendered_child_prompt = "\n".join(message.content for message in child_llm.requests[0])
        assert "project rule for e2e" in rendered_child_prompt
        assert "summarize integration state" in rendered_child_prompt
        await service.close()

    asyncio.run(scenario())


def test_fork_background_finishes_without_auto_parent_call_and_injects_next_safe_point(tmp_path):
    child_llm = ScriptedLLM(
        [[
            StreamEvent(StreamEventType.TEXT_DELTA, "fork child done"),
            StreamEvent(StreamEventType.DONE, usage=UsageObservation(provider="fake", input_tokens=5)),
        ]]
    )
    parent_llm = ScriptedLLM(
        [
            [StreamEvent(StreamEventType.TEXT_DELTA, "parent acknowledged"), StreamEvent(StreamEventType.DONE)],
            [StreamEvent(StreamEventType.TEXT_DELTA, "parent second"), StreamEvent(StreamEventType.DONE)],
        ]
    )
    shared_inbox = SubAgentNotificationInbox()
    child_factory = RecordingLLMFactory({"parent-model": [child_llm]})

    async def scenario():
        agent_tool, service, inbox = make_real_agent_tool(
            tmp_path,
            child_factory=child_factory,
            inbox=shared_inbox,
        )

        run_result = await agent_tool.execute_async(
            {
                "action": "run",
                "type": "fork",
                "task": "continue from frozen parent",
            }
        )

        assert run_result.ok is True
        assert run_result.content["inline"] is False
        task_id = run_result.content["task"]["id"]
        completed = await wait_for_terminal(service, task_id)
        assert completed.state is SubAgentTaskState.COMPLETED
        assert parent_llm.requests == []

        memory = InMemoryConversationMemory()
        registry = ToolRegistry()
        prompt_builder = RecordingPromptBuilder()
        parent_loop = AgentLoop(
            llm=parent_llm,
            memory=memory,
            tool_executor=ToolExecutor(registry),
            tool_registry=registry,
            permission=AllowPermission(),
            context_manager=PassthroughContextManager(memory),
            config=AgentConfig(max_rounds=2),
            prompt_builder=prompt_builder,
            notification_inbox=inbox,
        )
        events = await collect_async(parent_loop.run("next parent request", mode=AgentMode()))
        second_events = await collect_async(parent_loop.run("another request", mode=AgentMode()))

        assert events[-1].type is AgentEventType.FINAL_RESPONSE
        assert second_events[-1].type is AgentEventType.FINAL_RESPONSE
        first_request_text = "\n".join(message.content for message in parent_llm.requests[0])
        second_request_text = "\n".join(message.content for message in parent_llm.requests[1])
        assert task_id in first_request_text
        assert "fork child done" in first_request_text
        assert task_id not in second_request_text
        await service.close()

    asyncio.run(scenario())


def test_five_background_agent_tool_runs_use_fifo_queue_and_clear_resets_ids(tmp_path):
    cfg = subagent_config(max_concurrency=4)
    runtimes = [ControlledRuntime(f"task-{index}") for index in range(6)]
    inbox = SubAgentNotificationInbox()
    runtime_factory = FakeRuntimeFactory(runtimes)
    manager = SubAgentTaskManager(config=cfg, notification_inbox=inbox)
    service = SubAgentService(
        config=cfg,
        runtime_factory=runtime_factory,
        task_manager=manager,
    )
    parent_registry = ToolRegistry()
    snapshot_store = make_parent_snapshot_store(parent_registry)
    agent_tool = AgentTool(service=service, snapshot_store=snapshot_store, config=cfg)
    parent_registry.register(agent_tool)

    async def scenario():
        for index in range(5):
            result = await agent_tool.execute_async(
                {
                    "action": "run",
                    "type": "defined",
                    "role": "general",
                    "task": f"background {index}",
                    "background": True,
                }
            )
            assert result.ok is True

        await asyncio.gather(*(runtime.started.wait() for runtime in runtimes[:4]))
        list_result = await agent_tool.execute_async({"action": "list"})
        states = [task["state"] for task in list_result.content["tasks"]]
        assert states == ["running", "running", "running", "running", "queued"]
        assert runtimes[4].started.is_set() is False

        runtimes[0].finish.set()
        await runtimes[4].started.wait()
        first = await wait_for_terminal(service, "task-000001")
        assert first.state is SubAgentTaskState.COMPLETED
        assert manager.get("task-000005").state is SubAgentTaskState.RUNNING

        await service.clear()
        assert service.list_tasks() == ()
        reset_result = await agent_tool.execute_async(
            {
                "action": "run",
                "type": "defined",
                "role": "general",
                "task": "after clear",
                "background": True,
            }
        )
        assert reset_result.content["task"]["id"] == "task-000001"
        await service.close()

    asyncio.run(scenario())
