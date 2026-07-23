from dataclasses import FrozenInstanceError, fields, is_dataclass

from mycode.llm import ChatMessage
from mycode.memory import (
    FrameworkContext,
    FrameworkContextBlock,
    FrameworkContextKind,
    InstructionBlock,
    InstructionLayer,
    InstructionLoadResult,
    MemoryDiagnostic,
    MemoryIndexBundle,
    MemoryKind,
    MemoryNote,
    MemoryScope,
    NoteUpdateAction,
    NoteUpdateDecision,
    NoteUpdateResult,
    SessionRecord,
    SessionRecordType,
    SessionRestoreResult,
    SessionSummary,
)


def test_memory_models_are_immutable_and_have_expected_fields():
    for model in (
        MemoryDiagnostic,
        InstructionBlock,
        InstructionLoadResult,
        SessionRecord,
        SessionSummary,
        SessionRestoreResult,
        MemoryNote,
        MemoryIndexBundle,
        FrameworkContextBlock,
        FrameworkContext,
        NoteUpdateDecision,
        NoteUpdateResult,
    ):
        assert is_dataclass(model)
        assert model.__dataclass_params__.frozen is True

    assert [field.name for field in fields(MemoryDiagnostic)] == [
        "code",
        "message",
        "scope",
        "path",
        "line",
    ]
    assert [field.name for field in fields(InstructionBlock)] == [
        "layer",
        "path",
        "priority",
        "text",
        "sha256",
    ]
    assert [field.name for field in fields(InstructionLoadResult)] == [
        "blocks",
        "rendered_text",
        "diagnostics",
    ]
    assert [field.name for field in fields(SessionRecord)] == [
        "type",
        "timestamp",
        "role",
        "content",
        "tool_call_id",
        "tool_name",
        "tool_arguments",
        "origin",
    ]
    assert [field.name for field in fields(SessionSummary)] == [
        "session_id",
        "path",
        "title",
        "message_count",
        "updated_at",
        "recoverable",
    ]
    assert [field.name for field in fields(SessionRestoreResult)] == [
        "summary",
        "history",
        "skipped_lines",
        "truncated_at_boundary",
        "time_gap_seconds",
        "time_gap_block",
        "diagnostics",
    ]
    assert [field.name for field in fields(MemoryNote)] == [
        "note_id",
        "scope",
        "kind",
        "path",
        "frontmatter",
        "body",
        "updated_at",
        "source_session_id",
        "sha256",
    ]
    assert [field.name for field in fields(MemoryIndexBundle)] == [
        "scope",
        "entries",
        "rendered_text",
        "line_count",
        "byte_count",
        "truncated",
        "diagnostics",
    ]
    assert [field.name for field in fields(FrameworkContextBlock)] == [
        "id",
        "kind",
        "priority",
        "content",
    ]
    assert [field.name for field in fields(FrameworkContext)] == [
        "blocks",
        "restored_history",
        "session_summary",
        "diagnostics",
    ]
    assert [field.name for field in fields(NoteUpdateDecision)] == [
        "action",
        "scope",
        "kind",
        "target_note_id",
        "title",
        "body",
        "reason",
    ]
    assert [field.name for field in fields(NoteUpdateResult)] == [
        "created",
        "merged",
        "updated",
        "ignored",
        "diagnostics",
    ]

    diagnostic = MemoryDiagnostic(code="x", message="y")
    try:
        diagnostic.code = "z"
    except FrozenInstanceError:
        pass
    else:
        raise AssertionError("MemoryDiagnostic should be frozen")


def test_memory_models_support_expected_values_and_relationships():
    assert MemoryScope.USER.value == "user"
    assert MemoryScope.PROJECT.value == "project"
    assert MemoryKind.USER_PREFERENCE.value == "user_preference"
    assert MemoryKind.CORRECTION.value == "correction"
    assert MemoryKind.PROJECT_KNOWLEDGE.value == "project_knowledge"
    assert MemoryKind.REFERENCE.value == "reference"
    assert InstructionLayer.PROJECT_ROOT.value == "project_root"
    assert InstructionLayer.PROJECT_DIRECTORY.value == "project_directory"
    assert InstructionLayer.USER.value == "user"
    assert SessionRecordType.MESSAGE.value == "message"
    assert FrameworkContextKind.INSTRUCTIONS.value == "instructions"
    assert FrameworkContextKind.MEMORY_INDEX.value == "memory_index"
    assert FrameworkContextKind.RESTORE_NOTICE.value == "restore_notice"
    assert NoteUpdateAction.CREATE.value == "create"
    assert NoteUpdateAction.MERGE.value == "merge"
    assert NoteUpdateAction.UPDATE.value == "update"
    assert NoteUpdateAction.IGNORE.value == "ignore"

    restore = SessionRestoreResult(
        summary=None,
        history=(ChatMessage(role="user", content="hello"),),
        skipped_lines=1,
        truncated_at_boundary=False,
        time_gap_seconds=12,
        time_gap_block=None,
        diagnostics=(),
    )
    assert restore.time_gap_block is None

