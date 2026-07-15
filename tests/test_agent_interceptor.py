import asyncio

from mycode.agent import AgentMode, InterceptDecisionType, PlanOnlyInterceptor
from mycode.tool import ToolCall, ToolDefinition, ToolKind, ToolResult


def definition(kind: ToolKind) -> ToolDefinition:
    return ToolDefinition(
        name=f"{kind.value}_tool",
        description="Test tool.",
        parameters={"type": "object", "properties": {}, "required": []},
        kind=kind,
    )


def test_plan_only_interceptor_allows_read_and_write_when_mode_is_off():
    interceptor = PlanOnlyInterceptor()
    mode = AgentMode(plan_only=False)
    call = ToolCall(id="call-1", name="test", arguments={})

    read_decision = asyncio.run(interceptor.before_tool(call, definition(ToolKind.READ), mode, round_index=1))
    write_decision = asyncio.run(interceptor.before_tool(call, definition(ToolKind.WRITE), mode, round_index=1))

    assert read_decision.type == InterceptDecisionType.ALLOW
    assert write_decision.type == InterceptDecisionType.ALLOW


def test_plan_only_interceptor_allows_read_tools_when_mode_is_on():
    interceptor = PlanOnlyInterceptor()
    mode = AgentMode(plan_only=True)

    decision = asyncio.run(
        interceptor.before_tool(
            ToolCall(id="call-1", name="read_file", arguments={}),
            definition(ToolKind.READ),
            mode,
            round_index=1,
        )
    )

    assert decision.type == InterceptDecisionType.ALLOW


def test_plan_only_interceptor_requires_approval_for_write_tools_when_mode_is_on():
    interceptor = PlanOnlyInterceptor()
    mode = AgentMode(plan_only=True)

    decision = asyncio.run(
        interceptor.before_tool(
            ToolCall(id="call-1", name="edit_file", arguments={}),
            definition(ToolKind.WRITE),
            mode,
            round_index=1,
        )
    )

    assert decision.type == InterceptDecisionType.REQUIRE_APPROVAL
    assert "plan-only" in decision.reason


def test_plan_only_interceptor_after_tool_returns_result_unchanged():
    interceptor = PlanOnlyInterceptor()
    result = ToolResult(ok=True, tool_name="read_file", content={"text": "hello"})

    returned = asyncio.run(
        interceptor.after_tool(
            ToolCall(id="call-1", name="read_file", arguments={}),
            result,
            AgentMode(plan_only=True),
            round_index=1,
        )
    )

    assert returned is result
