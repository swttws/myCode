from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from mycode.memory.instructions import InstructionLoader
from mycode.memory.models import InstructionLayer
from mycode.memory.paths import MemoryPaths


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _make_paths(tmp_path: Path) -> tuple[MemoryPaths, Path, Path]:
    workspace = tmp_path / "workspace"
    home = tmp_path / "home"
    workspace.mkdir()
    home.mkdir()
    return MemoryPaths(workspace_root=workspace, home=home), workspace, home


def test_instruction_loader_loads_three_layers_in_fixed_order_and_renders_deterministically(tmp_path):
    paths, workspace, home = _make_paths(tmp_path)
    (workspace / "mycode.md").write_text("root line", encoding="utf-8")
    (workspace / ".mycode").mkdir()
    (workspace / ".mycode" / "instructions.md").write_text("project line", encoding="utf-8")
    (home / ".mycode").mkdir()
    (home / ".mycode" / "instructions.md").write_text("user line", encoding="utf-8")

    loader = InstructionLoader(paths=paths)
    first = loader.load()
    second = loader.load()

    assert [block.layer for block in first.blocks] == [
        InstructionLayer.PROJECT_ROOT,
        InstructionLayer.PROJECT_DIRECTORY,
        InstructionLayer.USER,
    ]
    assert [block.priority for block in first.blocks] == [100, 200, 300]
    assert [block.path for block in first.blocks] == [
        str((workspace / "mycode.md").resolve()),
        str((workspace / ".mycode" / "instructions.md").resolve()),
        str((home / ".mycode" / "instructions.md").resolve()),
    ]
    assert [block.text for block in first.blocks] == ["root line", "project line", "user line"]
    assert [block.sha256 for block in first.blocks] == [
        _sha256("root line"),
        _sha256("project line"),
        _sha256("user line"),
    ]
    assert first.rendered_text == second.rendered_text
    assert first.rendered_text.index("## project_root") < first.rendered_text.index("## project_directory") < first.rendered_text.index("## user")
    assert "path:" in first.rendered_text
    assert "sha256:" in first.rendered_text
    assert "root line" in first.rendered_text
    assert "project line" in first.rendered_text
    assert "user line" in first.rendered_text
    assert first.diagnostics == ()


def test_instruction_loader_ignores_missing_instruction_files_without_diagnostics(tmp_path):
    paths, workspace, _home = _make_paths(tmp_path)
    (workspace / "mycode.md").write_text("root line", encoding="utf-8")

    result = InstructionLoader(paths=paths).load()

    assert [block.layer for block in result.blocks] == [InstructionLayer.PROJECT_ROOT]
    assert result.diagnostics == ()


def test_instruction_loader_expands_includes_and_keeps_other_lines(tmp_path):
    paths, workspace, home = _make_paths(tmp_path)
    docs_dir = workspace / "docs"
    docs_dir.mkdir()
    (docs_dir / "rules.md").write_text("included a\nincluded b", encoding="utf-8")
    (workspace / "mycode.md").write_text(
        "before\n@include docs/rules.md\nafter",
        encoding="utf-8",
    )
    (workspace / ".mycode").mkdir()
    (workspace / ".mycode" / "instructions.md").write_text("project line", encoding="utf-8")
    (home / ".mycode").mkdir()
    (home / ".mycode" / "instructions.md").write_text("user line", encoding="utf-8")

    result = InstructionLoader(paths=paths).load()

    assert result.blocks[0].text == "before\nincluded a\nincluded b\nafter"
    assert "included a" in result.rendered_text
    assert "included b" in result.rendered_text
    assert result.diagnostics == ()


def test_instruction_loader_reports_invalid_include_without_leaking_other_content(tmp_path):
    paths, workspace, home = _make_paths(tmp_path)
    (workspace / "mycode.md").write_text("before\n@include ../secret.md\nafter", encoding="utf-8")
    (workspace / ".mycode").mkdir()
    (workspace / ".mycode" / "instructions.md").write_text("project line", encoding="utf-8")
    (home / ".mycode").mkdir()
    (home / ".mycode" / "instructions.md").write_text("user line", encoding="utf-8")

    result = InstructionLoader(paths=paths).load()

    assert result.blocks[0].text == "before\nafter"
    assert len(result.diagnostics) == 1
    diagnostic = result.diagnostics[0]
    assert diagnostic.code
    assert diagnostic.path == str((workspace / "mycode.md").resolve())
    assert diagnostic.line == 2


def test_instruction_loader_rejects_include_cycles_and_depth_limits(tmp_path):
    paths, workspace, home = _make_paths(tmp_path)
    chain = workspace / "chain"
    chain.mkdir()
    (chain / "a.md").write_text("@include b.md", encoding="utf-8")
    (chain / "b.md").write_text("@include c.md", encoding="utf-8")
    (chain / "c.md").write_text("@include d.md", encoding="utf-8")
    (chain / "d.md").write_text("@include e.md", encoding="utf-8")
    (chain / "e.md").write_text("@include f.md", encoding="utf-8")
    (chain / "f.md").write_text("deep line", encoding="utf-8")
    (workspace / "mycode.md").write_text(
        "@include chain/a.md\n@include cycle/a.md",
        encoding="utf-8",
    )
    cycle_dir = workspace / "cycle"
    cycle_dir.mkdir()
    (cycle_dir / "a.md").write_text("@include b.md", encoding="utf-8")
    (cycle_dir / "b.md").write_text("@include a.md", encoding="utf-8")
    (workspace / ".mycode").mkdir()
    (workspace / ".mycode" / "instructions.md").write_text("project line", encoding="utf-8")
    (home / ".mycode").mkdir()
    (home / ".mycode" / "instructions.md").write_text("user line", encoding="utf-8")

    result = InstructionLoader(paths=paths).load()

    assert "deep line" not in result.blocks[0].text
    codes = {diagnostic.code for diagnostic in result.diagnostics}
    assert codes
    assert any(code.endswith("depth_exceeded") or code == "include_depth_exceeded" for code in codes)
    assert any(code.endswith("cycle") or code == "include_cycle" for code in codes)


def test_instruction_loader_rejects_symlink_escape_for_user_scope(tmp_path):
    paths, workspace, home = _make_paths(tmp_path)
    (workspace / "mycode.md").write_text("root line", encoding="utf-8")
    (workspace / ".mycode").mkdir()
    (workspace / ".mycode" / "instructions.md").write_text("project line", encoding="utf-8")
    user_dir = home / ".mycode"
    user_dir.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.md").write_text("secret", encoding="utf-8")
    link = user_dir / "linked"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"symlinks are unavailable on this platform: {exc}")
    (user_dir / "instructions.md").write_text("@include linked/secret.md", encoding="utf-8")

    result = InstructionLoader(paths=paths).load()

    assert any(diagnostic.code for diagnostic in result.diagnostics)
    assert result.blocks[-1].text == ""
