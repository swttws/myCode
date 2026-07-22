from dataclasses import FrozenInstanceError

import pytest

from mycode.compact.models import (
    ArchivedArtifact,
    ArtifactSlice,
    CompactAction,
    CompactConfig,
    CompactError,
    CompactFailureCode,
    CompactPolicy,
    CompactReport,
    CompactStatus,
    HeavyCompactResult,
    LightCompactResult,
    RequestSnapshot,
    TokenEstimate,
)


def test_compact_config_and_policy_have_approved_defaults_and_are_frozen():
    config = CompactConfig(context_window_tokens=128_000)
    policy = CompactPolicy()

    assert config.tool_result_threshold_tokens == 8_000
    assert config.tool_batch_threshold_tokens == 12_000
    assert policy.preview_tokens == 2_000
    assert policy.auto_reserve_tokens == 13_000
    assert policy.manual_reserve_tokens == 3_000
    assert policy.keep_recent_tokens == 10_000
    assert policy.min_recent_messages == 5
    assert policy.max_attempts == 3
    assert policy.stale_after_seconds == 86_400

    with pytest.raises(FrozenInstanceError):
        config.context_window_tokens = 1


def test_compact_value_models_retain_their_context_fields():
    snapshot = RequestSnapshot(ascii_chars=12, non_ascii_chars=3, fingerprint="abc123")
    estimate = TokenEstimate(
        tokens=9,
        source="usage_delta",
        anchor_input_tokens=4,
        delta_tokens=5,
    )
    artifact = ArchivedArtifact(
        path=".mycode/archives/tool.txt",
        kind="tool_result",
        original_chars=42,
        estimated_tokens=11,
        sha256="digest",
    )
    slice_ = ArtifactSlice(path=artifact.path, text="payload", next_offset=7, eof=False)

    assert snapshot.fingerprint == "abc123"
    assert estimate.source == "usage_delta"
    assert estimate.anchor_input_tokens == 4
    assert artifact.kind == "tool_result"
    assert slice_.next_offset == 7
    assert not slice_.eof


def test_compact_enums_expose_the_approved_wire_values():
    assert {action.value for action in CompactAction} == {
        "none",
        "light",
        "heavy",
        "force",
        "emergency",
    }
    assert {status.value for status in CompactStatus} == {
        "safe",
        "compacted",
        "no_op",
        "failed",
    }
    assert {code.value for code in CompactFailureCode} == {
        "llm_error",
        "tool_attempt",
        "invalid_format",
        "summary_too_large",
        "budget_not_recovered",
        "archive_error",
        "timeout",
        "cancelled",
    }


def test_compact_report_results_and_error_preserve_outcome_details():
    report = CompactReport(
        status=CompactStatus.FAILED,
        actions=(CompactAction.HEAVY,),
        before_tokens=20_000,
        after_tokens=8_000,
        archived_count=2,
        attempts=3,
        circuit_open=True,
    )
    light = LightCompactResult(history=("message",), artifacts=(), changed=True)
    heavy = HeavyCompactResult(
        history=("summary",),
        artifacts=(),
        actions=(CompactAction.HEAVY,),
        summary="summary",
    )

    assert report.failure_code is None
    assert report.message_zh == ""
    assert light.changed
    assert heavy.summary == "summary"

    error = CompactError(report)

    assert isinstance(error, RuntimeError)
    assert error.report is report
