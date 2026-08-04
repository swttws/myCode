from __future__ import annotations

import os
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class GitTestEnvironment:
    env: dict[str, str]
    home: Path
    global_config: Path


@dataclass(frozen=True)
class GitTestRepository:
    root: Path
    bare_remote: Path
    env: dict[str, str]


@dataclass(frozen=True)
class RecordedGitRun:
    command: tuple[str, ...] | str
    cwd: Path | None
    env: Mapping[str, str] | None
    shell: bool | None
    capture_output: bool | None
    timeout: float | None
    check: bool | None
    text: bool | None


class FakeGitRunner:
    def __init__(self, responses: Sequence[Any] = ()) -> None:
        self.responses = list(responses)
        self.calls: list[RecordedGitRun] = []

    def __call__(
        self,
        command: Sequence[str] | str,
        *,
        cwd: Path | str | None = None,
        env: Mapping[str, str] | None = None,
        shell: bool | None = None,
        capture_output: bool | None = None,
        timeout: float | None = None,
        check: bool | None = None,
        text: bool | None = None,
        **_: Any,
    ) -> subprocess.CompletedProcess[bytes]:
        recorded_command: tuple[str, ...] | str
        if isinstance(command, str):
            recorded_command = command
        else:
            recorded_command = tuple(command)
        self.calls.append(
            RecordedGitRun(
                command=recorded_command,
                cwd=Path(cwd) if cwd is not None else None,
                env=env,
                shell=shell,
                capture_output=capture_output,
                timeout=timeout,
                check=check,
                text=text,
            )
        )
        if self.responses:
            response = self.responses.pop(0)
            if isinstance(response, BaseException):
                raise response
            return response
        return completed_git_process(recorded_command)


def isolated_git_environment(tmp_path: Path) -> GitTestEnvironment:
    home = tmp_path / "home"
    home.mkdir()
    global_config = home / ".gitconfig"
    global_config.write_text("", encoding="utf-8")

    env = os.environ.copy()
    env.update(
        {
            "HOME": str(home),
            "USERPROFILE": str(home),
            "GIT_CONFIG_GLOBAL": str(global_config),
            "GIT_CONFIG_NOSYSTEM": "1",
        }
    )
    return GitTestEnvironment(env=env, home=home, global_config=global_config)


def create_git_repository_with_bare_remote(tmp_path: Path) -> GitTestRepository:
    test_env = isolated_git_environment(tmp_path)
    bare_remote = tmp_path / "remote.git"
    repository_root = tmp_path / "repo"

    run_git(("init", "--bare", str(bare_remote)), cwd=tmp_path, env=test_env.env)
    repository_root.mkdir()
    run_git(("init", "-b", "main"), cwd=repository_root, env=test_env.env)
    run_git(("config", "user.name", "myCode Tests"), cwd=repository_root, env=test_env.env)
    run_git(
        ("config", "user.email", "mycode-tests@example.invalid"),
        cwd=repository_root,
        env=test_env.env,
    )
    (repository_root / "README.md").write_text("# test repo\n", encoding="utf-8")
    run_git(("add", "README.md"), cwd=repository_root, env=test_env.env)
    run_git(("commit", "-m", "initial commit"), cwd=repository_root, env=test_env.env)
    run_git(("remote", "add", "origin", str(bare_remote)), cwd=repository_root, env=test_env.env)
    run_git(("push", "-u", "origin", "main"), cwd=repository_root, env=test_env.env)

    return GitTestRepository(root=repository_root, bare_remote=bare_remote, env=test_env.env)


def run_git(
    args: Sequence[str],
    *,
    cwd: Path,
    env: Mapping[str, str],
) -> subprocess.CompletedProcess[bytes]:
    if isinstance(args, str):
        raise TypeError("args must be a sequence, not a shell string")
    if not cwd.is_absolute():
        raise ValueError("cwd must be absolute")
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        env=dict(env),
        shell=False,
        check=True,
        capture_output=True,
        text=False,
    )


def completed_git_process(
    command: Sequence[str] | str = ("git",),
    *,
    returncode: int = 0,
    stdout: bytes = b"",
    stderr: bytes = b"",
) -> subprocess.CompletedProcess[bytes]:
    return subprocess.CompletedProcess(
        args=command,
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
    )
