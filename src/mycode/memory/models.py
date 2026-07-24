from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from mycode.llm import ChatMessage


class MemoryScope(str, Enum):
    USER = "user"
    PROJECT = "project"


class MemoryKind(str, Enum):
    USER_PREFERENCE = "user_preference"
    CORRECTION = "correction"
    PROJECT_KNOWLEDGE = "project_knowledge"
    REFERENCE = "reference"


class InstructionLayer(str, Enum):
    PROJECT_ROOT = "project_root"
    PROJECT_DIRECTORY = "project_directory"
    USER = "user"


class SessionRecordType(str, Enum):
    MESSAGE = "message"


class FrameworkContextKind(str, Enum):
    INSTRUCTIONS = "instructions"
    MEMORY_INDEX = "memory_index"
    RESTORE_NOTICE = "restore_notice"


class NoteUpdateAction(str, Enum):
    CREATE = "create"
    MERGE = "merge"
    UPDATE = "update"
    IGNORE = "ignore"


@dataclass(frozen=True)
class MemoryDiagnostic:
    code: str
    message: str
    scope: MemoryScope | None = None
    path: str | None = None
    line: int | None = None


@dataclass(frozen=True)
class InstructionBlock:
    layer: InstructionLayer
    path: str
    priority: int
    text: str
    sha256: str


@dataclass(frozen=True)
class InstructionLoadResult:
    blocks: tuple[InstructionBlock, ...]
    rendered_text: str
    diagnostics: tuple[MemoryDiagnostic, ...] = ()


@dataclass(frozen=True)
class SessionRecord:
    type: SessionRecordType
    timestamp: str
    role: str
    content: str
    tool_call_id: str | None = None
    tool_name: str | None = None
    tool_arguments: str | None = None
    origin: str = "conversation"


@dataclass(frozen=True)
class SessionSummary:
    session_id: str
    path: str
    title: str
    message_count: int
    updated_at: str | None = None
    recoverable: bool = False


@dataclass(frozen=True)
class FrameworkContextBlock:
    id: str
    kind: FrameworkContextKind
    priority: int
    content: str


@dataclass(frozen=True)
class SessionRestoreResult:
    summary: SessionSummary | None
    history: tuple[ChatMessage, ...]
    skipped_lines: int
    truncated_at_boundary: bool
    time_gap_seconds: int | None = None
    time_gap_block: FrameworkContextBlock | None = None
    diagnostics: tuple[MemoryDiagnostic, ...] = ()


@dataclass(frozen=True)
class MemoryNote:
    note_id: str
    scope: MemoryScope
    kind: MemoryKind
    path: str
    frontmatter: dict[str, str]
    body: str
    updated_at: str
    source_session_id: str | None = None
    sha256: str = ""


@dataclass(frozen=True)
class MemoryIndexBundle:
    scope: MemoryScope
    entries: tuple[str, ...]
    rendered_text: str
    line_count: int
    byte_count: int
    truncated: bool
    diagnostics: tuple[MemoryDiagnostic, ...] = ()


@dataclass(frozen=True)
class FrameworkContext:
    blocks: tuple[FrameworkContextBlock, ...]
    restored_history: tuple[ChatMessage, ...]
    session_summary: SessionSummary | None = None
    diagnostics: tuple[MemoryDiagnostic, ...] = ()


@dataclass(frozen=True)
class NoteUpdateDecision:
    action: NoteUpdateAction
    scope: MemoryScope | None = None
    kind: MemoryKind | None = None
    target_note_id: str | None = None
    title: str | None = None
    body: str | None = None
    reason: str = ""


@dataclass(frozen=True)
class NoteUpdateResult:
    created: int
    merged: int
    updated: int
    ignored: int
    diagnostics: tuple[MemoryDiagnostic, ...] = ()

