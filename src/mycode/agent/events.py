from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from mycode.agent.approval import ApprovalRequest
from mycode.llm import UsageObservation
from mycode.tool import ToolCall, ToolResult


class AgentEventType(str, Enum):
    USER_MESSAGE = "user_message"
    THINKING_DELTA = "thinking_delta"
    TEXT_DELTA = "text_delta"
    TOOL_CALL_STARTED = "tool_call_started"
    TOOL_RESULT = "tool_result"
    FINAL_RESPONSE = "final_response"
    ERROR = "error"
    CANCELLED = "cancelled"
    APPROVAL_REQUIRED = "approval_required"
    USAGE = "usage"


class AgentErrorCode(str, Enum):
    LLM_ERROR = "llm_error"
    TOOL_ERROR = "tool_error"
    UNKNOWN_TOOL = "unknown_tool"
    INVALID_TOOL_KIND = "invalid_tool_kind"
    MAX_ROUNDS_EXCEEDED = "max_rounds_exceeded"
    MODEL_TIMEOUT = "model_timeout"
    TOOL_TIMEOUT = "tool_timeout"
    RUN_TIMEOUT = "run_timeout"
    CANCELLED = "cancelled"
    APPROVAL_CANCELLED = "approval_cancelled"
    PROMPT_ERROR = "prompt_error"


@dataclass(frozen=True)
class AgentEvent:
    type: AgentEventType
    content: str = ""
    round_index: int = 0
    tool_call: ToolCall | None = None
    tool_result: ToolResult | None = None
    approval_request: ApprovalRequest | None = None
    error_code: AgentErrorCode | None = None
    usage: UsageObservation | None = None
