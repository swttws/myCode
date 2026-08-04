from dataclasses import FrozenInstanceError, fields, is_dataclass
from pathlib import Path

import pytest

from mycode.llm import UsageObservation
from mycode.subagent.models import (
    AgentIsolationMode,
    AgentModelTier,
    AgentPermissionMode,
    AgentRoleDefinition,
    AgentRoleDiagnostic,
    AgentRoleMetadata,
    AgentRoleSource,
    SubAgentExecutionReport,
    SubAgentKind,
    SubAgentResult,
    SubAgentTaskSnapshot,
    SubAgentTaskState,
    SubAgentTaskSummary,
    SubAgentUsage,
    RESULT_TRUNCATED_MARKER,
    truncate_utf8_bytes,
)


def test_subagent_models_are_frozen_and_have_expected_fields():
    for model in (
        AgentRoleMetadata,
        AgentRoleDefinition,
        AgentRoleDiagnostic,
        SubAgentUsage,
        SubAgentResult,
        SubAgentTaskSummary,
        SubAgentTaskSnapshot,
        SubAgentExecutionReport,
    ):
        assert is_dataclass(model)
        assert model.__dataclass_params__.frozen is True

    assert [field.name for field in fields(AgentRoleMetadata)] == [
        "name",
        "description",
        "allowed_tools",
        "denied_tools",
        "model",
        "max_rounds",
        "permission_mode",
        "isolation",
    ]
    assert [field.name for field in fields(AgentRoleDefinition)] == [
        "metadata",
        "instruction",
        "source",
        "entry_path",
        "revision",
    ]
    assert [field.name for field in fields(SubAgentUsage)] == [
        "input_tokens",
        "output_tokens",
        "total_tokens",
        "cache_read_tokens",
        "cache_write_tokens",
    ]
    assert [field.name for field in fields(SubAgentTaskSummary)] == [
        "id",
        "sequence",
        "task_token",
        "kind",
        "role_name",
        "state",
        "detached",
        "rounds",
        "error_code",
        "usage",
        "isolation",
        "workspace_root",
        "branch_name",
        "workspace_preparation",
        "initialized_rules",
        "disposition",
    ]

    usage = SubAgentUsage(input_tokens=1)
    with pytest.raises(FrozenInstanceError):
        usage.input_tokens = 2


def test_subagent_enums_use_stable_protocol_values():
    assert AgentIsolationMode.SHARED.value == "shared"
    assert AgentIsolationMode.WORKTREE.value == "worktree"
    assert SubAgentKind.DEFINED.value == "defined"
    assert SubAgentKind.FORK.value == "fork"
    assert AgentRoleSource.PLUGIN.value == "plugin"
    assert AgentRoleSource.BUILTIN.value == "builtin"
    assert AgentRoleSource.USER.value == "user"
    assert AgentRoleSource.PROJECT.value == "project"
    assert AgentModelTier.INHERIT.value == "inherit"
    assert AgentModelTier.HAIKU.value == "haiku"
    assert AgentModelTier.SONNET.value == "sonnet"
    assert AgentModelTier.OPUS.value == "opus"
    assert AgentPermissionMode.INHERIT.value == "inherit"
    assert AgentPermissionMode.STRICT.value == "strict"
    assert AgentPermissionMode.DEFAULT.value == "default"
    assert AgentPermissionMode.PERMISSIVE.value == "permissive"
    assert SubAgentTaskState.QUEUED.value == "queued"
    assert SubAgentTaskState.RUNNING.value == "running"
    assert SubAgentTaskState.COMPLETED.value == "completed"
    assert SubAgentTaskState.FAILED.value == "failed"
    assert SubAgentTaskState.CANCELLED.value == "cancelled"


def test_subagent_model_invariants_reject_invalid_error_combinations():
    with pytest.raises(ValueError, match="error_code"):
        AgentRoleDiagnostic(
            code="",
            source=AgentRoleSource.PROJECT,
            path="role.md",
            message="bad role",
        )

    with pytest.raises(ValueError, match="terminal"):
        SubAgentExecutionReport(
            state=SubAgentTaskState.RUNNING,
            rounds=1,
            result=None,
            error_code=None,
            error_message=None,
            usage=SubAgentUsage(),
        )

    with pytest.raises(ValueError, match="error_code"):
        SubAgentExecutionReport(
            state=SubAgentTaskState.FAILED,
            rounds=1,
            result=None,
            error_code=None,
            error_message="模型失败",
            usage=SubAgentUsage(),
        )

    with pytest.raises(ValueError, match="result"):
        SubAgentExecutionReport(
            state=SubAgentTaskState.COMPLETED,
            rounds=1,
            result=None,
            error_code=None,
            error_message=None,
            usage=SubAgentUsage(),
        )

    with pytest.raises(ValueError, match="error_code"):
        SubAgentTaskSnapshot(
            id="task-000001",
            sequence=1,
            kind=SubAgentKind.DEFINED,
            role_name="general",
            state=SubAgentTaskState.FAILED,
            detached=True,
            rounds=1,
            result=None,
            error_code=None,
            error_message="失败",
            usage=SubAgentUsage(),
        )


def test_subagent_models_accept_valid_role_and_task_shapes():
    metadata = AgentRoleMetadata(
        name="general",
        description="通用任务",
        allowed_tools=("*",),
        denied_tools=("Agent",),
        model=AgentModelTier.INHERIT,
        max_rounds=8,
        permission_mode=AgentPermissionMode.INHERIT,
    )
    definition = AgentRoleDefinition(
        metadata=metadata,
        instruction="请非交互地完成任务。",
        source=AgentRoleSource.BUILTIN,
        entry_path=Path("general.md"),
        revision="abc123",
    )
    result = SubAgentResult(detail="完成", summary="完成")
    usage = SubAgentUsage(input_tokens=1, output_tokens=2, total_tokens=3)

    summary = SubAgentTaskSummary(
        id="task-000001",
        sequence=1,
        kind=SubAgentKind.DEFINED,
        role_name="general",
        state=SubAgentTaskState.COMPLETED,
        detached=False,
        rounds=1,
        error_code=None,
        usage=usage,
    )

    assert definition.metadata is metadata
    assert definition.metadata.isolation is AgentIsolationMode.SHARED
    assert summary.usage.total_tokens == 3
    assert result.detail == result.summary


def test_truncate_utf8_bytes_preserves_character_boundaries_and_marks_truncation():
    text = "甲乙丙丁戊己庚辛壬癸甲乙"
    limit = len(("甲乙" + RESULT_TRUNCATED_MARKER).encode("utf-8"))

    truncated, was_truncated = truncate_utf8_bytes(text, limit)

    assert was_truncated is True
    assert truncated == "甲乙" + RESULT_TRUNCATED_MARKER
    assert len(truncated.encode("utf-8")) <= limit
    assert "\ufffd" not in truncated


def test_truncate_utf8_bytes_returns_original_when_within_limit():
    text = "plain text"

    truncated, was_truncated = truncate_utf8_bytes(text, len(text.encode("utf-8")))

    assert truncated == text
    assert was_truncated is False


def test_usage_aggregate_sums_only_fields_reported_by_every_round():
    usage = SubAgentUsage.aggregate(
        (
            UsageObservation(
                provider="fake",
                input_tokens=10,
                output_tokens=2,
                total_tokens=12,
                cache_read_tokens=5,
                cache_write_tokens=1,
            ),
            UsageObservation(
                provider="fake",
                input_tokens=3,
                output_tokens=4,
                total_tokens=7,
                cache_read_tokens=0,
                cache_write_tokens=2,
            ),
        )
    )

    assert usage == SubAgentUsage(
        input_tokens=13,
        output_tokens=6,
        total_tokens=19,
        cache_read_tokens=5,
        cache_write_tokens=3,
    )

    partial = SubAgentUsage.aggregate(
        (
            UsageObservation(provider="fake", input_tokens=10, output_tokens=2),
            UsageObservation(provider="fake", input_tokens=None, output_tokens=4),
        )
    )

    assert partial.input_tokens is None
    assert partial.output_tokens == 6
    assert partial.total_tokens is None
    assert partial.cache_read_tokens is None
    assert partial.cache_write_tokens is None
