from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from types import MappingProxyType

from mycode.permission.models import PermissionDecision, PermissionEffect, PermissionMode
from mycode.team import (
    ApprovalState,
    BatchRecord,
    BatchState,
    DeliveryReceipt,
    IntegrationReport,
    MemberBackend,
    MessageProtocol,
    TaskKind,
    TaskPatch,
    TaskResult,
    TeamMessage,
    TeamRecord,
    TeamError,
    TeamSnapshot,
    TeamState,
    TeamTask,
    TeamTaskState,
)
from mycode.team.policy import TeamPermissionInterceptor, TeamRuntimeRole, TeamToolPolicy
from mycode.team.tool import TeamTool
from mycode.tool import ToolCall, ToolDefinition, ToolKind, ToolRuntimeScope


NOW = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)


class FakeTeamService:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.calls = []
        self.active = False
        self.coordinator = False
        self.tasks: dict[str, TeamTask] = {}
        self.approval_required_members: set[str] = set()
        self.snapshot = TeamSnapshot(
            team=TeamRecord(
                team_name="team-a",
                repository_root=root,
                repository_id="repo-123",
                target_branch="main",
                state=TeamState.ACTIVE,
            ),
            members=(),
            batches=(),
            registry={},
        )

    def visible_team_tools(self):
        return TeamToolPolicy(
            role=TeamRuntimeRole.LEAD if self.active else TeamRuntimeRole.PARENT,
            coordinator_enabled=self.coordinator,
        ).visible_names(frozenset({"team", "team_lead", "team_member", "read_file"}))

    async def create_or_attach(self, team_name: str, *, goal: str | None = None):
        self.calls.append(("create_or_attach", team_name, goal))
        self.active = True
        self.snapshot = TeamSnapshot(
            team=TeamRecord(
                team_name=team_name,
                repository_root=self.root,
                repository_id="repo-123",
                target_branch="main",
                state=TeamState.ACTIVE,
            ),
            members=(),
            batches=(),
            registry={},
        )
        return self.snapshot

    async def status(self):
        self.calls.append(("status",))
        return self.snapshot

    async def archive(self):
        self.calls.append(("archive",))
        return self.snapshot.team

    async def start_batch(self, goal: str):
        self.calls.append(("start_batch", goal))
        return BatchRecord(
            batch_id="batch-1",
            goal=goal,
            baseline_commit="0123456789abcdef0123456789abcdef01234567",
            state=BatchState.ACTIVE,
        )

    async def spawn_member(self, **kwargs):
        self.calls.append(("spawn_member", kwargs))
        return type("Member", (), {"member_name": kwargs["member_name"], "state": "running"})()

    async def terminate_member(self, member_name: str, *, force: bool):
        self.calls.append(("terminate_member", member_name, force))
        return type("Member", (), {"member_name": member_name, "state": "stopped"})()

    async def send_message(self, message: TeamMessage):
        self.calls.append(("send_message", message))
        return DeliveryReceipt(
            message_id=message.message_id,
            recipient_names=(message.target_name or "broadcast",),
            delivered_at=NOW,
            fanout_count=1,
        )

    def create_task(self, task: TeamTask):
        self.calls.append(("create_task", task))
        created = TeamTask(
            task_id=task.task_id,
            batch_id=task.batch_id,
            title=task.title,
            description=task.description,
            dependency_ids=task.dependency_ids,
            kind=task.kind,
            revision=1,
            created_at=NOW,
            updated_at=NOW,
        )
        self.tasks[created.task_id] = created
        return created

    def list_tasks(self, batch_id: str | None = None):
        self.calls.append(("list_tasks", batch_id))
        return tuple(self.tasks.values())

    def get_task(self, task_id: str):
        self.calls.append(("get_task", task_id))
        try:
            return self.tasks[task_id]
        except KeyError as exc:
            raise TeamError(
                code="missing_task",
                phase="load",
                message=f"missing task: {task_id}",
                task_id=task_id,
            ) from exc

    def claim_task(self, task_id: str, member_name: str, expected_revision: int):
        self.calls.append(("claim_task", task_id, member_name, expected_revision))
        claimed = TeamTask(
            task_id=task_id,
            batch_id="batch-1",
            title="Task",
            description="Do it",
            dependency_ids=(),
            kind=TaskKind.CODE,
            owner=member_name,
            state=TeamTaskState.CLAIMED,
            revision=expected_revision + 1,
        )
        self.tasks[task_id] = claimed
        return claimed

    def update_task(self, task_id: str, expected_revision: int, patch: TaskPatch):
        self.calls.append(("update_task", task_id, expected_revision, patch))
        current = self.tasks[task_id]
        updated = TeamTask(
            task_id=task_id,
            batch_id=current.batch_id,
            title=patch.title or current.title,
            description=patch.description or current.description,
            dependency_ids=patch.dependency_ids if patch.dependency_ids is not None else current.dependency_ids,
            kind=patch.kind or current.kind,
            owner=patch.owner if patch.owner is not None else current.owner,
            state=current.state,
            plan_revision=patch.plan_revision if patch.plan_revision is not None else current.plan_revision,
            approval_state=patch.approval_state or current.approval_state,
            revision=expected_revision + 1,
        )
        self.tasks[task_id] = updated
        return updated

    def transition_task(
        self,
        task_id: str,
        expected_revision: int,
        state: TeamTaskState,
        result: TaskResult | None = None,
        error: str | None = None,
    ):
        self.calls.append(("transition_task", task_id, expected_revision, state, result, error))
        current = self.tasks[task_id]
        transitioned = TeamTask(
            task_id=task_id,
            batch_id=current.batch_id,
            title=current.title,
            description=current.description,
            dependency_ids=current.dependency_ids,
            kind=current.kind,
            owner=current.owner,
            state=state,
            plan_revision=current.plan_revision,
            approval_state=current.approval_state,
            result=result,
            error=error,
            revision=expected_revision + 1,
        )
        self.tasks[task_id] = transitioned
        return transitioned

    def member_requires_approval(self, member_name: str, task_id: str) -> bool:
        self.calls.append(("member_requires_approval", member_name, task_id))
        return member_name in self.approval_required_members

    async def integrate_batch(self, batch_id: str):
        self.calls.append(("integrate_batch", batch_id))
        return IntegrationReport(
            batch_id=batch_id,
            state=BatchState.COMPLETED,
            target_ref_before="before",
            target_ref_after="after",
            result_commit_id="0123456789abcdef0123456789abcdef01234567",
            completed_at=NOW,
        )


def test_team_tool_is_parent_only_and_dispatches_stable_entry_actions(tmp_path: Path):
    service = FakeTeamService(tmp_path)
    tool = TeamTool(service=service)

    assert tool.definition.name == "team"
    assert tool.definition.runtime_scope is ToolRuntimeScope.PARENT_ONLY
    assert tool.definition.requires_approval is False

    created = asyncio.run(tool.execute_async({"action": "create", "team_name": "alpha", "goal": "ship"}))
    status = asyncio.run(tool.execute_async({"action": "status"}))

    assert created.ok is True
    assert created.content["team_name"] == "alpha"
    assert created.content["activated"] is True
    assert status.content["team_name"] == "alpha"
    assert service.calls[:2] == [
        ("create_or_attach", "alpha", "ship"),
        ("status",),
    ]


def test_team_tool_exposes_attach_as_stable_parent_entry_action(tmp_path: Path):
    service = FakeTeamService(tmp_path)
    tool = TeamTool(service=service)

    attached = asyncio.run(tool.execute_async({"action": "attach", "team_name": "alpha"}))

    assert attached.ok is True
    assert attached.content["team_name"] == "alpha"
    assert "attach" in tool.definition.parameters["properties"]["action"]["enum"]
    assert service.calls == [("create_or_attach", "alpha", None)]


def test_team_tool_schema_describes_action_specific_requirements_in_chinese(tmp_path: Path):
    tool = TeamTool(service=FakeTeamService(tmp_path), name="team_lead")

    parameters = tool.definition.parameters
    assert parameters["required"] == ["action"]
    branches = parameters["oneOf"]
    spawn = next(branch for branch in branches if branch["properties"]["action"]["enum"] == ["spawn_member"])

    assert spawn["required"] == [
        "action",
        "member_name",
        "role_name",
        "role_revision",
        "requested_backend",
        "task_id",
        "batch_id",
        "goal",
        "read_only",
        "approval_required",
    ]
    assert spawn["properties"]["requested_backend"]["enum"] == [
        "auto",
        "tmux",
        "terminal",
        "in_process",
    ]
    assert all("description" in definition for definition in parameters["properties"].values())
    assert all("description" in branch for branch in branches)
    assert "不同 action 使用不同参数" in parameters["description"]


def test_team_tool_rejects_missing_action_specific_argument(tmp_path: Path):
    tool = TeamTool(service=FakeTeamService(tmp_path), name="team_lead")

    result = asyncio.run(tool.execute_async({"action": "start_batch"}))

    assert result.ok is False
    assert result.content["reason_code"] == "missing_team_argument"
    assert result.error == "missing required argument: goal"


def test_team_tool_dispatches_lead_actions_to_service(tmp_path: Path):
    service = FakeTeamService(tmp_path)
    service.active = True
    tool = TeamTool(service=service)

    batch = asyncio.run(tool.execute_async({"action": "start_batch", "goal": "ship"}))
    member = asyncio.run(
        tool.execute_async(
            {
                "action": "spawn_member",
                "member_name": "dev",
                "role_name": "general",
                "role_revision": 3,
                "requested_backend": "in_process",
                "task_id": "task-1",
                "batch_id": "batch-1",
                "goal": "ship",
                "read_only": False,
                "approval_required": True,
            }
        )
    )
    receipt = asyncio.run(
        tool.execute_async(
            {
                "action": "send_message",
                "message_id": "msg-1",
                "target_name": "dev",
                "body": "continue",
                "summary": "continue",
            }
        )
    )

    assert batch.content["batch_id"] == "batch-1"
    assert member.content["member_name"] == "dev"
    assert receipt.content["message_id"] == "msg-1"
    assert service.calls[0] == ("start_batch", "ship")
    assert service.calls[1][0] == "spawn_member"
    assert service.calls[1][1]["requested_backend"] is MemberBackend.IN_PROCESS
    assert service.calls[2][0] == "send_message"
    assert service.calls[2][1].protocol is MessageProtocol.MESSAGE


def test_team_tool_can_be_parameterized_as_lead_and_member_views(tmp_path: Path):
    service = FakeTeamService(tmp_path)
    parent_tool = TeamTool(service=service)
    lead_tool = TeamTool(service=service, name="team_lead")
    member_tool = TeamTool(service=service, name="team_member", member_name="dev")

    assert lead_tool.definition.name == "team_lead"
    assert member_tool.definition.name == "team_member"
    assert parent_tool.definition.description.startswith("创建、接管、查看和协调持久化本地团队。")
    assert lead_tool.definition.description.startswith("编排团队批次、成员、任务、消息、审批和本地集成。")
    assert member_tool.definition.description.startswith("领取团队任务、提交计划、报告状态并交换团队消息。")
    assert "不同 action 使用不同参数" in lead_tool.definition.description
    assert lead_tool.definition.runtime_scope is ToolRuntimeScope.PARENT_ONLY
    assert member_tool.definition.runtime_scope is ToolRuntimeScope.PARENT_ONLY
    assert "create_task" in lead_tool.definition.parameters["properties"]["action"]["enum"]
    assert "plan_submit" in member_tool.definition.parameters["properties"]["action"]["enum"]
    assert "spawn_member" not in member_tool.definition.parameters["properties"]["action"]["enum"]
    assert "force" in lead_tool.definition.parameters["properties"]
    assert "error" in lead_tool.definition.parameters["properties"]


def test_team_lead_tool_dispatches_task_plan_shutdown_and_integration_actions(tmp_path: Path):
    service = FakeTeamService(tmp_path)
    tool = TeamTool(service=service, name="team_lead")

    created = asyncio.run(
        tool.execute_async(
            {
                "action": "create_task",
                "task_id": "task-1",
                "batch_id": "batch-1",
                "title": "Build",
                "description": "Build it",
                "dependency_ids": [],
                "kind": "code",
            }
        )
    )
    decision = asyncio.run(
        tool.execute_async(
            {
                "action": "plan_decision",
                "task_id": "task-1",
                "expected_revision": 1,
                "plan_revision": 0,
                "approved": True,
                "message_id": "decision-1",
                "target_name": "dev",
                "body": "approved",
            }
        )
    )
    shutdown = asyncio.run(
        tool.execute_async(
            {
                "action": "shutdown_request",
                "message_id": "shutdown-1",
                "target_name": "dev",
                "body": "stop when idle",
            }
        )
    )
    integration = asyncio.run(tool.execute_async({"action": "integrate", "batch_id": "batch-1"}))

    assert created.content["task_id"] == "task-1"
    assert decision.content["approval_state"] == "approved"
    assert shutdown.content["message_id"] == "shutdown-1"
    assert integration.content["batch_id"] == "batch-1"
    assert service.calls[0][0] == "create_task"
    assert service.calls[1] == ("get_task", "task-1")
    assert service.calls[2][0] == "update_task"
    assert service.calls[3][0] == "transition_task"
    assert service.calls[4][0] == "send_message"
    assert service.calls[4][1].protocol is MessageProtocol.PLAN_DECISION
    assert service.calls[5][0] == "send_message"
    assert service.calls[5][1].protocol is MessageProtocol.SHUTDOWN_REQUEST
    assert service.calls[6] == ("integrate_batch", "batch-1")


def test_team_member_tool_dispatches_member_scoped_actions(tmp_path: Path):
    service = FakeTeamService(tmp_path)
    tool = TeamTool(service=service, name="team_member", member_name="dev")

    claim = asyncio.run(
        tool.execute_async(
            {
                "action": "claim_task",
                "task_id": "task-1",
                "expected_revision": 1,
            }
        )
    )
    plan = asyncio.run(
        tool.execute_async(
            {
                "action": "plan_submit",
                "message_id": "plan-1",
                "task_id": "task-1",
                "batch_id": "batch-1",
                "expected_revision": 2,
                "plan_revision": 3,
                "body": "I will change the parser.",
                "summary": "parser plan",
            }
        )
    )
    status = asyncio.run(
        tool.execute_async(
            {
                "action": "status_update",
                "message_id": "status-1",
                "body": "idle",
                "summary": "idle",
            }
        )
    )
    response = asyncio.run(
        tool.execute_async(
            {
                "action": "shutdown_response",
                "message_id": "shutdown-response-1",
                "body": "checkpoint saved",
            }
        )
    )

    assert claim.content["owner"] == "dev"
    assert plan.content["plan_revision"] == 3
    assert status.content["message_id"] == "status-1"
    assert response.content["message_id"] == "shutdown-response-1"
    assert service.calls[0] == ("claim_task", "task-1", "dev", 1)
    assert service.calls[1] == ("get_task", "task-1")
    assert service.calls[2][0] == "update_task"
    assert service.calls[3][0] == "transition_task"
    assert service.calls[4][0] == "send_message"
    assert service.calls[4][1].protocol is MessageProtocol.PLAN_SUBMIT
    assert service.calls[5][1].protocol is MessageProtocol.STATUS_UPDATE
    assert service.calls[6][1].protocol is MessageProtocol.SHUTDOWN_RESPONSE


def test_team_member_tool_rejects_spoofed_member_identity(tmp_path: Path):
    service = FakeTeamService(tmp_path)
    tool = TeamTool(service=service, name="team_member", member_name="dev")

    claim = asyncio.run(
        tool.execute_async(
            {
                "action": "claim_task",
                "task_id": "task-1",
                "member_name": "ops",
                "expected_revision": 1,
            }
        )
    )
    message = asyncio.run(
        tool.execute_async(
            {
                "action": "send_message",
                "message_id": "msg-3",
                "target_name": "lead",
                "sender": "ops",
                "body": "spoofed",
            }
        )
    )
    update = asyncio.run(
        tool.execute_async(
            {
                "action": "update_task",
                "task_id": "task-1",
                "member_name": "ops",
                "expected_revision": 1,
                "title": "Spoofed",
            }
        )
    )

    assert claim.ok is False
    assert "member_name" in claim.error
    assert message.ok is False
    assert "sender" in message.error
    assert update.ok is False
    assert "member_name" in update.error
    assert service.calls == []


def test_team_member_tool_rejects_updates_and_transitions_for_other_owners(tmp_path: Path):
    service = FakeTeamService(tmp_path)
    service.tasks["task-1"] = TeamTask(
        task_id="task-1",
        batch_id="batch-1",
        title="Task",
        description="Do it",
        dependency_ids=(),
        kind=TaskKind.CODE,
        owner="ops",
        state=TeamTaskState.CLAIMED,
        revision=2,
    )
    tool = TeamTool(service=service, name="team_member", member_name="dev")

    update = asyncio.run(
        tool.execute_async(
            {
                "action": "update_task",
                "task_id": "task-1",
                "expected_revision": 2,
                "title": "Changed",
            }
        )
    )
    transition = asyncio.run(
        tool.execute_async(
            {
                "action": "transition_task",
                "task_id": "task-1",
                "expected_revision": 2,
                "state": "running",
            }
        )
    )

    assert update.ok is False
    assert update.content["reason_code"] == "task_owner_mismatch"
    assert transition.ok is False
    assert transition.content["reason_code"] == "task_owner_mismatch"
    assert not any(call[0] == "update_task" for call in service.calls)
    assert not any(call[0] == "transition_task" for call in service.calls)


def test_team_member_tool_requires_approval_before_running_approval_member_task(tmp_path: Path):
    service = FakeTeamService(tmp_path)
    service.approval_required_members.add("dev")
    service.tasks["task-1"] = TeamTask(
        task_id="task-1",
        batch_id="batch-1",
        title="Task",
        description="Do it",
        dependency_ids=(),
        kind=TaskKind.CODE,
        owner="dev",
        state=TeamTaskState.CLAIMED,
        revision=2,
    )
    tool = TeamTool(service=service, name="team_member", member_name="dev")

    result = asyncio.run(
        tool.execute_async(
            {
                "action": "transition_task",
                "task_id": "task-1",
                "expected_revision": 2,
                "state": "running",
            }
        )
    )

    assert result.ok is False
    assert result.content["reason_code"] == "approval_required"
    assert not any(call[0] == "transition_task" for call in service.calls)


def test_team_tool_send_message_accepts_task_and_batch_metadata(tmp_path: Path):
    service = FakeTeamService(tmp_path)
    tool = TeamTool(service=service, name="team_lead")

    result = asyncio.run(
        tool.execute_async(
            {
                "action": "send_message",
                "message_id": "msg-2",
                "target_name": "dev",
                "task_id": "task-1",
                "batch_id": "batch-1",
                "body": "task-scoped update",
            }
        )
    )

    assert result.ok is True
    assert service.calls[0][0] == "send_message"
    assert service.calls[0][1].task_id == "task-1"
    assert service.calls[0][1].batch_id == "batch-1"


def test_team_tool_rejects_unknown_actions_and_unknown_arguments(tmp_path: Path):
    tool = TeamTool(service=FakeTeamService(tmp_path))

    unknown_action = asyncio.run(tool.execute_async({"action": "bogus"}))
    unknown_argument = asyncio.run(tool.execute_async({"action": "status", "extra": True}))

    assert unknown_action.ok is False
    assert unknown_action.content["reason_code"] == "unknown_team_action"
    assert unknown_argument.ok is False
    assert unknown_argument.content["reason_code"] == "unknown_team_argument"


def test_team_tool_rejects_non_boolean_force_and_broadcast_arguments(tmp_path: Path):
    tool = TeamTool(service=FakeTeamService(tmp_path), name="team_lead")

    force = asyncio.run(
        tool.execute_async(
            {
                "action": "terminate_member",
                "member_name": "dev",
                "force": "false",
            }
        )
    )
    broadcast = asyncio.run(
        tool.execute_async(
            {
                "action": "send_message",
                "message_id": "msg-4",
                "broadcast": "false",
                "body": "hello",
            }
        )
    )

    assert force.ok is False
    assert "force" in force.error
    assert broadcast.ok is False
    assert "broadcast" in broadcast.error


def test_team_tool_policy_computes_parent_lead_member_and_coordinator_visibility():
    candidates = frozenset(
        {
            "team",
            "team_lead",
            "team_member",
            "read_file",
            "write_file",
            "edit_file",
            "run_command",
            "Agent",
        }
    )

    assert TeamToolPolicy(role=TeamRuntimeRole.PARENT).visible_names(candidates) == frozenset({"team"})
    assert TeamToolPolicy(role=TeamRuntimeRole.LEAD).visible_names(candidates) == frozenset(
        {"team", "team_lead", "read_file", "write_file", "edit_file", "run_command", "Agent"}
    )
    assert TeamToolPolicy(role=TeamRuntimeRole.MEMBER).visible_names(candidates) == frozenset(
        {"team_member", "read_file", "write_file", "edit_file", "run_command"}
    )
    assert TeamToolPolicy(role=TeamRuntimeRole.LEAD, coordinator_enabled=True).visible_names(candidates) == frozenset(
        {"team", "team_lead", "read_file", "run_command"}
    )


def test_team_tool_policy_denies_hidden_and_coordinator_write_tools():
    policy = TeamToolPolicy(role=TeamRuntimeRole.LEAD, coordinator_enabled=True)
    write_definition = ToolDefinition(
        name="write_file",
        description="write",
        parameters={"type": "object", "properties": {}, "required": []},
        kind=ToolKind.WRITE,
    )
    run_definition = ToolDefinition(
        name="run_command",
        description="run",
        parameters={
            "type": "object",
            "properties": {"command": {"type": "string"}},
            "required": ["command"],
        },
        kind=ToolKind.WRITE,
    )

    write_decision = policy.evaluate(ToolCall("call-1", "write_file", {}), write_definition)
    agent_decision = policy.evaluate(
        ToolCall("call-2", "Agent", {}),
        ToolDefinition(
            name="Agent",
            description="agent",
            parameters={"type": "object", "properties": {}, "required": []},
            kind=ToolKind.WRITE,
        ),
    )
    shell_decision = policy.evaluate(
        ToolCall("call-3", "run_command", {"command": "echo hi"}),
        run_definition,
    )
    git_decision = policy.evaluate(
        ToolCall("call-4", "run_command", {"command": "git status --short"}),
        run_definition,
    )

    assert write_decision.effect is PermissionEffect.DENY
    assert write_decision.reason_code == "coordinator_write_forbidden"
    assert agent_decision.reason_code == "coordinator_agent_forbidden"
    assert shell_decision.reason_code == "coordinator_shell_forbidden"
    assert git_decision.effect is PermissionEffect.ALLOW


def test_team_tool_policy_allows_coordinator_orchestration_tools_even_though_they_write():
    policy = TeamToolPolicy(role=TeamRuntimeRole.LEAD, coordinator_enabled=True)
    team_definition = ToolDefinition(
        name="team",
        description="team",
        parameters={"type": "object", "properties": {}, "required": []},
        kind=ToolKind.WRITE,
    )
    lead_definition = ToolDefinition(
        name="team_lead",
        description="team lead",
        parameters={"type": "object", "properties": {}, "required": []},
        kind=ToolKind.WRITE,
    )

    assert policy.evaluate(ToolCall("call-1", "team", {}), team_definition).effect is PermissionEffect.ALLOW
    assert policy.evaluate(ToolCall("call-2", "team_lead", {}), lead_definition).effect is PermissionEffect.ALLOW


def test_team_lead_plan_decision_requires_matching_plan_revision(tmp_path: Path):
    service = FakeTeamService(tmp_path)
    service.tasks["task-1"] = TeamTask(
        task_id="task-1",
        batch_id="batch-1",
        title="Task",
        description="Do it",
        dependency_ids=(),
        kind=TaskKind.CODE,
        owner="dev",
        state=TeamTaskState.AWAITING_APPROVAL,
        plan_revision=5,
        revision=7,
    )
    tool = TeamTool(service=service, name="team_lead")

    result = asyncio.run(
        tool.execute_async(
            {
                "action": "plan_decision",
                "task_id": "task-1",
                "expected_revision": 7,
                "plan_revision": 4,
                "approved": True,
                "message_id": "decision-1",
                "target_name": "dev",
                "body": "approved",
            }
        )
    )

    assert result.ok is False
    assert result.content["reason_code"] == "plan_revision_mismatch"
    assert not any(call[0] == "update_task" for call in service.calls)


def test_approval_protocol_moves_task_through_awaiting_approval_to_running(tmp_path: Path):
    service = FakeTeamService(tmp_path)
    service.tasks["task-1"] = TeamTask(
        task_id="task-1",
        batch_id="batch-1",
        title="Task",
        description="Do it",
        dependency_ids=(),
        kind=TaskKind.CODE,
        owner="dev",
        state=TeamTaskState.CLAIMED,
        revision=3,
    )
    member = TeamTool(service=service, name="team_member", member_name="dev")
    lead = TeamTool(service=service, name="team_lead")

    submitted = asyncio.run(
        member.execute_async(
            {
                "action": "plan_submit",
                "message_id": "plan-1",
                "task_id": "task-1",
                "batch_id": "batch-1",
                "expected_revision": 3,
                "plan_revision": 1,
                "body": "implement it",
            }
        )
    )
    assert submitted.ok is True
    assert submitted.content["state"] == "awaiting_approval"
    assert submitted.content["approval_state"] == "pending"

    decided = asyncio.run(
        lead.execute_async(
            {
                "action": "plan_decision",
                "task_id": "task-1",
                "expected_revision": submitted.content["revision"],
                "plan_revision": 1,
                "approved": True,
            }
        )
    )
    assert decided.ok is True
    assert decided.content["state"] == "running"
    assert decided.content["approval_state"] == "approved"


def test_member_policy_denies_workspace_writes_until_approval_provider_allows_them():
    policy = TeamToolPolicy(
        role=TeamRuntimeRole.MEMBER,
        member_write_allowed_provider=lambda: False,
    )
    write_definition = ToolDefinition(
        name="write_file",
        description="write",
        parameters={"type": "object", "properties": {}, "required": []},
        kind=ToolKind.WRITE,
    )
    member_definition = TeamTool(service=object(), name="team_member").definition

    write = policy.evaluate(ToolCall("write-1", "write_file", {}), write_definition)
    control = policy.evaluate(ToolCall("team-1", "team_member", {}), member_definition)

    assert write.effect is PermissionEffect.DENY
    assert write.reason_code == "member_approval_required"
    assert control.effect is PermissionEffect.ALLOW


def test_team_tool_policy_denied_result_shape():
    policy = TeamToolPolicy(role=TeamRuntimeRole.MEMBER)
    decision = policy._deny(
        "team_tool_hidden",
        "hidden",
        PermissionMode.DEFAULT,
        MappingProxyType({}),
    )
    result = policy.denied_result(ToolCall("call-1", "team", {}), decision)

    assert result.ok is False
    assert result.content["reason_code"] == "team_tool_hidden"
    assert result.error == "hidden"


def test_team_permission_interceptor_delegates_normal_tools_before_team_activation():
    calls = []

    class Permission:
        async def before_tool(self, call, definition, *, plan_only, round_index):
            calls.append((call.name, plan_only, round_index))
            return PermissionDecision(
                effect=PermissionEffect.ALLOW,
                reason_code="normal_allowed",
                message_zh="allowed",
                mode=PermissionMode.DEFAULT,
                display_arguments=MappingProxyType({}),
            )

    interceptor = TeamPermissionInterceptor(
        policy_provider=lambda: None,
        permission=Permission(),
    )

    decision = asyncio.run(
        interceptor.before_tool(
            ToolCall("call-1", "read_file", {}),
            ToolDefinition(
                name="read_file",
                description="read",
                parameters={"type": "object", "properties": {}, "required": []},
                kind=ToolKind.READ,
            ),
            plan_only=False,
            round_index=2,
        )
    )

    assert decision.reason_code == "normal_allowed"
    assert calls == [("read_file", False, 2)]
