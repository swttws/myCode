from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from mycode.llm import ChatMessage
from mycode.tool import ToolDefinition


@dataclass(frozen=True)
class PromptConfig:
    full_reminder_interval_rounds: int = 4
    environment_value_limit: int = 512
    git_timeout_seconds: float = 1.0

    def __post_init__(self) -> None:
        if self.full_reminder_interval_rounds < 1:
            raise ValueError("full_reminder_interval_rounds must be positive")
        if self.environment_value_limit < 1:
            raise ValueError("environment_value_limit must be positive")
        if self.git_timeout_seconds <= 0:
            raise ValueError("git_timeout_seconds must be positive")


@dataclass(frozen=True)
class PromptModuleDefinition:
    id: str
    priority: int
    protected: bool = False


@dataclass(frozen=True)
class StablePromptContext:
    tools: tuple[ToolDefinition, ...]


class PromptModule(Protocol):
    @property
    def definition(self) -> PromptModuleDefinition:
        raise NotImplementedError

    def render(self, context: StablePromptContext) -> str:
        raise NotImplementedError


@dataclass(frozen=True)
class PromptDiagnostic:
    code: str
    source: str
    message: str


@dataclass(frozen=True)
class EnvironmentSnapshot:
    workspace: str | None
    operating_system: str
    current_time: str
    timezone: str
    git_branch: str | None
    git_status: str | None
    diagnostics: tuple[PromptDiagnostic, ...]


@dataclass(frozen=True)
class SystemReminder:
    id: str
    full_content: str
    concise_content: str


@dataclass(frozen=True)
class PromptContextBlock:
    id: str
    kind: str
    priority: int
    content: str


@dataclass(frozen=True)
class TurnPromptContext:
    turn_id: int
    environment: EnvironmentSnapshot
    plan_only: bool
    reminders: tuple[SystemReminder, ...]
    framework_blocks: tuple[PromptContextBlock, ...] = ()


@dataclass(frozen=True)
class PromptBuildMetadata:
    enabled_module_ids: tuple[str, ...]
    stable_prompt_sha256: str
    diagnostics: tuple[PromptDiagnostic, ...]


@dataclass(frozen=True)
class PromptBuildResult:
    messages: tuple[ChatMessage, ...]
    tools: tuple[ToolDefinition, ...]
    metadata: PromptBuildMetadata
