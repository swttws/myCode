from __future__ import annotations

import subprocess
from hashlib import sha256
from pathlib import Path

import pytest

from mycode.workspace import WorkspaceTaskIdentity
from mycode.worktree.git import GitWorktreeGateway
from mycode.worktree.models import GitStatus, GitWorktreeEntry, WorktreeConfig, WorktreeError
from tests.worktree_helpers import (
    FakeGitRunner,
    completed_git_process,
    create_git_repository_with_bare_remote,
    run_git,
)


def test_runner_receives_structured_argv_explicit_cwd_shell_false_and_timeout(
    tmp_path: Path,
):
    runner = FakeGitRunner([completed_git_process(stdout=b"ok\n")])
    gateway = GitWorktreeGateway(
        config=WorktreeConfig(digest="abc123", git_timeout_seconds=7.5),
        env={"BASE": "1"},
        runner=runner,
    )

    result = gateway._run(("status", "--porcelain=v2", "-z"), cwd=tmp_path)

    assert result.stdout == b"ok\n"
    assert len(runner.calls) == 1
    call = runner.calls[0]
    assert call.command == ("git", "status", "--porcelain=v2", "-z")
    assert call.cwd == tmp_path
    assert call.shell is False
    assert call.capture_output is True
    assert call.timeout == 7.5
    assert call.check is False
    assert call.text is False


def test_runner_rejects_shell_string_arguments(tmp_path: Path):
    gateway = GitWorktreeGateway(
        config=WorktreeConfig(digest="abc123"),
        env={},
        runner=FakeGitRunner(),
    )

    with pytest.raises(WorktreeError, match="参数"):
        gateway._run("status --short", cwd=tmp_path)  # type: ignore[arg-type]


def test_output_is_truncated_to_sixty_four_kib_per_stream(tmp_path: Path):
    stdout = b"o" * (64 * 1024 + 17)
    stderr = b"e" * (64 * 1024 + 19)
    runner = FakeGitRunner([completed_git_process(stdout=stdout, stderr=stderr)])
    gateway = GitWorktreeGateway(
        config=WorktreeConfig(digest="abc123"),
        env={},
        runner=runner,
    )

    result = gateway._run(("rev-parse", "HEAD"), cwd=tmp_path)

    assert result.stdout == b"o" * (64 * 1024)
    assert result.stderr == b"e" * (64 * 1024)


def test_nonzero_exit_uses_bounded_chinese_diagnostic(tmp_path: Path):
    huge_secret_tail = "secret-tail"
    stderr = ("错误" * 5000 + huge_secret_tail).encode("utf-8")
    runner = FakeGitRunner([completed_git_process(returncode=128, stderr=stderr)])
    gateway = GitWorktreeGateway(
        config=WorktreeConfig(digest="abc123"),
        env={},
        runner=runner,
    )

    with pytest.raises(WorktreeError) as captured:
        gateway._run(("status",), cwd=tmp_path)

    error = captured.value
    assert error.code == "git_failed"
    assert error.phase == "git"
    assert error.git_exit_code == 128
    assert "Git 命令失败" in error.message
    assert len(error.message.encode("utf-8")) <= 4096
    assert huge_secret_tail not in error.message


def test_timeout_maps_to_stable_error_without_output_leak(tmp_path: Path):
    runner = FakeGitRunner(
        [
            subprocess.TimeoutExpired(
                cmd=("git", "status"),
                timeout=3,
                output=b"stdout-secret",
                stderr=b"stderr-secret",
            )
        ]
    )
    gateway = GitWorktreeGateway(
        config=WorktreeConfig(digest="abc123"),
        env={},
        runner=runner,
    )

    with pytest.raises(WorktreeError) as captured:
        gateway._run(("status",), cwd=tmp_path)

    assert captured.value.code == "git_timeout"
    assert captured.value.phase == "git"
    assert "超时" in captured.value.message
    assert "secret" not in captured.value.message


def test_missing_git_maps_to_stable_error(tmp_path: Path):
    runner = FakeGitRunner([FileNotFoundError("git")])
    gateway = GitWorktreeGateway(
        config=WorktreeConfig(digest="abc123"),
        env={},
        runner=runner,
    )

    with pytest.raises(WorktreeError) as captured:
        gateway._run(("status",), cwd=tmp_path)

    assert captured.value.code == "git_missing"
    assert captured.value.phase == "git"
    assert "Git 不可用" in captured.value.message


def test_undecodable_error_output_still_has_stable_summary(tmp_path: Path):
    runner = FakeGitRunner([completed_git_process(returncode=2, stderr=b"\xff\xfe\xfd")])
    gateway = GitWorktreeGateway(
        config=WorktreeConfig(digest="abc123"),
        env={},
        runner=runner,
    )

    with pytest.raises(WorktreeError, match="Git 命令失败") as captured:
        gateway._run(("status",), cwd=tmp_path)

    assert captured.value.code == "git_failed"
    assert captured.value.git_exit_code == 2


def test_identify_repository_returns_real_roots_and_stable_identity(tmp_path: Path):
    repository = create_git_repository_with_bare_remote(tmp_path)
    gateway = GitWorktreeGateway(config=WorktreeConfig(digest="abc123"), env=repository.env)

    identity = gateway.identify_repository(repository.root)
    repeated = gateway.identify_repository(repository.root)

    assert identity.root == repository.root.resolve()
    assert identity.common_dir == (repository.root / ".git").resolve()
    assert identity.repository_id == sha256(
        f"{identity.root}\0{identity.common_dir}".encode("utf-8")
    ).hexdigest()
    assert repeated == identity


def test_validate_ignored_root_accepts_ignored_worktrees_directory(tmp_path: Path):
    repository = create_git_repository_with_bare_remote(tmp_path)
    (repository.root / ".gitignore").write_text(".worktrees/\n", encoding="utf-8")
    gateway = GitWorktreeGateway(config=WorktreeConfig(digest="abc123"), env=repository.env)

    gateway.validate_ignored_root(repository.root / ".worktrees")


def test_validate_ignored_root_rejects_unignored_path_without_mutating_gitignore(
    tmp_path: Path,
):
    repository = create_git_repository_with_bare_remote(tmp_path)
    gitignore = repository.root / ".gitignore"
    gitignore.write_text("logs/\n", encoding="utf-8")
    gateway = GitWorktreeGateway(config=WorktreeConfig(digest="abc123"), env=repository.env)

    with pytest.raises(WorktreeError, match="忽略"):
        gateway.validate_ignored_root(repository.root / ".worktrees")

    assert gitignore.read_text(encoding="utf-8") == "logs/\n"


def test_list_porcelain_parses_branch_detached_locked_prunable_and_space_paths(
    tmp_path: Path,
):
    oid_main = "a" * 40
    oid_detached = "b" * 40
    oid_feature = "c" * 40
    main_path = tmp_path / "repo"
    detached_path = tmp_path / "detached worktree"
    feature_path = tmp_path / "feature"
    runner = FakeGitRunner(
        [
            completed_git_process(
                stdout=_porcelain(
                    [
                        f"worktree {main_path}",
                        f"HEAD {oid_main}",
                        "branch refs/heads/main",
                    ],
                    [
                        f"worktree {detached_path}",
                        f"HEAD {oid_detached}",
                        "detached",
                        "locked manual inspection",
                    ],
                    [
                        f"worktree {feature_path}",
                        f"HEAD {oid_feature}",
                        "branch refs/heads/feature/worktree",
                        "prunable gitdir file points to missing location",
                    ],
                )
            )
        ]
    )
    gateway = GitWorktreeGateway(
        config=WorktreeConfig(digest="abc123"),
        env={},
        runner=runner,
    )

    entries = gateway.list_porcelain(tmp_path)

    assert entries == (
        GitWorktreeEntry(
            path=main_path.resolve(),
            head=oid_main,
            branch="main",
            locked=False,
            prunable=False,
        ),
        GitWorktreeEntry(
            path=detached_path.resolve(),
            head=oid_detached,
            branch=None,
            locked=True,
            prunable=False,
        ),
        GitWorktreeEntry(
            path=feature_path.resolve(),
            head=oid_feature,
            branch="feature/worktree",
            locked=False,
            prunable=True,
        ),
    )
    assert runner.calls[0].command == ("git", "worktree", "list", "--porcelain", "-z")


def _porcelain(*records: list[str]) -> bytes:
    return b"".join(("\0".join(record) + "\0\0").encode("utf-8") for record in records)


@pytest.mark.parametrize(
    ("stdout", "pattern"),
    [
        (_porcelain(["HEAD " + "a" * 40, "branch refs/heads/main"]), "worktree"),
        (_porcelain(["worktree C:/repo", "branch refs/heads/main"]), "HEAD"),
        (
            _porcelain(["worktree C:/repo", "HEAD " + "a" * 40, "HEAD " + "b" * 40]),
            "重复",
        ),
        (_porcelain(["worktree relative/path", "HEAD " + "a" * 40]), "绝对"),
        (_porcelain(["worktree C:/repo", "HEAD nope"]), "OID"),
        (
            _porcelain(["worktree C:/repo", "HEAD " + "a" * 40, "branch refs/tags/v1"]),
            "branch",
        ),
    ],
)
def test_list_porcelain_fails_closed_for_malformed_records(
    tmp_path: Path,
    stdout: bytes,
    pattern: str,
):
    runner = FakeGitRunner([completed_git_process(stdout=stdout)])
    gateway = GitWorktreeGateway(
        config=WorktreeConfig(digest="abc123"),
        env={},
        runner=runner,
    )

    with pytest.raises(WorktreeError, match=pattern):
        gateway.list_porcelain(tmp_path)


def test_identify_repository_rejects_common_dir_inside_worktrees_root(tmp_path: Path):
    repository_root = tmp_path / "repo"
    bad_common = repository_root / ".worktrees" / "bad-common.git"
    bad_common.mkdir(parents=True)
    runner = FakeGitRunner(
        [
            completed_git_process(stdout=str(repository_root).encode("utf-8") + b"\n"),
            completed_git_process(stdout=str(bad_common).encode("utf-8") + b"\n"),
        ]
    )
    gateway = GitWorktreeGateway(
        config=WorktreeConfig(digest="abc123"),
        env={},
        runner=runner,
    )

    with pytest.raises(WorktreeError, match="common"):
        gateway.identify_repository(repository_root)


def test_status_parses_staged_unstaged_untracked_ignored_and_renamed_paths(
    tmp_path: Path,
):
    repository = create_git_repository_with_bare_remote(tmp_path)
    (repository.root / ".gitignore").write_text("ignored.txt\n", encoding="utf-8")
    (repository.root / "old.txt").write_text("old\n", encoding="utf-8")
    run_git(("add", ".gitignore", "old.txt"), cwd=repository.root, env=repository.env)
    run_git(("commit", "-m", "status fixtures"), cwd=repository.root, env=repository.env)
    run_git(("mv", "old.txt", "new.txt"), cwd=repository.root, env=repository.env)
    (repository.root / "staged.txt").write_text("staged\n", encoding="utf-8")
    run_git(("add", "staged.txt"), cwd=repository.root, env=repository.env)
    (repository.root / "README.md").write_text("# changed\n", encoding="utf-8")
    (repository.root / "untracked.txt").write_text("untracked\n", encoding="utf-8")
    (repository.root / "ignored.txt").write_text("ignored\n", encoding="utf-8")
    gateway = GitWorktreeGateway(config=WorktreeConfig(digest="abc123"), env=repository.env)

    status = gateway.status(repository.root)

    assert status == GitStatus(
        has_staged_changes=True,
        has_unstaged_changes=True,
        untracked_paths=("untracked.txt",),
    )


def test_status_fails_closed_for_malformed_porcelain(tmp_path: Path):
    runner = FakeGitRunner([completed_git_process(stdout=b"2 R. N... missing-old-path\0")])
    gateway = GitWorktreeGateway(
        config=WorktreeConfig(digest="abc123"),
        env={},
        runner=runner,
    )

    with pytest.raises(WorktreeError, match="status"):
        gateway.status(tmp_path)


def test_upstream_returns_tracking_branch_or_none_when_unconfigured(tmp_path: Path):
    repository = create_git_repository_with_bare_remote(tmp_path)
    gateway = GitWorktreeGateway(config=WorktreeConfig(digest="abc123"), env=repository.env)

    assert gateway.upstream(repository.root) == "origin/main"

    run_git(("branch", "--unset-upstream"), cwd=repository.root, env=repository.env)

    assert gateway.upstream(repository.root) is None


def test_upstream_other_git_errors_fail_closed(tmp_path: Path):
    runner = FakeGitRunner([completed_git_process(returncode=128, stderr=b"fatal: bad ref")])
    gateway = GitWorktreeGateway(
        config=WorktreeConfig(digest="abc123"),
        env={},
        runner=runner,
    )

    with pytest.raises(WorktreeError) as captured:
        gateway.upstream(tmp_path)

    assert captured.value.code == "git_failed"


def test_commits_not_in_upstream_returns_oldest_to_newest_local_commits(tmp_path: Path):
    repository = create_git_repository_with_bare_remote(tmp_path)
    gateway = GitWorktreeGateway(config=WorktreeConfig(digest="abc123"), env=repository.env)
    (repository.root / "one.txt").write_text("one\n", encoding="utf-8")
    run_git(("add", "one.txt"), cwd=repository.root, env=repository.env)
    run_git(("commit", "-m", "one"), cwd=repository.root, env=repository.env)
    first = run_git(("rev-parse", "HEAD"), cwd=repository.root, env=repository.env).stdout.decode().strip()
    (repository.root / "two.txt").write_text("two\n", encoding="utf-8")
    run_git(("add", "two.txt"), cwd=repository.root, env=repository.env)
    run_git(("commit", "-m", "two"), cwd=repository.root, env=repository.env)
    second = run_git(("rev-parse", "HEAD"), cwd=repository.root, env=repository.env).stdout.decode().strip()

    assert gateway.commits_not_in_upstream(repository.root, "origin/main") == (first, second)


def test_commits_not_in_upstream_fails_closed_for_missing_ref(tmp_path: Path):
    repository = create_git_repository_with_bare_remote(tmp_path)
    gateway = GitWorktreeGateway(config=WorktreeConfig(digest="abc123"), env=repository.env)

    with pytest.raises(WorktreeError):
        gateway.commits_not_in_upstream(repository.root, "origin/missing")


def test_capture_head_returns_committed_oid_when_main_worktree_is_dirty(tmp_path: Path):
    repository = create_git_repository_with_bare_remote(tmp_path)
    gateway = GitWorktreeGateway(config=WorktreeConfig(digest="abc123"), env=repository.env)
    expected = run_git(("rev-parse", "HEAD"), cwd=repository.root, env=repository.env).stdout.decode().strip()
    (repository.root / "README.md").write_text("# dirty but uncommitted\n", encoding="utf-8")

    assert gateway.capture_head(repository.root) == expected


def test_add_creates_isolated_worktree_from_base_commit_without_main_dirty_content(
    tmp_path: Path,
):
    repository = create_git_repository_with_bare_remote(tmp_path)
    gateway = GitWorktreeGateway(config=WorktreeConfig(digest="abc123"), env=repository.env)
    identity = _identity(repository.root, gateway)
    target = repository.root / ".worktrees" / "general" / "task-000001"
    target.parent.mkdir(parents=True)
    (repository.root / "README.md").write_text("# dirty but uncommitted\n", encoding="utf-8")

    gateway.add(identity, target)

    assert target.is_dir()
    assert (target / "README.md").read_text(encoding="utf-8") == "# test repo\n"
    assert run_git(("branch", "--show-current"), cwd=repository.root, env=repository.env).stdout.decode().strip() == "main"
    assert run_git(("status", "--short"), cwd=repository.root, env=repository.env).stdout.decode().startswith(" M README.md")


def test_remove_uses_non_force_worktree_remove(tmp_path: Path):
    target = tmp_path / "worktree"
    target.mkdir()
    runner = FakeGitRunner([completed_git_process()])
    gateway = GitWorktreeGateway(
        config=WorktreeConfig(digest="abc123"),
        env={},
        runner=runner,
    )

    gateway.remove(tmp_path, target)

    assert runner.calls[0].command == ("git", "worktree", "remove", str(target))
    assert "--force" not in runner.calls[0].command


def test_delete_branch_rejects_uncontrolled_or_mismatched_branch(tmp_path: Path):
    gateway = GitWorktreeGateway(
        config=WorktreeConfig(digest="abc123"),
        env={},
        runner=FakeGitRunner(),
    )

    with pytest.raises(WorktreeError, match="分支"):
        gateway.delete_branch(tmp_path, "main", expected_branch="main")
    with pytest.raises(WorktreeError, match="租约"):
        gateway.delete_branch(
            tmp_path,
            "mycode/worktree/general/task-000001",
            expected_branch="mycode/worktree/general/other",
        )


def test_delete_branch_removes_pushed_but_unmerged_temporary_branch(tmp_path: Path):
    repository = create_git_repository_with_bare_remote(tmp_path)
    gateway = GitWorktreeGateway(config=WorktreeConfig(digest="abc123"), env=repository.env)
    identity = _identity(repository.root, gateway)
    target = repository.root / ".worktrees" / "general" / "task-000001"
    target.parent.mkdir(parents=True)
    gateway.add(identity, target)
    (target / "feature.txt").write_text("feature\n", encoding="utf-8")
    run_git(("add", "feature.txt"), cwd=target, env=repository.env)
    run_git(("commit", "-m", "feature"), cwd=target, env=repository.env)
    run_git(("push", "-u", "origin", identity.branch_name), cwd=target, env=repository.env)
    gateway.remove(repository.root, target)

    gateway.delete_branch(
        repository.root,
        identity.branch_name,
        expected_branch=identity.branch_name,
    )

    assert (
        run_git(("branch", "--list", identity.branch_name), cwd=repository.root, env=repository.env)
        .stdout.decode()
        .strip()
        == ""
    )


def test_integration_helpers_use_local_structured_git_commands(tmp_path: Path):
    runner = FakeGitRunner(
        [
            completed_git_process(),
            completed_git_process(),
            completed_git_process(),
            completed_git_process(),
        ]
    )
    gateway = GitWorktreeGateway(
        config=WorktreeConfig(digest="abc123"),
        env={},
        runner=runner,
    )
    repository_root = tmp_path.resolve()
    integration_root, branch = gateway.create_integration_branch(
        repository_root,
        "batch-1",
        "a" * 40,
    )
    integration_root.mkdir(parents=True, exist_ok=True)
    gateway.merge_commit(integration_root, "b" * 40)
    gateway.update_local_ref(repository_root, "main", "c" * 40)
    gateway.abort_merge(integration_root)

    assert integration_root == repository_root / ".worktrees" / "integration" / "batch-1"
    assert branch == "mycode/team/integration/batch-1"
    assert [call.command for call in runner.calls] == [
        (
            "git",
            "worktree",
            "add",
            "-b",
            "mycode/team/integration/batch-1",
            str(integration_root),
            "a" * 40,
        ),
        ("git", "merge", "--no-edit", "b" * 40),
        ("git", "update-ref", "refs/heads/main", "c" * 40),
        ("git", "merge", "--abort"),
    ]


def _identity(repository_root: Path, gateway: GitWorktreeGateway) -> WorkspaceTaskIdentity:
    repository_identity = gateway.identify_repository(repository_root)
    base_commit = gateway.capture_head(repository_root)
    return WorkspaceTaskIdentity(
        repository_id=repository_identity.repository_id,
        task_id="task-000001",
        role_name="general",
        task_token="task-000001",
        relative_name="general/task-000001",
        branch_name="mycode/worktree/general/task-000001",
        base_commit=base_commit,
    )
