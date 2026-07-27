from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from mycode.llm import ChatMessage
from mycode.permission.models import ApprovalProvider
from mycode.prompt.models import PromptContextBlock


MAX_SKILL_FILE_BYTES = 128 * 1024
MAX_FRONTMATTER_BYTES = 16 * 1024
MAX_RESOURCE_BYTES = 1024 * 1024
MAX_RESOURCE_COUNT = 256

_SKILL_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")


class SkillSource(str, Enum):
    BUILTIN = "builtin"
    USER = "user"
    PROJECT = "project"


class SkillMode(str, Enum):
    SHARED = "shared"
    ISOLATED = "isolated"


class SkillContextStrategy(str, Enum):
    NONE = "none"
    RECENT = "recent"
    SUMMARY = "summary"


SOURCE_PRIORITY = {
    SkillSource.BUILTIN: 100,
    SkillSource.USER: 200,
    SkillSource.PROJECT: 300,
}


class SkillError(RuntimeError):
    pass


class SkillParseError(SkillError):
    pass


class SkillStartupError(SkillError):
    pass


class SkillResourceError(SkillError):
    pass


class SkillExecutionError(SkillError):
    pass


@dataclass(frozen=True)
class SkillContextPolicy:
    strategy: SkillContextStrategy
    turns: int = 0

    def __post_init__(self) -> None:
        strategy = SkillContextStrategy(self.strategy)
        object.__setattr__(self, "strategy", strategy)
        if strategy is SkillContextStrategy.RECENT:
            if not isinstance(self.turns, int) or self.turns <= 0:
                raise ValueError("recent context turns must be positive")
            return
        if self.turns != 0:
            raise ValueError("turns are only valid for recent context")


@dataclass(frozen=True)
class SkillMetadata:
    name: str
    description: str
    allowed_tools: tuple[str, ...]
    mode: SkillMode
    context: SkillContextPolicy | None
    model: str | None

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or _SKILL_NAME_PATTERN.fullmatch(self.name) is None:
            raise ValueError("invalid skill name")
        if not isinstance(self.description, str) or not self.description.strip():
            raise ValueError("description must be non-empty")
        if len(self.description) > 200:
            raise ValueError("description must fit within 200 characters")
        allowed_tools = tuple(self.allowed_tools)
        if any(not isinstance(name, str) or not name for name in allowed_tools):
            raise ValueError("allowed tool names must be non-empty strings")
        if len(set(allowed_tools)) != len(allowed_tools):
            raise ValueError("duplicate allowed tool")
        object.__setattr__(self, "allowed_tools", allowed_tools)
        mode = SkillMode(self.mode)
        object.__setattr__(self, "mode", mode)
        if mode is SkillMode.SHARED:
            if self.context is not None:
                raise ValueError("shared skill context must be empty")
        elif self.context is None:
            raise ValueError("isolated skill context is required")
        if self.model is not None and (not isinstance(self.model, str) or not self.model.strip()):
            raise ValueError("model must be non-empty")


@dataclass(frozen=True)
class SkillDiagnostic:
    code: str
    source: SkillSource
    path: str
    message: str
    skill_name: str | None = None


@dataclass(frozen=True)
class SkillCandidate:
    source: SkillSource
    package_root: Path
    entry_path: Path
    fingerprint: tuple[tuple[str, int, int], ...]


@dataclass(frozen=True)
class SkillScanResult:
    candidates: tuple[SkillCandidate, ...]
    diagnostics: tuple[SkillDiagnostic, ...] = ()


@dataclass(frozen=True)
class SkillDefinition:
    metadata: SkillMetadata
    instruction: str
    source: SkillSource
    entry_path: Path
    package_root: Path
    resources: tuple[str, ...]
    revision: str


@dataclass(frozen=True)
class SkillActivation:
    name: str
    arguments: str
    rendered_instruction: str
    revision: str


@dataclass(frozen=True)
class SkillExecutionScope:
    name: str
    allowed_tools: frozenset[str]


@dataclass(frozen=True)
class SkillRunContext:
    history: tuple[ChatMessage, ...]
    framework_blocks: tuple[PromptContextBlock, ...]
    approval_provider: ApprovalProvider | None
    scope: SkillExecutionScope | None
    isolated_depth: int


@dataclass(frozen=True)
class SkillExecutionResult:
    ok: bool
    summary: str
    error_code: str | None = None


@dataclass(frozen=True)
class SkillCatalogSnapshot:
    definitions: tuple[SkillDefinition, ...]
    diagnostics: tuple[SkillDiagnostic, ...]
    generation: int
