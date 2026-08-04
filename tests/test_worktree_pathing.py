from pathlib import Path

import pytest

from mycode.worktree.models import WorktreeError
from mycode.worktree.pathing import WorktreePathPolicy


def test_validate_relative_name_accepts_safe_ascii_boundaries(tmp_path: Path):
    policy = WorktreePathPolicy(repository_root=tmp_path)
    exact_segment = "a" * 64
    exact_total = "/".join(("a" * 64, "b" * 64, "c" * 64, "d" * 5))

    assert policy.validate_relative_name("a") == "a"
    assert policy.validate_relative_name(exact_segment) == exact_segment
    assert policy.validate_relative_name(exact_total) == exact_total
    assert policy.validate_relative_name("role.name/task_01-2") == "role.name/task_01-2"


@pytest.mark.parametrize(
    "relative_name",
    [
        "",
        "/",
        "role/",
        "/task",
        "role//task",
        ".",
        "..",
        "role/.",
        "role/..",
        ".role",
        "role.",
        "-role",
        "role-",
        "_role",
        "role_",
        "a" * 65,
        "/".join(("a" * 64, "b" * 64, "c" * 64, "d" * 6)),
        r"role\task",
        "/absolute",
        "C:/absolute",
        "role\nname",
        "角色",
    ],
)
def test_validate_relative_name_rejects_unsafe_inputs(tmp_path: Path, relative_name: str):
    policy = WorktreePathPolicy(repository_root=tmp_path)

    with pytest.raises(WorktreeError, match="名称"):
        policy.validate_relative_name(relative_name)


@pytest.mark.parametrize(
    "reserved_name",
    [
        "CON",
        "con",
        "CON.txt",
        "prn",
        "AUX.log",
        "nul",
        "COM1",
        "com9.txt",
        "LPT1",
        "lpt9.md",
    ],
)
def test_validate_relative_name_rejects_windows_reserved_names(
    tmp_path: Path,
    reserved_name: str,
):
    policy = WorktreePathPolicy(repository_root=tmp_path)

    with pytest.raises(WorktreeError, match="保留"):
        policy.validate_relative_name(f"general/{reserved_name}")


def test_validate_branch_name_accepts_only_controlled_safe_prefix(tmp_path: Path):
    policy = WorktreePathPolicy(repository_root=tmp_path)

    assert (
        policy.validate_branch_name("mycode/worktree/general/task-000001")
        == "mycode/worktree/general/task-000001"
    )

    for branch_name in (
        "",
        "main",
        "mycode/worktree",
        "mycode/worktree/",
        "mycode/other/general/task-000001",
        "mycode/worktree/general/..",
        "mycode/worktree/general/CON",
        "mycode/worktree/general/task.lock",
        "mycode/worktree/general/task~1",
    ):
        with pytest.raises(WorktreeError, match="分支"):
            policy.validate_branch_name(branch_name)


def test_path_policy_resolves_controlled_roots_targets_and_metadata(tmp_path: Path):
    repo_root = tmp_path / "repo"
    worktrees_root = repo_root / ".worktrees"
    worktrees_root.mkdir(parents=True)
    policy = WorktreePathPolicy(repository_root=repo_root)

    assert policy.validate_root(repo_root) == worktrees_root.resolve()
    assert (
        policy.resolve_target("general/task-000001")
        == (worktrees_root / "general" / "task-000001").resolve()
    )
    assert (
        policy.resolve_metadata_path("general/task-000001")
        == (worktrees_root / ".metadata" / "general" / "task-000001.json").resolve()
    )


def test_target_boundary_rejects_paths_outside_worktrees_root(tmp_path: Path):
    repo_root = tmp_path / "repo"
    worktrees_root = repo_root / ".worktrees"
    worktrees_root.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    policy = WorktreePathPolicy(repository_root=repo_root)

    with pytest.raises(WorktreeError, match="边界"):
        policy.assert_target_boundary(outside / "task")

    link = worktrees_root / "linked-outside"
    _symlink_or_skip(outside, link)

    with pytest.raises(WorktreeError, match="边界"):
        policy.assert_target_boundary(link / "task")


def test_rule_source_and_target_stay_inside_expected_roots(tmp_path: Path):
    repo_root = tmp_path / "repo"
    worktrees_root = repo_root / ".worktrees"
    source_dir = repo_root / "config"
    source_dir.mkdir(parents=True)
    worktrees_root.mkdir(parents=True)
    (source_dir / "settings.toml").write_text("ok = true\n", encoding="utf-8")
    workspace_root = worktrees_root / "general" / "task-000001"
    workspace_root.mkdir(parents=True)
    policy = WorktreePathPolicy(repository_root=repo_root)

    assert (
        policy.resolve_rule_source("config/settings.toml")
        == (source_dir / "settings.toml").resolve()
    )
    assert (
        policy.resolve_rule_target(workspace_root, ".env")
        == (workspace_root / ".env").resolve()
    )

    with pytest.raises(WorktreeError, match="边界"):
        policy.resolve_rule_source("../outside.txt")
    with pytest.raises(WorktreeError, match="边界"):
        policy.resolve_rule_target(workspace_root, "../outside.txt")

    outside = tmp_path / "outside"
    outside.mkdir()
    source_link = repo_root / "linked-source"
    _symlink_or_skip(outside, source_link)

    with pytest.raises(WorktreeError, match="边界"):
        policy.resolve_rule_source("linked-source/file.txt")

    target_link = workspace_root / "linked-target"
    _symlink_or_skip(outside, target_link)

    with pytest.raises(WorktreeError, match="边界"):
        policy.resolve_rule_target(workspace_root, "linked-target/file.txt")


def _symlink_or_skip(source: Path, link: Path) -> None:
    try:
        link.symlink_to(source, target_is_directory=source.is_dir())
    except OSError as exc:
        pytest.skip(f"platform does not allow symlinks in this test: {exc}")
