from __future__ import annotations

from pathlib import Path

import pytest

from mycode.skill import SkillSource, SkillStartupError
from mycode.skill.catalog import SkillCatalog
from mycode.skill.loader import SkillLoader
from tests.skill_test_support import write_skill


def make_catalog(
    tmp_path: Path,
    *,
    tool_names: frozenset[str] | None = None,
    reserved_slash_names: frozenset[str] | None = None,
) -> tuple[SkillCatalog, Path, Path, Path]:
    workspace = tmp_path / "workspace"
    home = tmp_path / "home"
    builtin = tmp_path / "builtins"
    project_root = workspace / ".mycode" / "skills"
    user_root = home / ".mycode" / "skills"
    for root in (project_root, user_root, builtin):
        root.mkdir(parents=True)
    loader = SkillLoader(workspace_root=workspace, home=home, builtin_root=builtin)
    catalog = SkillCatalog(
        loader=loader,
        tool_names=lambda: tool_names or frozenset({"read_file", "search_code", "run_command"}),
        reserved_slash_names=reserved_slash_names or frozenset({"help", "clear", "h"}),
    )
    return catalog, project_root, user_root, builtin


def test_initialize_uses_project_user_builtin_precedence_and_sorted_names(tmp_path):
    catalog, project_root, user_root, builtin_root = make_catalog(tmp_path)
    write_skill(builtin_root, "review", description="内置")
    write_skill(user_root, "review", description="用户")
    write_skill(project_root, "review", description="项目")
    write_skill(builtin_root, "commit", description="提交")

    snapshot = catalog.initialize()

    assert [(definition.metadata.name, definition.source) for definition in snapshot.definitions] == [
        ("commit", SkillSource.BUILTIN),
        ("review", SkillSource.PROJECT),
    ]
    assert catalog.get("review").metadata.description == "项目"
    assert snapshot.generation == 1


def test_initialize_falls_back_to_lower_priority_after_parse_error(tmp_path):
    catalog, project_root, user_root, _ = make_catalog(tmp_path)
    write_skill(user_root, "review", description="用户")
    package = project_root / "review"
    package.mkdir()
    (package / "SKILL.md").write_text("---\nname: other\n---\nbad", encoding="utf-8")

    snapshot = catalog.initialize()

    assert catalog.get("review").source is SkillSource.USER
    assert any(diagnostic.code == "parse_error" and diagnostic.skill_name == "review" for diagnostic in snapshot.diagnostics)


def test_initialize_fails_fast_for_unknown_tools_and_reserved_slash_names(tmp_path):
    catalog, project_root, _, _ = make_catalog(tmp_path, tool_names=frozenset({"read_file"}))
    write_skill(project_root, "badtools", allowed_tools=("read_file", "missing_tool"))

    with pytest.raises(SkillStartupError) as exc_info:
        catalog.initialize()

    message = str(exc_info.value)
    assert "badtools" in message
    assert "missing_tool" in message

    catalog, project_root, _, _ = make_catalog(tmp_path / "reserved", reserved_slash_names=frozenset({"review"}))
    write_skill(project_root, "review")

    with pytest.raises(SkillStartupError, match="review"):
        catalog.initialize()


def test_snapshot_get_and_read_resource_delegate_to_effective_definition(tmp_path):
    catalog, project_root, _, _ = make_catalog(tmp_path)
    write_skill(project_root, "docs", resources={"notes/info.md": "hello"})

    snapshot = catalog.initialize()

    assert catalog.snapshot() == snapshot
    assert catalog.get("docs").metadata.name == "docs"
    assert catalog.get("missing") is None
    assert catalog.read_resource("docs", "notes/info.md") == "hello"
    with pytest.raises(KeyError):
        catalog.read_resource("missing", "notes/info.md")


def test_refresh_add_modify_delete_and_generation_changes_only_for_effective_definitions(tmp_path):
    catalog, project_root, user_root, _ = make_catalog(tmp_path)
    write_skill(user_root, "review", description="用户")
    initial = catalog.initialize()

    same = catalog.refresh()
    assert same.generation == initial.generation

    write_skill(project_root, "review", description="项目")
    updated = catalog.refresh()
    assert updated.generation == initial.generation + 1
    assert catalog.get("review").source is SkillSource.PROJECT
    assert catalog.get("review").metadata.description == "项目"

    (project_root / "review" / "SKILL.md").unlink()
    fallback = catalog.refresh()
    assert fallback.generation == updated.generation + 1
    assert catalog.get("review").source is SkillSource.USER


def test_refresh_rejects_unknown_tool_update_and_keeps_last_valid_definition(tmp_path):
    catalog, project_root, _, _ = make_catalog(tmp_path, tool_names=frozenset({"read_file"}))
    write_skill(project_root, "review", allowed_tools=("read_file",), description="有效")
    initial = catalog.initialize()

    write_skill(project_root, "review", allowed_tools=("missing_tool",), description="坏版本")
    refreshed = catalog.refresh()

    assert refreshed.generation == initial.generation
    assert catalog.get("review").metadata.description == "有效"
    assert any(diagnostic.code == "unknown_tool" and diagnostic.skill_name == "review" for diagnostic in refreshed.diagnostics)


def test_refresh_omits_new_invalid_skill_without_blocking_other_updates(tmp_path):
    catalog, project_root, _, _ = make_catalog(tmp_path, tool_names=frozenset({"read_file"}))
    write_skill(project_root, "good", allowed_tools=("read_file",))
    initial = catalog.initialize()

    write_skill(project_root, "bad", allowed_tools=("missing_tool",))
    write_skill(project_root, "added", allowed_tools=("read_file",))
    refreshed = catalog.refresh()

    assert refreshed.generation == initial.generation + 1
    assert [definition.metadata.name for definition in refreshed.definitions] == ["added", "good"]
    assert catalog.get("bad") is None
