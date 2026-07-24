import hashlib
from pathlib import Path

import pytest

from mycode.memory.paths import MemoryPaths


def _digest(path: Path) -> str:
    return hashlib.sha256(str(path.resolve()).encode("utf-8")).hexdigest()


def test_memory_paths_derive_expected_roots(tmp_path):
    workspace = tmp_path / "workspace"
    home = tmp_path / "home"
    workspace.mkdir()
    home.mkdir()

    paths = MemoryPaths(workspace_root=workspace, home=home)

    expected_digest = _digest(workspace)
    assert paths.project_digest == expected_digest
    assert paths.project_store_root == home / ".mycode" / "projects" / expected_digest
    assert paths.sessions_dir == home / ".mycode" / "projects" / expected_digest / "sessions"
    assert paths.project_memory_dir == home / ".mycode" / "projects" / expected_digest / "memory"
    assert paths.user_memory_dir == home / ".mycode" / "memory"


def test_memory_paths_ensure_directories_creates_expected_layout(tmp_path):
    workspace = tmp_path / "workspace"
    home = tmp_path / "home"
    workspace.mkdir()
    home.mkdir()

    paths = MemoryPaths(workspace_root=workspace, home=home)
    paths.ensure_directories()

    assert paths.project_store_root.is_dir()
    assert paths.sessions_dir.is_dir()
    assert paths.project_memory_dir.is_dir()
    assert paths.user_memory_dir.is_dir()
    assert (home / ".mycode").is_dir()
    assert not (home / ".mewcode").exists()
    assert not (workspace / ".mewcode").exists()


def test_validate_project_path_accepts_inside_workspace_paths(tmp_path):
    workspace = tmp_path / "workspace"
    home = tmp_path / "home"
    (workspace / "src").mkdir(parents=True)
    home.mkdir()

    paths = MemoryPaths(workspace_root=workspace, home=home)

    relative = paths.validate_project_path("src/main.py")
    absolute = paths.validate_project_path(workspace / "src" / "main.py")
    nested_missing = paths.validate_project_path("src/nested/file.md")

    assert relative == workspace.resolve() / "src" / "main.py"
    assert absolute == relative
    assert nested_missing == workspace.resolve() / "src" / "nested" / "file.md"


def test_validate_user_mycode_path_accepts_inside_home_paths(tmp_path):
    workspace = tmp_path / "workspace"
    home = tmp_path / "home"
    workspace.mkdir()
    (home / ".mycode" / "memory").mkdir(parents=True)

    paths = MemoryPaths(workspace_root=workspace, home=home)

    relative = paths.validate_user_mycode_path("memory/note.md")
    absolute = paths.validate_user_mycode_path(home / ".mycode" / "memory" / "note.md")

    assert relative == home.resolve() / ".mycode" / "memory" / "note.md"
    assert absolute == relative


@pytest.mark.parametrize("value", ["../outside.txt", "src/../../outside.txt"])
def test_validate_project_path_rejects_escape_attempts(tmp_path, value):
    workspace = tmp_path / "workspace"
    home = tmp_path / "home"
    workspace.mkdir()
    home.mkdir()

    paths = MemoryPaths(workspace_root=workspace, home=home)

    with pytest.raises(ValueError):
        paths.validate_project_path(value)


def test_validate_project_path_rejects_absolute_escape(tmp_path):
    workspace = tmp_path / "workspace"
    home = tmp_path / "home"
    outside = tmp_path / "outside.txt"
    workspace.mkdir()
    home.mkdir()

    paths = MemoryPaths(workspace_root=workspace, home=home)

    with pytest.raises(ValueError):
        paths.validate_project_path(outside)


def test_validate_user_mycode_path_rejects_escape_attempts(tmp_path):
    workspace = tmp_path / "workspace"
    home = tmp_path / "home"
    workspace.mkdir()
    home.mkdir()

    paths = MemoryPaths(workspace_root=workspace, home=home)

    with pytest.raises(ValueError):
        paths.validate_user_mycode_path("../outside.txt")


def test_validate_project_path_rejects_symlink_escape(tmp_path):
    workspace = tmp_path / "workspace"
    home = tmp_path / "home"
    outside = tmp_path / "outside"
    workspace.mkdir()
    home.mkdir()
    outside.mkdir()
    link = workspace / "linked"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"当前平台不能创建符号链接: {exc}")

    paths = MemoryPaths(workspace_root=workspace, home=home)

    with pytest.raises(ValueError):
        paths.validate_project_path("linked/secret.txt")


def test_validate_user_mycode_path_rejects_symlink_escape(tmp_path):
    workspace = tmp_path / "workspace"
    home = tmp_path / "home"
    outside = tmp_path / "outside"
    workspace.mkdir()
    home.mkdir()
    outside.mkdir()
    user_root = home / ".mycode"
    user_root.mkdir()
    link = user_root / "linked"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"当前平台不能创建符号链接: {exc}")

    paths = MemoryPaths(workspace_root=workspace, home=home)

    with pytest.raises(ValueError):
        paths.validate_user_mycode_path("linked/secret.txt")

