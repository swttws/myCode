from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from types import MappingProxyType

from mycode.permission.models import PermissionDecision, PermissionEffect, PermissionMode
from mycode.team import (
    BatchRecord,
    BatchState,
    DeliveryReceipt,
    MemberBackend,
    MessageProtocol,
    TeamMessage,
    TeamRecord,
    TeamSnapshot,
    TeamState,
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

    async def send_message(self, message: TeamMessage):
        self.calls.append(("send_message", message))
        return DeliveryReceipt(
            message_id=message.message_id,
            recipient_names=(message.target_name or "broadcast",),
            delivered_at=NOW,
            fanout_count=1,
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


def test_team_tool_rejects_unknown_actions_and_unknown_arguments(tmp_path: Path):
    tool = TeamTool(service=FakeTeamService(tmp_path))

    unknown_action = asyncio.run(tool.execute_async({"action": "bogus"}))
    unknown_argument = asyncio.run(tool.execute_async({"action": "status", "extra": True}))

    assert unknown_action.ok is False
    assert unknown_action.content["reason_code"] == "unknown_team_action"
    assert unknown_argument.ok is False
    assert unknown_argument.content["reason_code"] == "unknown_team_argument"


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
