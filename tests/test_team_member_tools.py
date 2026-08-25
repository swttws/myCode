from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from mycode.team import DeliveryReceipt, MessageProtocol, TeamMessage
from mycode.team.tooling.member_tools import (
    TeamClarificationRequestTool,
    TeamMessageSendTool,
    TeamStatusUpdateTool,
)


NOW = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)


class FakeMemberService:
    def __init__(self) -> None:
        self.messages = []

    async def send_message(self, message: TeamMessage):
        self.messages.append(message)
        return DeliveryReceipt(
            message_id=message.message_id,
            recipient_names=(message.target_name or "broadcast",),
            delivered_at=NOW,
            fanout_count=1,
        )


class FakeClarificationService(FakeMemberService):
    def __init__(self) -> None:
        super().__init__()
        self.team_name = "team-a"
        self.created_requests = []
        self.tasks = {
            "task-1": type(
                "Task",
                (),
                {
                    "task_id": "task-1",
                    "batch_id": "batch-1",
                    "owner": "dev",
                    "revision": 3,
                },
            )()
        }
        self.transitions = []
        self.member_states = []

    def get_task(self, task_id):
        return self.tasks[task_id]

    def create_request(self, request):
        self.created_requests.append(request)
        return request

    def transition_task(self, task_id, expected_revision, state, result=None, error=None):
        self.transitions.append((task_id, expected_revision, state))
        self.tasks[task_id].revision += 1
        return self.tasks[task_id]

    def set_member_state(self, state):
        self.member_states.append(state)


def test_member_message_tool_rejects_sender_identity_override() -> None:
    service = FakeMemberService()
    tool = TeamMessageSendTool(service, member_name="dev")

    result = asyncio.run(
        tool.execute_async(
            {
                "message_id": "msg-1",
                "sender": "other",
                "body": "done",
            }
        )
    )

    assert result.ok is False
    assert result.content["reason_code"] == "member_identity_mismatch"
    assert service.messages == []


def test_member_status_update_defaults_target_to_lead_and_bound_sender() -> None:
    service = FakeMemberService()
    tool = TeamStatusUpdateTool(service, member_name="dev")

    result = asyncio.run(tool.execute_async({"message_id": "status-1", "body": "idle"}))

    assert result.ok is True
    assert service.messages[0].protocol is MessageProtocol.STATUS_UPDATE
    assert service.messages[0].sender == "dev"
    assert service.messages[0].target_name == "lead"


def test_member_clarification_request_persists_request_and_notifies_lead() -> None:
    service = FakeClarificationService()
    tool = TeamClarificationRequestTool(service, member_name="dev")

    result = asyncio.run(
        tool.execute_async(
            {
                "message_id": "clarification-msg-1",
                "request_id": "request-1",
                "task_id": "task-1",
                "question": "Which API should this use?",
                "options": ["v1", "v2"],
                "context_summary": "Both APIs are available.",
            }
        )
    )

    assert result.ok is True
    assert service.created_requests[0].request_id == "request-1"
    assert service.created_requests[0].member_name == "dev"
    assert service.transitions[0][2].value == "awaiting_input"
    assert service.member_states[0].value == "awaiting_input"
    assert service.messages[0].protocol is MessageProtocol.CLARIFICATION_REQUEST
    assert service.messages[0].target_name == "lead"
    assert service.messages[0].task_id == "task-1"
