from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import Enum

from mycode.tool import ToolCall


@dataclass(frozen=True)
class ApprovalRequest:
    id: str
    tool_call: ToolCall
    reason: str
    plan_only: bool
    round_index: int


class ApprovalDecisionType(str, Enum):
    APPROVE_ONCE = "approve_once"
    REJECT = "reject"
    CANCEL = "cancel"


@dataclass(frozen=True)
class ApprovalDecision:
    type: ApprovalDecisionType


ApprovalProvider = Callable[[ApprovalRequest], Awaitable[ApprovalDecision]]
