from __future__ import annotations

import importlib.resources as resources

from mycode.skill.loader import SkillLoader


def test_builtin_skill_files_are_packaged_and_parse_with_loader(tmp_path):
    builtin_root = resources.files("mycode.skill").joinpath("builtins")
    loader = SkillLoader(workspace_root=tmp_path / "workspace", home=tmp_path / "home", builtin_root=builtin_root)

    definitions = {definition.metadata.name: definition for definition in (loader.load(candidate) for candidate in loader.scan().candidates)}

    assert set(definitions) == {"commit", "review", "test"}
    assert definitions["commit"].metadata.mode.value == "shared"
    assert definitions["review"].metadata.mode.value == "shared"
    assert definitions["test"].metadata.mode.value == "isolated"
    assert definitions["test"].metadata.context.strategy.value == "recent"
    assert definitions["test"].metadata.context.turns == 3
    for definition in definitions.values():
        assert "{{arguments}}" in definition.instruction
        assert any("\u4e00" <= character <= "\u9fff" for character in definition.metadata.description)


def test_pyproject_includes_builtin_skill_package_data():
    pyproject = resources.files("mycode").joinpath("../../pyproject.toml")
    text = pyproject.read_text(encoding="utf-8")

    assert '"mycode.skill" = ["builtins/*/SKILL.md"]' in text


def test_readme_documents_skill_system_contract():
    readme = resources.files("mycode").joinpath("../../README.md")
    text = readme.read_text(encoding="utf-8")

    required_fragments = [
        "## Stage 10 Skill 系统",
        ".mycode/skills",
        "~/.mycode/skills",
        "SKILL.md",
        "allowed_tools",
        "mode: shared",
        "mode: isolated",
        "load_skill",
        "{{arguments}}",
        "none",
        "recent",
        "summary",
        "commit",
        "review",
        "test",
        "热更新",
        "不做 Skill 市场",
    ]
    for fragment in required_fragments:
        assert fragment in text
