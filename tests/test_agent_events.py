from mycode.agent import (
    AgentConfig,
    AgentErrorCode,
    AgentEvent,
    AgentEventType,
    AgentMode,
    ApprovalRequest,
)
from mycode.tool import ToolCall


def test_agent_event_type_declares_public_contract():
    assert [event_type.value for event_type in AgentEventType] == [
        "user_message",
        "thinking_delta",
        "text_delta",
        "tool_call_started",
        "tool_result",
        "final_response",
        "error",
        "cancelled",
        "approval_required",
    ]


def test_agent_error_code_declares_machine_readable_values():
    assert AgentErrorCode.MAX_ROUNDS_EXCEEDED.value == "max_rounds_exceeded"


def test_agent_event_can_carry_tool_approval_and_error_context():
    call = ToolCall(id="call-1", name="edit_file", arguments={"path": "README.md"})
    request = ApprovalRequest(
        id="approval-call-1",
        tool_call=call,
        reason="plan-only write tool requires approval",
        plan_only=True,
        round_index=2,
    )

    event = AgentEvent(
        type=AgentEventType.APPROVAL_REQUIRED,
        content="approval required",
        round_index=2,
        tool_call=call,
        approval_request=request,
        error_code=AgentErrorCode.APPROVAL_CANCELLED,
    )

    assert event.round_index == 2
    assert event.tool_call == call
    assert event.approval_request == request
    assert event.error_code == AgentErrorCode.APPROVAL_CANCELLED


def test_agent_mode_reset_turns_off_plan_only():
    mode = AgentMode(plan_only=True)

    mode.reset()

    assert mode.plan_only is False


def test_agent_config_defaults_to_eight_rounds_and_mentions_plan_only():
    config = AgentConfig()

    assert config.max_rounds == 8
    assert "plan-only" in config.minimal_system_prompt
