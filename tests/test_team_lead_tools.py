from __future__ import annotations

import asyncio

from datetime import datetime, timezone

from mycode.team.tooling.lead_tools import (
    TeamAttachTool,
    TeamClarificationRespondTool,
    TeamCreateTool,
    TeamMemberSpawnTool,
    TeamRequestListTool,
    TeamToolApprovalRespondTool,
    TeamUserDecisionRequestTool,
)
from mycode.team.domain.models import MemberState
from mycode.team.infrastructure.requests import TeamRequest, TeamRequestKind, TeamRequestState


class FakeLeadService:
    def __init__(self) -> None:
        self.calls = []
        self.team_name = "team-a"
        self.requests = []

    async def spawn_member(self, **kwargs):
        self.calls.append(("spawn_member", kwargs))
        return type("Member", (), {"member_name": kwargs["member_name"], "state": MemberState.RUNNING})()

    async def list_requests(self, *, state=None):
        return tuple(item for item in self.requests if state is None or item.state is state)

    async def get_request(self, request_id):
        return next(item for item in self.requests if item.request_id == request_id)

    async def create_request(self, request):
        self.requests.append(request)
        return request

    async def resolve_request(self, request_id, **kwargs):
        current = await self.get_request(request_id)
        resolved = type(current)(
            **{
                **current.__dict__,
                "state": kwargs["state"],
                "resolution": kwargs["resolution"],
                "resolved_by": kwargs["resolved_by"],
                "resolved_at": datetime.now(timezone.utc),
            }
        )
        self.requests[self.requests.index(current)] = resolved
        return resolved


def test_lead_member_spawn_passes_only_service_derived_arguments() -> None:
    service = FakeLeadService()
    tool = TeamMemberSpawnTool(service)

    result = asyncio.run(
        tool.execute_async(
            {
                "member_name": "dev",
                "role_name": "builder",
                "task_id": "task-1",
                "goal": "ship feature",
            }
        )
    )

    assert result.ok is True
    assert service.calls == [
        (
            "spawn_member",
            {
                "member_name": "dev",
                "role_name": "builder",
                "task_id": "task-1",
                "goal": "ship feature",
            },
        )
    ]


def test_lead_member_spawn_rejects_old_backend_and_policy_arguments() -> None:
    tool = TeamMemberSpawnTool(FakeLeadService())

    result = asyncio.run(
        tool.execute_async(
            {
                "member_name": "dev",
                "role_name": "builder",
                "task_id": "task-1",
                "goal": "ship feature",
                "requested_backend": "in_process",
            }
        )
    )

    assert result.ok is False
    assert result.content["reason_code"] == "unknown_argument"
    assert result.content["field"] == "requested_backend"


def test_lead_create_and_attach_emit_round_control() -> None:
    class CreateAttachService(FakeLeadService):
        async def create_team(self, name, goal=None):
            self.calls.append(("create_team", name, goal))
            return type(
                "Snapshot",
                (),
                {"team": type("Team", (), {"team_name": name, "state": type("State", (), {"value": "active"})()})()},
            )()

        async def attach_team(self, name):
            self.calls.append(("attach_team", name))
            return type(
                "Snapshot",
                (),
                {"team": type("Team", (), {"team_name": name, "state": type("State", (), {"value": "active"})()})()},
            )()

    service = CreateAttachService()

    created = asyncio.run(TeamCreateTool(service).execute_async({"team_name": "alpha"}))
    attached = asyncio.run(TeamAttachTool(service).execute_async({"team_name": "alpha"}))

    assert created.control.stop_current_round is True
    assert created.control.replan_next_round is True
    assert attached.control.stop_current_round is True
    assert attached.control.replan_next_round is True


def _clarification_request():
    return TeamRequest(
        request_id="request-1",
        team_name="team-a",
        batch_id="batch-1",
        task_id="task-1",
        member_name="dev",
        kind=TeamRequestKind.CLARIFICATION,
        question="Which API?",
        options=("v1", "v2"),
        context_summary="Need a choice.",
        state=TeamRequestState.PENDING,
        created_at=datetime.now(timezone.utc),
    )


def test_lead_can_list_and_resolve_member_clarification() -> None:
    service = FakeLeadService()
    service.requests.append(_clarification_request())

    listed = asyncio.run(TeamRequestListTool(service).execute_async({}))
    responded = asyncio.run(
        TeamClarificationRespondTool(service).execute_async(
            {"request_id": "request-1", "resolution": "v2"}
        )
    )

    assert listed.ok is True
    assert listed.content["requests"][0]["state"] == "pending"
    assert responded.ok is True
    assert service.requests[0].state is TeamRequestState.RESOLVED
    assert responded.content["resolution"] == "v2"


def test_lead_user_decision_creates_pending_request_without_blocking() -> None:
    service = FakeLeadService()

    result = asyncio.run(
        TeamUserDecisionRequestTool(service).execute_async(
            {
                "request_id": "user-request-1",
                "question": "Which behavior is intended?",
                "options": ["strict", "lenient"],
                "context_summary": "Both are technically valid.",
            }
        )
    )

    assert result.ok is True
    assert result.content["state"] == "pending"
    assert service.requests[0].kind is TeamRequestKind.USER_DECISION


def test_lead_can_reject_tool_approval() -> None:
    service = FakeLeadService()
    request = _clarification_request()
    service.requests.append(type(request)(**{**request.__dict__, "kind": TeamRequestKind.TOOL_APPROVAL}))

    result = asyncio.run(
        TeamToolApprovalRespondTool(service).execute_async(
            {
                "request_id": "request-1",
                "approved": False,
                "resolution": "unsafe command",
            }
        )
    )

    assert result.ok is True
    assert service.requests[0].state is TeamRequestState.REJECTED
