from __future__ import annotations

import os
from pathlib import Path

import pytest

from mycode.skill import (
    MAX_FRONTMATTER_BYTES,
    MAX_RESOURCE_BYTES,
    MAX_RESOURCE_COUNT,
    MAX_SKILL_FILE_BYTES,
    SkillContextStrategy,
    SkillMode,
    SkillParseError,
    SkillResourceError,
    SkillSource,
)
from mycode.skill.loader import SkillLoader
from tests.skill_test_support import write_skill


def make_loader(tmp_path: Path) -> tuple[SkillLoader, Path, Path, Path]:
    workspace = tmp_path / "workspace"
    home = tmp_path / "home"
    builtin = tmp_path / "builtins"
    for root in (workspace / ".mycode" / "skills", home / ".mycode" / "skills", builtin):
        root.mkdir(parents=True)
    return (
        SkillLoader(workspace_root=workspace, home=home, builtin_root=builtin),
        workspace / ".mycode" / "skills",
        home / ".mycode" / "skills",
        builtin,
    )


def test_scan_discovers_only_first_level_skill_directories(tmp_path):
    loader, project_root, user_root, builtin_root = make_loader(tmp_path)
    write_skill(project_root, "project")
    write_skill(user_root, "user")
    write_skill(builtin_root, "builtin")
    (project_root / "loose.md").write_text("ignored", encoding="utf-8")
    (project_root / "missing").mkdir()

    result = loader.scan()

    assert [(candidate.source, candidate.package_root.name) for candidate in result.candidates] == [
        (SkillSource.BUILTIN, "builtin"),
        (SkillSource.USER, "user"),
        (SkillSource.PROJECT, "project"),
    ]
    assert [diagnostic.code for diagnostic in result.diagnostics] == ["missing_entry"]
    assert result.diagnostics[0].skill_name == "missing"


def test_scan_fingerprint_includes_entry_and_resources_in_stable_order(tmp_path):
    loader, project_root, _, _ = make_loader(tmp_path)
    package = write_skill(
        project_root,
        "inspect",
        resources={"templates/b.md": "B", "examples/a.md": "A"},
    )

    candidate = loader.scan().candidates[0]

    assert candidate.package_root == package
    assert [item[0] for item in candidate.fingerprint] == [
        "SKILL.md",
        "examples/a.md",
        "templates/b.md",
    ]
    assert all(size > 0 and mtime > 0 for _, size, mtime in candidate.fingerprint)


def test_scan_rejects_symlink_package_or_entry_when_supported(tmp_path):
    loader, project_root, _, _ = make_loader(tmp_path)
    target = write_skill(project_root, "target")
    symlink_dir = project_root / "linkdir"
    try:
        symlink_dir.symlink_to(target, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation is not available on this platform")

    result = loader.scan()

    assert "linkdir" not in [candidate.package_root.name for candidate in result.candidates]
    assert any(diagnostic.code == "symlink_package" for diagnostic in result.diagnostics)


@pytest.mark.parametrize(
    ("mode", "context", "expected_strategy", "expected_turns"),
    [
        ("shared", None, None, None),
        ("isolated", {"strategy": "none"}, SkillContextStrategy.NONE, 0),
        ("isolated", {"strategy": "recent", "turns": 3}, SkillContextStrategy.RECENT, 3),
        ("isolated", {"strategy": "summary"}, SkillContextStrategy.SUMMARY, 0),
    ],
)
def test_load_parses_valid_metadata_modes(tmp_path, mode, context, expected_strategy, expected_turns):
    loader, project_root, _, _ = make_loader(tmp_path)
    write_skill(project_root, "valid", mode=mode, context=context, model="gpt-test")
    candidate = loader.scan().candidates[0]

    definition = loader.load(candidate)

    assert definition.metadata.name == "valid"
    assert definition.metadata.mode == SkillMode(mode)
    assert definition.instruction == "执行 {{arguments}}。"
    assert definition.revision
    if expected_strategy is None:
        assert definition.metadata.context is None
    else:
        assert definition.metadata.context.strategy is expected_strategy
        assert definition.metadata.context.turns == expected_turns


@pytest.mark.parametrize(
    ("name", "text", "message"),
    [
        ("no_frontmatter", "name: bad\n---\nbody", "frontmatter"),
        ("missing_end", "---\nname: bad\nbody", "frontmatter"),
        ("not_mapping", "---\n- bad\n---\nbody", "mapping"),
        ("unknown_field", "---\nname: unknown_field\ndescription: x\nallowed_tools: []\nmode: shared\nextra: 1\n---\nbody", "unknown"),
        ("missing_field", "---\nname: missing_field\nallowed_tools: []\nmode: shared\n---\nbody", "missing"),
        ("bad_name", "---\nname: Bad\ndescription: x\nallowed_tools: []\nmode: shared\n---\nbody", "name"),
        ("wrong_dir", "---\nname: other\ndescription: x\nallowed_tools: []\nmode: shared\n---\nbody", "directory"),
        ("duplicate_tool", "---\nname: duplicate_tool\ndescription: x\nallowed_tools:\n  - read_file\n  - read_file\nmode: shared\n---\nbody", "duplicate"),
        ("empty_body", "---\nname: empty_body\ndescription: x\nallowed_tools: []\nmode: shared\n---\n   ", "body"),
        ("shared_context", "---\nname: shared_context\ndescription: x\nallowed_tools: []\nmode: shared\ncontext:\n  strategy: none\n---\nbody", "context"),
        ("isolated_no_context", "---\nname: isolated_no_context\ndescription: x\nallowed_tools: []\nmode: isolated\n---\nbody", "context"),
        ("recent_no_turns", "---\nname: recent_no_turns\ndescription: x\nallowed_tools: []\nmode: isolated\ncontext:\n  strategy: recent\n---\nbody", "turns"),
    ],
)
def test_load_rejects_invalid_frontmatter(tmp_path, name, text, message):
    loader, project_root, _, _ = make_loader(tmp_path)
    package = project_root / name
    package.mkdir()
    (package / "SKILL.md").write_text(text, encoding="utf-8")
    candidate = next(candidate for candidate in loader.scan().candidates if candidate.package_root.name == name)

    with pytest.raises(SkillParseError, match=message):
        loader.load(candidate)


def test_load_rejects_oversized_entry_and_frontmatter(tmp_path):
    loader, project_root, _, _ = make_loader(tmp_path)
    package = project_root / "large"
    package.mkdir()
    (package / "SKILL.md").write_text("x" * (MAX_SKILL_FILE_BYTES + 1), encoding="utf-8")
    candidate = next(candidate for candidate in loader.scan().candidates if candidate.package_root.name == "large")

    with pytest.raises(SkillParseError, match="too large"):
        loader.load(candidate)

    (package / "SKILL.md").write_text(
        "---\n" + ("x" * (MAX_FRONTMATTER_BYTES + 1)) + "\n---\nbody",
        encoding="utf-8",
    )
    candidate = next(candidate for candidate in loader.scan().candidates if candidate.package_root.name == "large")

    with pytest.raises(SkillParseError, match="frontmatter too large"):
        loader.load(candidate)


def test_load_preserves_markdown_body_and_lists_resources(tmp_path):
    loader, project_root, _, _ = make_loader(tmp_path)
    write_skill(
        project_root,
        "resources",
        body="\n# Title\n\nUse `code`.\n\n",
        resources={
            "templates/prompt.md": "模板",
            "examples/demo.txt": "示例",
            "scripts/run.ps1": "Write-Output ok",
        },
    )
    candidate = loader.scan().candidates[0]

    definition = loader.load(candidate)

    assert definition.instruction == "# Title\n\nUse `code`."
    assert definition.resources == (
        "examples/demo.txt",
        "scripts/run.ps1",
        "templates/prompt.md",
    )


def test_load_rejects_too_many_resources(tmp_path):
    loader, project_root, _, _ = make_loader(tmp_path)
    resources = {f"r/{index}.txt": str(index) for index in range(MAX_RESOURCE_COUNT + 1)}
    write_skill(project_root, "too_many", resources=resources)
    candidate = loader.scan().candidates[0]

    with pytest.raises(SkillParseError, match="too many resources"):
        loader.load(candidate)


def test_read_resource_allows_utf8_files_and_rejects_unsafe_paths(tmp_path):
    loader, project_root, _, _ = make_loader(tmp_path)
    package = write_skill(project_root, "safe", resources={"docs/info.md": "中文内容"})
    candidate = loader.scan().candidates[0]
    definition = loader.load(candidate)

    assert loader.read_resource(definition, "docs/info.md") == "中文内容"

    for unsafe in ("../SKILL.md", str(package / "docs" / "info.md"), "missing.md", "docs"):
        with pytest.raises(SkillResourceError):
            loader.read_resource(definition, unsafe)


def test_read_resource_rejects_symlink_escape_and_oversized_file(tmp_path):
    loader, project_root, _, _ = make_loader(tmp_path)
    write_skill(project_root, "safe", resources={"big.txt": "x", "link.txt": "placeholder"})
    outside = tmp_path / "outside.txt"
    outside.write_text("outside", encoding="utf-8")
    link = project_root / "safe" / "link.txt"
    link.unlink()
    try:
        link.symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation is not available on this platform")
    big = project_root / "safe" / "big.txt"
    big.write_text("x" * (MAX_RESOURCE_BYTES + 1), encoding="utf-8")
    candidate = loader.scan().candidates[0]
    definition = loader.load(candidate)

    with pytest.raises(SkillResourceError, match="symlink"):
        loader.read_resource(definition, "link.txt")
    with pytest.raises(SkillResourceError, match="too large"):
        loader.read_resource(definition, "big.txt")
