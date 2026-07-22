from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Literal

from mycode.llm import ChatMessage


@dataclass(frozen=True)
class CompactConfig:
    context_window_tokens: int
    tool_result_threshold_tokens: int = 8_000
    tool_batch_threshold_tokens: int = 12_000


@dataclass(frozen=True)
class CompactPolicy:
    preview_tokens: int = 2_000
    auto_reserve_tokens: int = 13_000
    manual_reserve_tokens: int = 3_000
    keep_recent_tokens: int = 10_000
    min_recent_messages: int = 5
    max_attempts: int = 3
    stale_after_seconds: int = 86_400


@dataclass(frozen=True)
class RequestSnapshot:
    ascii_chars: int
    non_ascii_chars: int
    fingerprint: str


@dataclass(frozen=True)
class TokenEstimate:
    tokens: int
    source: Literal["full_chars", "usage_delta"]
    anchor_input_tokens: int | None
    delta_tokens: int


@dataclass(frozen=True)
class ArchivedArtifact:
    path: str
    kind: Literal["tool_result", "user_message", "history"]
    original_chars: int
    estimated_tokens: int
    sha256: str


@dataclass(frozen=True)
class ArtifactSlice:
    path: str
    text: str
    next_offset: int
    eof: bool


class CompactAction(str, Enum):
    NONE = "none"
    LIGHT = "light"
    HEAVY = "heavy"
    FORCE = "force"
    EMERGENCY = "emergency"


class CompactStatus(str, Enum):
    SAFE = "safe"
    COMPACTED = "compacted"
    NO_OP = "no_op"
    FAILED = "failed"


class CompactFailureCode(str, Enum):
    LLM_ERROR = "llm_error"
    TOOL_ATTEMPT = "tool_attempt"
    INVALID_FORMAT = "invalid_format"
    SUMMARY_TOO_LARGE = "summary_too_large"
    BUDGET_NOT_RECOVERED = "budget_not_recovered"
    ARCHIVE_ERROR = "archive_error"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class CompactReport:
    status: CompactStatus
    actions: tuple[CompactAction, ...]
    before_tokens: int
    after_tokens: int
    archived_count: int
    attempts: int
    circuit_open: bool
    failure_code: CompactFailureCode | None = None
    message_zh: str = ""


@dataclass(frozen=True)
class LightCompactResult:
    history: tuple[ChatMessage, ...]
    artifacts: tuple[ArchivedArtifact, ...]
    changed: bool


@dataclass(frozen=True)
class HeavyCompactResult:
    history: tuple[ChatMessage, ...]
    artifacts: tuple[ArchivedArtifact, ...]
    actions: tuple[CompactAction, ...]
    summary: str


class CompactError(RuntimeError):
    def __init__(self, report: CompactReport) -> None:
        super().__init__(report.message_zh)
        self.report = report
