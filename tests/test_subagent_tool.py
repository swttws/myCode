import asyncio

import pytest

from mycode.permission.models import PermissionMode
from mycode.prompt.models import PromptBuildMetadata, PromptBuildResult
from mycode.llm import ChatMessage
from mycode.subagent.context import ParentAgentSnapshotStore
from mycode.subagent.models import (
    AgentIsolationMode,
    AgentModelTier,
    ParentAgentSnapshot,
    SubAgentConfig,
    SubAgentKind,
    SubAgentResult,
    SubAgentTaskSnapshot,
    SubAgentTaskState,
    SubAgentTaskSummary,
    SubAgentUsage,
)
from mycode.subagent.service import SubAgentRunResponse
from mycode.subagent.tool import AgentTool
from mycode.tool import ToolDefinition, ToolKind, ToolRuntimeScope
from mycode.workspace import WorkspacePreparation
from mycode.worktree.models import WorktreeDisposition, WorktreeDispositionResult


def config():
    return SubAgentConfig(
        model_map={
            AgentModelTier.HAIKU: "haiku",
            AgentModelTier.SONNET: "sonnet",
            AgentModelTier.OPUS: "opus",
        },
        foreground_timeout_seconds=120,
    )


def parent_snapshot_store():
    store = ParentAgentSnapshotStore()
    store.update(
        PromptBuildResult(
            messages=(ChatMessage(role="user", content="parent"),),
            tools=(
                ToolDefinition(
                    name="read_file",
                    description="Read.",
                    parameters={"type": "object", "properties": {}, "required": []},
                    kind=ToolKind.READ,
                ),
            ),
            metadata=PromptBuildMetadata((), "sha", ()),
        ),
        model_id="parent-model",
        max_rounds=8,
        permission_mode=PermissionMode.DEFAULT,
        plan_only=False,
    )
    return store


def snapshot(
    *,
    task_id="task-000001",
    state=SubAgentTaskState.COMPLETED,
    detached=False,
    result=None,
    error_code=None,
    error_message=None,
):
    return SubAgentTaskSnapshot(
        id=task_id,
        sequence=int(task_id.rsplit("-", 1)[-1]),
        kind=SubAgentKind.DEFINED,
        role_name="general",
        state=state,
        detached=detached,
        rounds=1,
        result=result
        if result is not None
        else (
            SubAgentResult(detail="完整结果", summary="结果摘要")
            if state is SubAgentTaskState.COMPLETED
            else None
        ),
        error_code=error_code,
        error_message=error_message,
        usage=SubAgentUsage(input_tokens=1, output_tokens=None),
    )


class FakeService:
    def __init__(self):
        self.run_calls = []
        self.run_response = SubAgentRunResponse(inline=True, task=snapshot())
        self.summaries = (
            SubAgentTaskSummary(
                id="task-000001",
                sequence=1,
                kind=SubAgentKind.DEFINED,
                role_name="general",
                state=SubAgentTaskState.COMPLETED,
                detached=False,
                rounds=1,
                error_code=None,
                usage=SubAgentUsage(input_tokens=1),
            ),
        )
        self.details = {"task-000001": snapshot()}

    async def run(self, launch_request):
        self.run_calls.append(launch_request)
        return self.run_response

    def list_tasks(self):
        return self.summaries

    def get_task(self, task_id):
        if task_id not in self.details:
            raise KeyError(f"task_not_found: {task_id}")
        return self.details[task_id]


def make_tool(*, service=None, store=None):
    return AgentTool(
        service=service or FakeService(),
        snapshot_store=store or parent_snapshot_store(),
        config=config(),
    )


def run_tool(tool, arguments):
    return asyncio.run(tool.execute_async(arguments))


def test_agent_tool_schema_is_single_stable_parent_only_entry():
    service = FakeService()
    tool = make_tool(service=service)
    definition = tool.definition
    before = definition.parameters
    service.summaries = ()

    after = tool.definition.parameters

    assert definition.name == "Agent"
    assert definition.kind is ToolKind.WRITE
    assert definition.parallel_safe is False
    assert definition.requires_approval is False
    assert definition.runtime_scope is ToolRuntimeScope.PARENT_ONLY
    assert definition.execution_timeout_seconds == 125
    assert before == after
    assert before["additionalProperties"] is False
    assert before["properties"]["action"]["enum"] == ["run", "list", "get"]


@pytest.mark.parametrize(
    "arguments",
    [
        {},
        {"action": "bad"},
        {"action": "run", "type": "defined", "task": "t", "role": "general", "task_id": "task-1"},
        {"action": "run", "type": "defined", "task": "t"},
        {"action": "run", "type": "defined", "task": "t", "role": "general", "extra": True},
        {"action": "run", "type": "fork", "task": "t", "role": "general"},
        {"action": "run", "type": "fork", "task": "t", "background": True},
        {"action": "list", "task_id": "task-1"},
        {"action": "get"},
        {"action": "get", "task_id": "task-1", "role": "general"},
    ],
)
def test_agent_tool_rejects_invalid_arguments_without_calling_service(arguments):
    service = FakeService()
    result = run_tool(make_tool(service=service), arguments)

    assert result.ok is False
    assert result.content["reason_code"] == "invalid_agent_arguments"
    assert service.run_calls == []


def test_agent_tool_run_defined_and_fork_convert_to_launch_requests():
    service = FakeService()
    tool = make_tool(service=service)

    defined = run_tool(
        tool,
        {
            "action": "run",
            "type": "defined",
            "task": "总结 README",
            "role": "general",
            "background": True,
        },
    )
    fork = run_tool(tool, {"action": "run", "type": "fork", "task": "继续分析"})

    assert defined.ok is True
    assert fork.ok is True
    assert service.run_calls[0].kind is SubAgentKind.DEFINED
    assert service.run_calls[0].task == "总结 README"
    assert service.run_calls[0].role_name == "general"
    assert service.run_calls[0].requested_background is True
    assert isinstance(service.run_calls[0].parent, ParentAgentSnapshot)
    assert service.run_calls[1].kind is SubAgentKind.FORK
    assert service.run_calls[1].role_name is None
    assert service.run_calls[1].requested_background is True


def test_agent_tool_run_requires_parent_snapshot_but_list_and_get_do_not():
    service = FakeService()
    missing_store = ParentAgentSnapshotStore()
    tool = make_tool(service=service, store=missing_store)

    run_result = run_tool(
        tool,
        {"action": "run", "type": "defined", "task": "t", "role": "general"},
    )
    list_result = run_tool(tool, {"action": "list"})
    get_result = run_tool(tool, {"action": "get", "task_id": "task-000001"})

    assert run_result.ok is False
    assert run_result.content["reason_code"] == "parent_snapshot_unavailable"
    assert list_result.ok is True
    assert get_result.ok is True


def test_agent_tool_list_and_get_include_worktree_workspace_fields(tmp_path):
    workspace_root = tmp_path / ".worktrees" / "general" / "task-000001"
    branch_name = "mycode/worktree/general/task-000001"
    disposition = WorktreeDispositionResult(
        disposition=WorktreeDisposition.RETAINED,
        workspace_root=workspace_root,
        branch_name=branch_name,
        reasons=("未推送提交",),
    )
    service = FakeService()
    service.summaries = (
        SubAgentTaskSummary(
            id="task-000001",
            sequence=1,
            task_token="task-000001",
            kind=SubAgentKind.DEFINED,
            role_name="general",
            state=SubAgentTaskState.COMPLETED,
            detached=True,
            rounds=1,
            usage=SubAgentUsage(input_tokens=1),
            isolation=AgentIsolationMode.WORKTREE,
            workspace_root=workspace_root,
            branch_name=branch_name,
            workspace_preparation=WorkspacePreparation.CREATED,
            initialized_rules=("copy:.mycode", "hooks:.githooks"),
            disposition=disposition,
        ),
    )
    service.details = {
        "task-000001": SubAgentTaskSnapshot(
            id="task-000001",
            sequence=1,
            task_token="task-000001",
            kind=SubAgentKind.DEFINED,
            role_name="general",
            state=SubAgentTaskState.COMPLETED,
            detached=True,
            rounds=1,
            result=SubAgentResult(detail="detail", summary="summary"),
            usage=SubAgentUsage(input_tokens=1),
            isolation=AgentIsolationMode.WORKTREE,
            workspace_root=workspace_root,
            branch_name=branch_name,
            workspace_preparation=WorkspacePreparation.CREATED,
            initialized_rules=("copy:.mycode", "hooks:.githooks"),
            disposition=disposition,
        ),
    }
    tool = make_tool(service=service)

    list_result = run_tool(tool, {"action": "list"})
    get_result = run_tool(tool, {"action": "get", "task_id": "task-000001"})

    listed = list_result.content["tasks"][0]
    detailed = get_result.content["task"]
    assert listed["isolation"] == "worktree"
    assert listed["workspace_root"] == str(workspace_root)
    assert listed["branch_name"] == branch_name
    assert listed["workspace_preparation"] == "created"
    assert listed["initialized_rules"] == ("copy:.mycode", "hooks:.githooks")
    assert listed["disposition"]["disposition"] == "retained"
    assert listed["disposition"]["reasons"] == ("未推送提交",)
    assert detailed["isolation"] == listed["isolation"]
    assert detailed["workspace_root"] == listed["workspace_root"]
    assert detailed["disposition"] == listed["disposition"]


def test_agent_tool_list_get_and_service_errors_are_stable_chinese_results():
    service = FakeService()
    service.run_response = SubAgentRunResponse(
        inline=False,
        task=snapshot(
            task_id="task-000002",
            state=SubAgentTaskState.FAILED,
            detached=True,
            result=None,
            error_code="llm_error",
            error_message="模型失败",
        ),
    )
    tool = make_tool(service=service)

    run_result = run_tool(
        tool,
        {"action": "run", "type": "defined", "task": "t", "role": "general"},
    )
    list_result = run_tool(tool, {"action": "list"})
    missing_result = run_tool(tool, {"action": "get", "task_id": "task-missing"})

    assert run_result.ok is True
    assert run_result.content["inline"] is False
    assert run_result.content["task"]["state"] == "failed"
    assert run_result.content["task"]["error_code"] == "llm_error"
    assert "模型失败" in run_result.content["message"]
    assert list_result.content["tasks"][0]["id"] == "task-000001"
    assert list_result.content["tasks"][0]["usage"]["output_tokens"] == "未知"
    assert missing_result.ok is False
    assert missing_result.content["reason_code"] == "task_not_found"
    assert "未找到" in missing_result.error
