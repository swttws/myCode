from __future__ import annotations

from pathlib import Path

from mycode.skill import SkillExecutionScope
from mycode.skill.catalog import SkillCatalog
from mycode.skill.loader import SkillLoader
from mycode.skill.runtime import SkillRuntime
from tests.skill_test_support import write_skill


def make_runtime(tmp_path: Path) -> tuple[SkillRuntime, Path]:
    workspace = tmp_path / "workspace"
    home = tmp_path / "home"
    builtin = tmp_path / "builtins"
    project_root = workspace / ".mycode" / "skills"
    for root in (project_root, home / ".mycode" / "skills", builtin):
        root.mkdir(parents=True)
    loader = SkillLoader(workspace_root=workspace, home=home, builtin_root=builtin)
    catalog = SkillCatalog(
        loader=loader,
        tool_names=lambda: frozenset({"read_file", "search_code", "run_command"}),
        reserved_slash_names=frozenset({"help", "clear"}),
    )
    runtime = SkillRuntime(catalog)
    return runtime, project_root


def test_prompt_blocks_show_catalog_without_sop_before_activation(tmp_path):
    runtime, project_root = make_runtime(tmp_path)
    write_skill(project_root, "review", description="审查变更", body="完整 SOP")
    runtime.refresh()

    blocks = runtime.prompt_blocks()

    assert [block.id for block in blocks] == ["skill-catalog"]
    assert blocks[0].priority == -100
    assert "review: 审查变更" in blocks[0].content
    assert "完整 SOP" not in blocks[0].content


def test_activate_renders_arguments_and_persists_active_sop(tmp_path):
    runtime, project_root = make_runtime(tmp_path)
    write_skill(project_root, "review", description="审查", body="审查目标：{{arguments}}")
    runtime.refresh()

    activation = runtime.activate("review", "main 分支")
    blocks = runtime.prompt_blocks()

    assert activation.rendered_instruction == "审查目标：main 分支"
    assert [block.id for block in blocks] == ["active-skills", "skill-catalog"]
    assert blocks[0].priority == -200
    assert "审查目标：main 分支" in blocks[0].content


def test_multiple_active_skills_are_sorted_and_clear_removes_runtime_state(tmp_path):
    runtime, project_root = make_runtime(tmp_path)
    write_skill(project_root, "zeta", description="Z", body="Z {{arguments}}")
    write_skill(project_root, "alpha", description="A", body="A {{arguments}}")
    runtime.refresh()

    runtime.activate("zeta", "2")
    runtime.activate("alpha", "1")
    active_content = runtime.prompt_blocks()[0].content

    assert active_content.index("## alpha") < active_content.index("## zeta")

    runtime.clear()

    assert [block.id for block in runtime.prompt_blocks()] == ["skill-catalog"]
    assert runtime.current_scope() is None
    assert runtime.current_run_context() is None


def test_refresh_rerenders_active_skill_with_original_arguments(tmp_path):
    runtime, project_root = make_runtime(tmp_path)
    package = write_skill(project_root, "review", description="审查", body="旧 {{arguments}}")
    runtime.refresh()
    runtime.activate("review", "参数")

    (package / "SKILL.md").write_text(
        "---\nname: review\ndescription: 审查\nallowed_tools:\n  - read_file\nmode: shared\n---\n新 {{arguments}}",
        encoding="utf-8",
    )
    runtime.refresh()

    assert "新 参数" in runtime.prompt_blocks()[0].content
    assert "旧 参数" not in runtime.prompt_blocks()[0].content


def test_execution_scope_sets_run_context_visible_tools_and_restores_parent(tmp_path):
    runtime, project_root = make_runtime(tmp_path)
    write_skill(project_root, "review", allowed_tools=("read_file",))
    runtime.refresh()
    scope = runtime.set_current_scope("review")

    assert scope == SkillExecutionScope("review", frozenset({"read_file"}))
    assert runtime.current_scope() == scope
    runtime.clear_current_scope()
    assert runtime.current_scope() is None

    with runtime.execution_scope(
        scope,
        history=(),
        framework_blocks=(),
        approval_provider=None,
        isolated_depth=0,
    ):
        assert runtime.current_scope() == scope
        assert runtime.visible_tool_names() == frozenset({"read_file", "load_skill"})
        assert runtime.allows_tool("read_file") is True
        assert runtime.allows_tool("load_skill") is True
        assert runtime.allows_tool("run_command") is False
        assert runtime.current_run_context().scope == scope

    assert runtime.current_scope() is None
    assert runtime.current_run_context() is None


def test_set_current_scope_rejects_unknown_skill(tmp_path):
    runtime, _ = make_runtime(tmp_path)
    runtime.refresh()

    try:
        runtime.set_current_scope("missing")
    except KeyError as exc:
        assert exc.args == ("missing",)
    else:
        raise AssertionError("missing skill should be rejected")
