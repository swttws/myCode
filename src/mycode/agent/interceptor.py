from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Protocol

from mycode.agent.state import AgentMode
from mycode.tool import ToolCall, ToolDefinition, ToolKind, ToolResult


class InterceptDecisionType(str, Enum):
    ALLOW = "allow"
    DENY = "deny"
    REQUIRE_APPROVAL = "require_approval"


@dataclass(frozen=True)
class InterceptDecision:
    type: InterceptDecisionType
    reason: str = ""
    result: ToolResult | None = None


class ToolInterceptor(Protocol):
    async def before_tool(
        self,
        call: ToolCall,
        definition: ToolDefinition,
        mode: AgentMode,
        round_index: int,
    ) -> InterceptDecision:
        raise NotImplementedError

    async def after_tool(
        self,
        call: ToolCall,
        result: ToolResult,
        mode: AgentMode,
        round_index: int,
    ) -> ToolResult:
        raise NotImplementedError


class PlanOnlyInterceptor:
    # 默认 plan-only 策略：读工具放行，写工具必须等待上层审批。
    async def before_tool(
        self,
        call: ToolCall,
        definition: ToolDefinition,
        mode: AgentMode,
        round_index: int,
    ) -> InterceptDecision:
        if not mode.plan_only or definition.kind == ToolKind.READ:
            return InterceptDecision(InterceptDecisionType.ALLOW)
        return InterceptDecision(
            InterceptDecisionType.REQUIRE_APPROVAL,
            reason="write tool requires approval in plan-only mode",
        )

    async def after_tool(
        self,
        call: ToolCall,
        result: ToolResult,
        mode: AgentMode,
        round_index: int,
    ) -> ToolResult:
        # Stage 03 暂不修改工具结果，保留后置拦截点供后续审计或权限策略扩展。
        return result
