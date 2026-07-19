import os
from pathlib import Path

import pytest

from mycode.permission.pathing import PathGuard, ToolPathError


def test_inspect_accepts_relative_and_inside_absolute_paths(tmp_path):
    workspace = tmp_path / "workspace"
    source = workspace / "src" / "main.py"
    source.parent.mkdir(parents=True)
    source.write_text("print('ok')", encoding="utf-8")
    guard = PathGuard(workspace)

    relative = guard.inspect("src/main.py")
    absolute = guard.inspect(str(source))

    assert relative.resolved == source.resolve()
    assert relative.relative == "src/main.py"
    assert relative.match_value == os.path.normcase("src/main.py").replace("\\", "/")
    assert absolute == relative
    assert guard.resolve("src/main.py") == source.resolve()


@pytest.mark.parametrize("value", ["../outside.txt", "src/../../outside.txt"])
def test_inspect_rejects_parent_escape(tmp_path, value):
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    with pytest.raises(ToolPathError, match="工作区"):
        PathGuard(workspace).inspect(value)


def test_inspect_rejects_absolute_path_outside_workspace(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside.txt"

    with pytest.raises(ToolPathError, match="工作区"):
        PathGuard(workspace).inspect(str(outside))


def test_inspect_accepts_nonexistent_target_when_existing_parent_is_inside(tmp_path):
    workspace = tmp_path / "workspace"
    (workspace / "new").mkdir(parents=True)
    guard = PathGuard(workspace)

    inspected = guard.inspect("new/nested/file.txt")

    assert inspected.resolved == workspace.resolve() / "new" / "nested" / "file.txt"
    assert inspected.relative == "new/nested/file.txt"


def test_inspect_rejects_symlink_escape(tmp_path):
    workspace = tmp_path / "workspace"
    outside = tmp_path / "outside"
    workspace.mkdir()
    outside.mkdir()
    link = workspace / "linked"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"当前平台不能创建符号链接: {exc}")

    with pytest.raises(ToolPathError, match="工作区"):
        PathGuard(workspace).inspect("linked/secret.txt")


def test_inspect_accepts_symlink_resolving_inside_workspace(tmp_path):
    workspace = tmp_path / "workspace"
    target = workspace / "real"
    target.mkdir(parents=True)
    link = workspace / "linked"
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"当前平台不能创建符号链接: {exc}")

    inspected = PathGuard(workspace).inspect("linked/file.txt")

    assert inspected.resolved == target.resolve() / "file.txt"
    assert inspected.relative == "real/file.txt"


@pytest.mark.skipif(os.name != "nt", reason="仅 Windows 需要盘符大小写与分隔符覆盖")
def test_windows_match_value_uses_platform_case_normalization(tmp_path):
    workspace = tmp_path / "workspace"
    target = workspace / "SRC" / "Main.PY"
    target.parent.mkdir(parents=True)
    target.write_text("", encoding="utf-8")

    inspected = PathGuard(workspace).inspect("SRC\\Main.PY")

    assert inspected.relative == "SRC/Main.PY"
    assert inspected.match_value == os.path.normcase("SRC/Main.PY").replace("\\", "/")


@pytest.mark.skipif(os.name != "nt", reason="UNC 是 Windows 路径语义")
def test_windows_unc_path_outside_workspace_is_rejected(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    with pytest.raises(ToolPathError):
        PathGuard(workspace).inspect(r"\\server\share\file.txt")
