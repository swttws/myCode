from __future__ import annotations

from pathlib import Path

import pytest

from mycode.workspace import WorkspaceTaskIdentity
from mycode.worktree.initializer import WorktreeInitializer
from mycode.worktree.models import (
    InitializationResult,
    WorktreeConfig,
    WorktreeError,
    WorktreeInitRule,
    WorktreeRuleType,
)
from mycode.worktree.pathing import WorktreePathPolicy


def test_copy_rules_copy_files_and_directories_in_declared_order(tmp_path: Path):
    repo_root, workspace_root = _repo_and_workspace(tmp_path)
    (repo_root / "config").mkdir()
    (repo_root / "config" / "app.toml").write_text("debug = true\n", encoding="utf-8")
    (repo_root / "templates" / "nested").mkdir(parents=True)
    (repo_root / "templates" / "nested" / "prompt.md").write_text("hello\n", encoding="utf-8")
    initializer = _initializer(repo_root)

    result = initializer.initialize(
        _identity(),
        workspace_root,
        WorktreeConfig(
            digest="abc123",
            rules=(
                _rule(WorktreeRuleType.COPY, "config/app.toml", "config/app.toml"),
                _rule(WorktreeRuleType.COPY, "templates", "templates"),
            ),
        ),
    )

    assert result == InitializationResult(
        completed_rules=("0:copy:config/app.toml", "1:copy:templates"),
        hooks_path=None,
    )
    assert (workspace_root / "config" / "app.toml").read_text(encoding="utf-8") == "debug = true\n"
    assert (workspace_root / "templates" / "nested" / "prompt.md").read_text(encoding="utf-8") == "hello\n"


def test_copy_rule_fails_when_target_exists_without_overwriting(tmp_path: Path):
    repo_root, workspace_root = _repo_and_workspace(tmp_path)
    (repo_root / "config").mkdir()
    (repo_root / "config" / "app.toml").write_text("new\n", encoding="utf-8")
    (workspace_root / "config").mkdir()
    target = workspace_root / "config" / "app.toml"
    target.write_text("old\n", encoding="utf-8")

    with pytest.raises(WorktreeError, match="存在"):
        _initializer(repo_root).initialize(
            _identity(),
            workspace_root,
            WorktreeConfig(
                digest="abc123",
                rules=(_rule(WorktreeRuleType.COPY, "config/app.toml", "config/app.toml"),),
            ),
        )

    assert target.read_text(encoding="utf-8") == "old\n"


def test_copy_rule_fails_when_source_is_missing(tmp_path: Path):
    repo_root, workspace_root = _repo_and_workspace(tmp_path)

    with pytest.raises(WorktreeError, match="source|来源"):
        _initializer(repo_root).initialize(
            _identity(),
            workspace_root,
            WorktreeConfig(
                digest="abc123",
                rules=(_rule(WorktreeRuleType.COPY, "missing.txt", "missing.txt"),),
            ),
        )


def test_ignored_copy_validates_source_and_target_before_copying(tmp_path: Path):
    repo_root, workspace_root = _repo_and_workspace(tmp_path)
    (repo_root / ".cache").mkdir()
    (repo_root / ".cache" / "seed.db").write_text("seed\n", encoding="utf-8")
    git = RecordingIgnoreGateway()
    initializer = _initializer(repo_root, git=git)

    initializer.initialize(
        _identity(),
        workspace_root,
        WorktreeConfig(
            digest="abc123",
            rules=(
                _rule(
                    WorktreeRuleType.IGNORED_COPY,
                    ".cache/seed.db",
                    ".cache/seed.db",
                ),
            ),
        ),
    )

    assert git.calls == [repo_root / ".cache" / "seed.db", workspace_root / ".cache" / "seed.db"]
    assert (workspace_root / ".cache" / "seed.db").read_text(encoding="utf-8") == "seed\n"


@pytest.mark.parametrize("failing_call", [0, 1])
def test_ignored_copy_failure_stops_before_writing(tmp_path: Path, failing_call: int):
    repo_root, workspace_root = _repo_and_workspace(tmp_path)
    (repo_root / ".cache").mkdir()
    (repo_root / ".cache" / "seed.db").write_text("seed\n", encoding="utf-8")
    git = RecordingIgnoreGateway(failing_call=failing_call)

    with pytest.raises(WorktreeError, match="ignored_copy|忽略|Git"):
        _initializer(repo_root, git=git).initialize(
            _identity(),
            workspace_root,
            WorktreeConfig(
                digest="abc123",
                rules=(
                    _rule(
                        WorktreeRuleType.IGNORED_COPY,
                        ".cache/seed.db",
                        ".cache/seed.db",
                    ),
                ),
            ),
        )

    assert not (workspace_root / ".cache" / "seed.db").exists()


def test_symlink_rule_creates_link_to_declared_source(tmp_path: Path):
    _require_symlink_support(tmp_path)
    repo_root, workspace_root = _repo_and_workspace(tmp_path)
    (repo_root / "vendor").mkdir()

    result = _initializer(repo_root).initialize(
        _identity(),
        workspace_root,
        WorktreeConfig(
            digest="abc123",
            rules=(_rule(WorktreeRuleType.SYMLINK, "vendor", "vendor"),),
        ),
    )

    assert result.completed_rules == ("0:symlink:vendor",)
    assert (workspace_root / "vendor").is_symlink()
    assert (workspace_root / "vendor").resolve() == (repo_root / "vendor").resolve()


def test_symlink_rule_fails_when_target_exists(tmp_path: Path):
    repo_root, workspace_root = _repo_and_workspace(tmp_path)
    (repo_root / "vendor").mkdir()
    (workspace_root / "vendor").write_text("existing\n", encoding="utf-8")

    with pytest.raises(WorktreeError, match="存在"):
        _initializer(repo_root).initialize(
            _identity(),
            workspace_root,
            WorktreeConfig(
                digest="abc123",
                rules=(_rule(WorktreeRuleType.SYMLINK, "vendor", "vendor"),),
            ),
        )

    assert (workspace_root / "vendor").read_text(encoding="utf-8") == "existing\n"


def test_symlink_rule_reports_platform_failure_without_copying(
    monkeypatch,
    tmp_path: Path,
):
    repo_root, workspace_root = _repo_and_workspace(tmp_path)
    (repo_root / "vendor").mkdir()

    def reject_symlink(self, target, target_is_directory=False):
        raise OSError("symlink denied")

    monkeypatch.setattr(Path, "symlink_to", reject_symlink)

    with pytest.raises(WorktreeError, match="symlink|符号链接"):
        _initializer(repo_root).initialize(
            _identity(),
            workspace_root,
            WorktreeConfig(
                digest="abc123",
                rules=(_rule(WorktreeRuleType.SYMLINK, "vendor", "vendor"),),
            ),
        )

    assert not (workspace_root / "vendor").exists()


def test_hooks_rule_copies_directory_and_returns_hooks_path(tmp_path: Path):
    repo_root, workspace_root = _repo_and_workspace(tmp_path)
    (repo_root / "hooks").mkdir()
    (repo_root / "hooks" / "pre-commit").write_text("echo ok\n", encoding="utf-8")

    result = _initializer(repo_root).initialize(
        _identity(),
        workspace_root,
        WorktreeConfig(
            digest="abc123",
            rules=(_rule(WorktreeRuleType.HOOKS, "hooks", ".git-hooks"),),
        ),
    )

    assert result == InitializationResult(
        completed_rules=("0:hooks:.git-hooks",),
        hooks_path=workspace_root / ".git-hooks",
    )
    assert (workspace_root / ".git-hooks" / "pre-commit").read_text(encoding="utf-8") == "echo ok\n"


def test_hooks_rule_fails_when_source_becomes_file(tmp_path: Path):
    repo_root, workspace_root = _repo_and_workspace(tmp_path)
    (repo_root / "hooks").write_text("not a directory\n", encoding="utf-8")

    with pytest.raises(WorktreeError, match="hooks|目录"):
        _initializer(repo_root).initialize(
            _identity(),
            workspace_root,
            WorktreeConfig(
                digest="abc123",
                rules=(_rule(WorktreeRuleType.HOOKS, "hooks", ".git-hooks"),),
            ),
        )


def test_mixed_rules_return_completed_order_and_stop_on_failure(tmp_path: Path):
    repo_root, workspace_root = _repo_and_workspace(tmp_path)
    (repo_root / "config").mkdir()
    (repo_root / "config" / "app.toml").write_text("ok\n", encoding="utf-8")
    (repo_root / "hooks").mkdir()
    (repo_root / "hooks" / "pre-commit").write_text("hook\n", encoding="utf-8")
    (repo_root / ".cache").mkdir()
    (repo_root / ".cache" / "seed.db").write_text("seed\n", encoding="utf-8")

    result = _initializer(repo_root).initialize(
        _identity(),
        workspace_root,
        WorktreeConfig(
            digest="abc123",
            rules=(
                _rule(WorktreeRuleType.COPY, "config/app.toml", "config/app.toml"),
                _rule(WorktreeRuleType.HOOKS, "hooks", ".git-hooks"),
                _rule(WorktreeRuleType.IGNORED_COPY, ".cache/seed.db", ".cache/seed.db"),
            ),
        ),
    )

    assert result == InitializationResult(
        completed_rules=(
            "0:copy:config/app.toml",
            "1:hooks:.git-hooks",
            "2:ignored_copy:.cache/seed.db",
        ),
        hooks_path=workspace_root / ".git-hooks",
    )

    with pytest.raises(WorktreeError, match="1:copy"):
        _initializer(repo_root).initialize(
            _identity(),
            repo_root / ".worktrees" / "general" / "task-000002",
            WorktreeConfig(
                digest="abc123",
                rules=(
                    _rule(WorktreeRuleType.COPY, "config/app.toml", "first.toml"),
                    _rule(WorktreeRuleType.COPY, "missing.toml", "missing.toml"),
                    _rule(WorktreeRuleType.COPY, "config/app.toml", "never.toml"),
                ),
            ),
        )

    other_workspace = repo_root / ".worktrees" / "general" / "task-000002"
    assert (other_workspace / "first.toml").exists()
    assert not (other_workspace / "never.toml").exists()


class RecordingIgnoreGateway:
    def __init__(self, *, failing_call: int | None = None) -> None:
        self.failing_call = failing_call
        self.calls: list[Path] = []

    def validate_ignored_root(self, path: Path) -> None:
        if self.failing_call == len(self.calls):
            raise WorktreeError(
                code="worktree_root_not_ignored",
                phase="git",
                message="Git 忽略检查失败",
                path=path,
            )
        self.calls.append(path)


def _repo_and_workspace(tmp_path: Path) -> tuple[Path, Path]:
    repo_root = tmp_path / "repo"
    workspace_root = repo_root / ".worktrees" / "general" / "task-000001"
    workspace_root.mkdir(parents=True)
    return repo_root, workspace_root


def _initializer(repo_root: Path, *, git=None) -> WorktreeInitializer:
    return WorktreeInitializer(
        path_policy=WorktreePathPolicy(repository_root=repo_root),
        git=git or RecordingIgnoreGateway(),
    )


def _identity() -> WorkspaceTaskIdentity:
    return WorkspaceTaskIdentity(
        repository_id="repo-123",
        task_id="task-000001",
        role_name="general",
        task_token="task-000001",
        relative_name="general/task-000001",
        branch_name="mycode/worktree/general/task-000001",
        base_commit="a" * 40,
    )


def _rule(
    rule_type: WorktreeRuleType,
    source: str,
    target: str,
) -> WorktreeInitRule:
    return WorktreeInitRule(type=rule_type, source=source, target=target)


def _require_symlink_support(tmp_path: Path) -> None:
    source = tmp_path / "symlink-source"
    target = tmp_path / "symlink-target"
    source.mkdir()
    try:
        target.symlink_to(source, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"platform does not allow symlinks in this test: {exc}")
    finally:
        if target.exists() or target.is_symlink():
            target.unlink()
