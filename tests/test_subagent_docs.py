from importlib.resources import as_file, files
from pathlib import Path

from mycode.subagent.loader import AgentRoleLoader
from mycode.subagent.models import AgentModelTier, AgentPermissionMode


KNOWN_TOOLS = {"read_file", "find_files", "search_code", "edit_file", "Agent"}
DEFAULT_READ_ONLY_TOOLS = ("read_file", "find_files", "search_code")


def load_builtin_definitions(tmp_path):
    builtin_resource = files("mycode.subagent") / "builtins"
    with as_file(builtin_resource) as builtin_dir:
        loader = AgentRoleLoader(
            project_root=tmp_path / "project",
            home=tmp_path / "home",
            builtin_dir=builtin_dir,
            known_tool_names=KNOWN_TOOLS,
        )
        candidates = loader.load()
    definitions = {
        candidate.definition.metadata.name: candidate.definition
        for candidate in candidates
        if candidate.definition is not None
    }
    diagnostics = [
        diagnostic
        for candidate in candidates
        for diagnostic in candidate.diagnostics
    ]
    return definitions, diagnostics


def test_builtin_subagent_roles_are_packaged_and_loadable(tmp_path):
    definitions, diagnostics = load_builtin_definitions(tmp_path)

    assert diagnostics == []
    assert set(definitions) == {"general", "explore", "review"}
    for definition in definitions.values():
        assert any("\u4e00" <= char <= "\u9fff" for char in definition.instruction)
        assert "TODO" not in definition.instruction
        assert "TBD" not in definition.instruction
        assert "{{" not in definition.instruction


def test_builtin_general_role_metadata_matches_stage_12_design(tmp_path):
    definitions, _ = load_builtin_definitions(tmp_path)

    general = definitions["general"].metadata

    assert general.allowed_tools == ("*",)
    assert general.denied_tools == ("Agent",)
    assert general.model is AgentModelTier.INHERIT
    assert general.permission_mode is AgentPermissionMode.INHERIT
    assert general.max_rounds == 8


def test_builtin_explore_and_review_are_strict_read_only_roles(tmp_path):
    definitions, _ = load_builtin_definitions(tmp_path)

    for role_name in ("explore", "review"):
        metadata = definitions[role_name].metadata
        assert metadata.allowed_tools == DEFAULT_READ_ONLY_TOOLS
        assert metadata.denied_tools == ("Agent",)
        assert metadata.model is AgentModelTier.INHERIT
        assert metadata.permission_mode is AgentPermissionMode.STRICT
        assert metadata.max_rounds == 8


def test_pyproject_packages_builtin_subagent_markdown_files():
    pyproject = Path("pyproject.toml").read_text(encoding="utf-8")

    assert '"mycode.subagent" = ["builtins/*.md"]' in pyproject
