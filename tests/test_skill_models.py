from pathlib import Path

import pytest

from mycode.skill import (
    MAX_FRONTMATTER_BYTES,
    MAX_RESOURCE_BYTES,
    MAX_RESOURCE_COUNT,
    MAX_SKILL_FILE_BYTES,
    SOURCE_PRIORITY,
    SkillActivation,
    SkillCandidate,
    SkillCatalogSnapshot,
    SkillContextPolicy,
    SkillContextStrategy,
    SkillDefinition,
    SkillDiagnostic,
    SkillExecutionResult,
    SkillExecutionScope,
    SkillMetadata,
    SkillMode,
    SkillParseError,
    SkillResourceError,
    SkillRunContext,
    SkillScanResult,
    SkillSource,
    SkillStartupError,
)


def test_skill_enum_values_and_source_priority_are_fixed():
    assert [source.value for source in SkillSource] == ["builtin", "user", "project"]
    assert [mode.value for mode in SkillMode] == ["shared", "isolated"]
    assert [strategy.value for strategy in SkillContextStrategy] == ["none", "recent", "summary"]
    assert SOURCE_PRIORITY == {
        SkillSource.BUILTIN: 100,
        SkillSource.USER: 200,
        SkillSource.PROJECT: 300,
    }


def test_skill_size_limits_are_bounded():
    assert MAX_SKILL_FILE_BYTES == 128 * 1024
    assert MAX_FRONTMATTER_BYTES == 16 * 1024
    assert MAX_RESOURCE_BYTES == 1024 * 1024
    assert MAX_RESOURCE_COUNT == 256


def test_recent_context_requires_positive_turns():
    assert SkillContextPolicy(SkillContextStrategy.RECENT, turns=3).turns == 3

    with pytest.raises(ValueError, match="recent context turns must be positive"):
        SkillContextPolicy(SkillContextStrategy.RECENT, turns=0)


def test_none_and_summary_context_forbid_turns():
    assert SkillContextPolicy(SkillContextStrategy.NONE).turns == 0
    assert SkillContextPolicy(SkillContextStrategy.SUMMARY).turns == 0

    with pytest.raises(ValueError, match="turns are only valid for recent context"):
        SkillContextPolicy(SkillContextStrategy.NONE, turns=1)


def test_shared_metadata_keeps_context_empty_and_isolated_requires_context():
    shared = SkillMetadata(
        name="review",
        description="审查变更。",
        allowed_tools=("read_file",),
        mode=SkillMode.SHARED,
        context=None,
        model="ignored-in-shared",
    )

    assert shared.context is None

    with pytest.raises(ValueError, match="shared skill context must be empty"):
        SkillMetadata(
            name="review",
            description="审查变更。",
            allowed_tools=(),
            mode=SkillMode.SHARED,
            context=SkillContextPolicy(SkillContextStrategy.NONE),
            model=None,
        )

    with pytest.raises(ValueError, match="isolated skill context is required"):
        SkillMetadata(
            name="test",
            description="运行测试。",
            allowed_tools=(),
            mode=SkillMode.ISOLATED,
            context=None,
            model=None,
        )


def test_metadata_validates_name_description_and_tools():
    with pytest.raises(ValueError, match="invalid skill name"):
        SkillMetadata("BadName", "描述", (), SkillMode.SHARED, None, None)

    with pytest.raises(ValueError, match="description must be non-empty"):
        SkillMetadata("ok", "", (), SkillMode.SHARED, None, None)

    with pytest.raises(ValueError, match="description must fit"):
        SkillMetadata("ok", "x" * 201, (), SkillMode.SHARED, None, None)

    with pytest.raises(ValueError, match="duplicate allowed tool"):
        SkillMetadata("ok", "描述", ("read_file", "read_file"), SkillMode.SHARED, None, None)


def test_core_dataclasses_are_immutable_and_locatable(tmp_path):
    metadata = SkillMetadata(
        name="investigate",
        description="调查问题。",
        allowed_tools=("read_file",),
        mode=SkillMode.ISOLATED,
        context=SkillContextPolicy(SkillContextStrategy.RECENT, turns=2),
        model="gpt-test",
    )
    definition = SkillDefinition(
        metadata=metadata,
        instruction="调查 {{arguments}}。",
        source=SkillSource.PROJECT,
        entry_path=tmp_path / "investigate" / "SKILL.md",
        package_root=tmp_path / "investigate",
        resources=("examples/demo.md",),
        revision="abc123",
    )

    assert definition.metadata.name == "investigate"
    with pytest.raises(Exception):
        definition.revision = "changed"


def test_scan_snapshot_activation_scope_and_result_shapes(tmp_path):
    candidate = SkillCandidate(
        source=SkillSource.USER,
        package_root=tmp_path / "review",
        entry_path=tmp_path / "review" / "SKILL.md",
        fingerprint=(("SKILL.md", 12, 34),),
    )
    diagnostic = SkillDiagnostic(
        code="missing_entry",
        source=SkillSource.USER,
        path=str(tmp_path / "bad"),
        message="缺少 SKILL.md",
        skill_name="bad",
    )
    scan = SkillScanResult(candidates=(candidate,), diagnostics=(diagnostic,))
    snapshot = SkillCatalogSnapshot(definitions=(), diagnostics=scan.diagnostics, generation=1)
    activation = SkillActivation(
        name="review",
        arguments="main",
        rendered_instruction="审查 main",
        revision="rev",
    )
    scope = SkillExecutionScope("review", frozenset({"read_file"}))
    run_context = SkillRunContext(
        history=(),
        framework_blocks=(),
        approval_provider=None,
        scope=scope,
        isolated_depth=0,
    )
    result = SkillExecutionResult(ok=True, summary="完成")

    assert scan.candidates == (candidate,)
    assert snapshot.generation == 1
    assert activation.rendered_instruction == "审查 main"
    assert run_context.scope == scope
    assert result.error_code is None


def test_skill_error_hierarchy_is_stable():
    assert issubclass(SkillParseError, RuntimeError)
    assert issubclass(SkillStartupError, RuntimeError)
    assert issubclass(SkillResourceError, RuntimeError)
