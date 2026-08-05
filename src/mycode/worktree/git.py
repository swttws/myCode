from __future__ import annotations

import os
import re
import subprocess
from collections.abc import Mapping, Sequence
from hashlib import sha256
from pathlib import Path
from typing import Protocol

from mycode.workspace import WorkspaceTaskIdentity
from mycode.worktree.models import (
    GitStatus,
    GitWorktreeEntry,
    RepositoryIdentity,
    WorktreeConfig,
    WorktreeError,
)
from mycode.worktree.pathing import WorktreePathPolicy


_MAX_STREAM_BYTES = 64 * 1024
_MAX_DIAGNOSTIC_BYTES = 4096
_OID_RE = re.compile(r"^[0-9a-fA-F]{40}$")
_BRANCH_PREFIX = "refs/heads/"


class GitRunner(Protocol):
    def __call__(
        self,
        command: Sequence[str],
        *,
        cwd: Path,
        env: Mapping[str, str],
        shell: bool,
        check: bool,
        capture_output: bool,
        timeout: float,
        text: bool,
    ) -> subprocess.CompletedProcess[bytes]: ...


class GitWorktreeGateway:
    def __init__(
        self,
        *,
        config: WorktreeConfig,
        env: Mapping[str, str] | None = None,
        runner: GitRunner | None = None,
    ) -> None:
        if not isinstance(config, WorktreeConfig):
            raise ValueError("config must be a WorktreeConfig")
        self._config = config
        self._env = dict(env or {})
        self._runner = runner or subprocess.run

    def identify_repository(self, repository_root: Path) -> RepositoryIdentity:
        root = self._resolve_directory_arg("repository_root", repository_root)
        top_level = _decode_single_line(
            self._run(("rev-parse", "--show-toplevel"), cwd=root).stdout,
            "repository_root",
        )
        common_dir = _decode_single_line(
            self._run(("rev-parse", "--git-common-dir"), cwd=root).stdout,
            "common directory",
        )
        resolved_root = _resolve_git_path(top_level, root)
        resolved_common = _resolve_git_path(common_dir, root)
        if _is_relative_to(resolved_common, resolved_root / ".worktrees"):
            raise WorktreeError(
                code="git_common_dir_boundary",
                phase="git",
                message="Git common directory 越过受控边界",
                path=resolved_common,
            )
        repository_id = sha256(f"{resolved_root}\0{resolved_common}".encode("utf-8")).hexdigest()
        return RepositoryIdentity(
            root=resolved_root,
            common_dir=resolved_common,
            repository_id=repository_id,
        )

    def validate_ignored_root(self, worktrees_root: Path) -> None:
        if not isinstance(worktrees_root, Path):
            raise WorktreeError(
                code="worktree_root_not_ignored",
                phase="git",
                message="Worktree 根目录必须是路径",
            )
        if not worktrees_root.is_absolute():
            raise WorktreeError(
                code="worktree_root_not_ignored",
                phase="git",
                message="Worktree 根目录必须是绝对路径",
            )
        repository_root = worktrees_root.parent
        try:
            relative = worktrees_root.relative_to(repository_root).as_posix().rstrip("/") + "/"
        except ValueError as exc:
            raise WorktreeError(
                code="worktree_root_not_ignored",
                phase="git",
                message="Worktree 根目录必须位于仓库内",
                path=worktrees_root,
            ) from exc

        try:
            self._run(("check-ignore", "-q", "--", relative), cwd=repository_root)
        except WorktreeError as exc:
            if exc.code == "git_failed":
                raise WorktreeError(
                    code="worktree_root_not_ignored",
                    phase="git",
                    message="Worktree 根目录未被 Git 忽略",
                    path=worktrees_root,
                ) from exc
            raise

    def list_porcelain(self, repository_root: Path) -> tuple[GitWorktreeEntry, ...]:
        root = self._resolve_directory_arg("repository_root", repository_root)
        result = self._run(("worktree", "list", "--porcelain", "-z"), cwd=root)
        return _parse_worktree_list(result.stdout)

    def status(self, target: Path) -> GitStatus:
        root = self._resolve_directory_arg("target", target)
        result = self._run(
            ("status", "--porcelain=v2", "-z", "--ignored=matching"),
            cwd=root,
        )
        return _parse_status(result.stdout)

    def upstream(self, target: Path) -> str | None:
        root = self._resolve_directory_arg("target", target)
        try:
            result = self._run(
                ("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}"),
                cwd=root,
            )
        except WorktreeError as exc:
            if exc.code == "git_failed" and _is_missing_upstream_error(exc.message):
                return None
            raise
        upstream = result.stdout.decode("utf-8", errors="replace").strip()
        return upstream or None

    def commits_not_in_upstream(self, target: Path, upstream: str) -> tuple[str, ...]:
        if type(upstream) is not str or not upstream:
            raise WorktreeError(
                code="git_invalid_arguments",
                phase="git",
                message="upstream 必须是非空字符串",
                path=target if isinstance(target, Path) and target.is_absolute() else None,
            )
        root = self._resolve_directory_arg("target", target)
        result = self._run(("rev-list", "--reverse", f"{upstream}..HEAD"), cwd=root)
        commits = tuple(
            line.strip().lower()
            for line in result.stdout.decode("utf-8", errors="replace").splitlines()
            if line.strip()
        )
        for commit in commits:
            if not _OID_RE.match(commit):
                raise WorktreeError(
                    code="git_parse_failed",
                    phase="git",
                    message="rev-list 输出包含非法 OID",
                    path=root,
                )
        return commits

    def capture_head(self, repository_root: Path) -> str:
        root = self._resolve_directory_arg("repository_root", repository_root)
        result = self._run(("rev-parse", "--verify", "HEAD^{commit}"), cwd=root)
        head = result.stdout.decode("utf-8", errors="replace").strip().lower()
        if not _OID_RE.match(head):
            raise WorktreeError(
                code="git_parse_failed",
                phase="git",
                message="HEAD OID 非法",
                path=root,
            )
        return head

    def current_branch(self, repository_root: Path) -> str:
        root = self._resolve_directory_arg("repository_root", repository_root)
        result = self._run(("branch", "--show-current"), cwd=root)
        branch = result.stdout.decode("utf-8", errors="replace").strip()
        if not _is_safe_local_branch(branch):
            raise WorktreeError(
                code="git_parse_failed",
                phase="git",
                message="current branch name is invalid",
                path=root,
            )
        return branch

    def create_integration_branch(
        self,
        repository_root: Path,
        batch_id: str,
        base_commit: str,
    ) -> tuple[Path, str]:
        root = self._resolve_directory_arg("repository_root", repository_root)
        if type(batch_id) is not str or not batch_id:
            raise WorktreeError(
                code="git_invalid_arguments",
                phase="git",
                message="batch_id must be a non-empty string",
                path=root,
            )
        if not _OID_RE.match(base_commit):
            raise WorktreeError(
                code="git_invalid_arguments",
                phase="git",
                message="base_commit OID is invalid",
                path=root,
            )
        policy = WorktreePathPolicy(repository_root=root)
        relative_name = policy.validate_relative_name(f"integration/{batch_id}")
        branch = policy.validate_branch_name(f"mycode/team/integration/{batch_id}")
        integration_root = root / ".worktrees" / Path(*relative_name.split("/"))
        integration_root.parent.mkdir(parents=True, exist_ok=True)
        self._run(
            (
                "worktree",
                "add",
                "-b",
                branch,
                str(integration_root),
                base_commit,
            ),
            cwd=root,
        )
        return integration_root, branch

    def merge_commit(self, integration_root: Path, commit_id: str) -> None:
        root = self._resolve_directory_arg("integration_root", integration_root)
        if not _OID_RE.match(commit_id):
            raise WorktreeError(
                code="git_invalid_arguments",
                phase="git",
                message="commit_id OID is invalid",
                path=root,
            )
        self._run(("merge", "--no-edit", commit_id), cwd=root)

    def update_local_ref(self, repository_root: Path, branch: str, commit_id: str) -> None:
        root = self._resolve_directory_arg("repository_root", repository_root)
        if not _is_safe_local_branch(branch):
            raise WorktreeError(
                code="git_invalid_arguments",
                phase="git",
                message="branch name is invalid",
                path=root,
                branch_name=branch if isinstance(branch, str) and branch else None,
            )
        if not _OID_RE.match(commit_id):
            raise WorktreeError(
                code="git_invalid_arguments",
                phase="git",
                message="commit_id OID is invalid",
                path=root,
            )
        self._run(("update-ref", f"refs/heads/{branch}", commit_id), cwd=root)

    def abort_merge(self, integration_root: Path) -> None:
        root = self._resolve_directory_arg("integration_root", integration_root)
        self._run(("merge", "--abort"), cwd=root)

    def add(self, identity: WorkspaceTaskIdentity, target: Path) -> None:
        if not isinstance(identity, WorkspaceTaskIdentity):
            raise WorktreeError(
                code="git_invalid_arguments",
                phase="git",
                message="identity 必须是 WorkspaceTaskIdentity",
            )
        if not _OID_RE.match(identity.base_commit):
            raise WorktreeError(
                code="git_invalid_arguments",
                phase="git",
                message="base_commit OID 非法",
            )
        repository_root = self._find_repository_root_for_target(target)
        policy = WorktreePathPolicy(repository_root=repository_root)
        policy.validate_branch_name(identity.branch_name)
        resolved_target = policy.assert_target_boundary(target)
        self._run(
            (
                "worktree",
                "add",
                "-b",
                identity.branch_name,
                str(resolved_target),
                identity.base_commit,
            ),
            cwd=repository_root,
        )

    def remove(self, repository_root: Path, target: Path) -> None:
        root = self._resolve_directory_arg("repository_root", repository_root)
        if not isinstance(target, Path):
            raise WorktreeError(
                code="git_invalid_arguments",
                phase="git",
                message="target 必须是路径",
                path=root,
            )
        if not target.is_absolute():
            raise WorktreeError(
                code="git_invalid_arguments",
                phase="git",
                message="target 必须是绝对路径",
                path=root,
            )
        self._run(("worktree", "remove", str(target.resolve(strict=False))), cwd=root)

    def delete_branch(
        self,
        repository_root: Path,
        branch: str,
        *,
        expected_branch: str | None = None,
    ) -> None:
        root = self._resolve_directory_arg("repository_root", repository_root)
        if expected_branch is not None and branch != expected_branch:
            raise WorktreeError(
                code="git_branch_lease_mismatch",
                phase="git",
                message="租约分支不匹配，拒绝删除临时分支",
                path=root,
                branch_name=branch if branch else None,
            )
        WorktreePathPolicy(repository_root=root).validate_branch_name(branch)
        self._run(("branch", "-D", branch), cwd=root)

    def remove_integration_worktree(
        self,
        repository_root: Path,
        integration_root: Path,
        branch: str,
    ) -> None:
        self.remove(repository_root, integration_root)
        self.delete_branch(repository_root, branch, expected_branch=branch)

    def _run(
        self,
        args: Sequence[str],
        *,
        cwd: Path,
        env_overrides: Mapping[str, str] | None = None,
    ) -> subprocess.CompletedProcess[bytes]:
        command = self._command(args, cwd)
        env = os.environ.copy()
        env.update(self._env)
        if env_overrides:
            env.update(env_overrides)

        try:
            completed = self._runner(
                command,
                cwd=cwd,
                env=env,
                shell=False,
                check=False,
                capture_output=True,
                timeout=self._config.git_timeout_seconds,
                text=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise WorktreeError(
                code="git_timeout",
                phase="git",
                message=f"Git 命令超时：timeout={exc.timeout:g} 秒",
                path=cwd,
            ) from exc
        except FileNotFoundError as exc:
            raise WorktreeError(
                code="git_missing",
                phase="git",
                message="Git 不可用：找不到 git 可执行文件",
                path=cwd,
            ) from exc

        stdout = _bounded_bytes(completed.stdout)
        stderr = _bounded_bytes(completed.stderr)
        result = subprocess.CompletedProcess(
            args=tuple(command),
            returncode=completed.returncode,
            stdout=stdout,
            stderr=stderr,
        )
        if completed.returncode != 0:
            raise WorktreeError(
                code="git_failed",
                phase="git",
                message=_nonzero_message(command, completed.returncode, stdout, stderr),
                path=cwd,
                git_exit_code=completed.returncode,
            )
        return result

    def _command(self, args: Sequence[str], cwd: Path) -> tuple[str, ...]:
        if isinstance(args, str):
            raise WorktreeError(
                code="git_invalid_arguments",
                phase="git",
                message="Git 参数必须是字符串数组，不能是 shell 字符串",
                path=cwd if isinstance(cwd, Path) and cwd.is_absolute() else None,
            )
        if not isinstance(cwd, Path):
            raise WorktreeError(
                code="git_invalid_cwd",
                phase="git",
                message="Git cwd 必须是路径",
            )
        if not cwd.is_absolute():
            raise WorktreeError(
                code="git_invalid_cwd",
                phase="git",
                message="Git cwd 必须是绝对路径",
            )
        normalized_args: list[str] = []
        for index, arg in enumerate(args):
            if type(arg) is not str or not arg:
                raise WorktreeError(
                    code="git_invalid_arguments",
                    phase="git",
                    message=f"Git 参数第 {index + 1} 项必须是非空字符串",
                    path=cwd,
                )
            normalized_args.append(arg)
        return ("git", *normalized_args)

    def _find_repository_root_for_target(self, target: Path) -> Path:
        if not isinstance(target, Path):
            raise WorktreeError(
                code="git_invalid_arguments",
                phase="git",
                message="target 必须是路径",
            )
        if not target.is_absolute():
            raise WorktreeError(
                code="git_invalid_arguments",
                phase="git",
                message="target 必须是绝对路径",
            )
        for ancestor in (target.parent, *target.parents):
            if (ancestor / ".git").exists():
                return ancestor.resolve(strict=True)
        raise WorktreeError(
            code="git_invalid_arguments",
            phase="git",
            message="无法从目标路径定位仓库根目录",
            path=target,
        )

    def _resolve_directory_arg(self, field_name: str, value: Path) -> Path:
        if not isinstance(value, Path):
            raise WorktreeError(
                code="git_invalid_cwd",
                phase="git",
                message=f"{field_name} 必须是路径",
            )
        if not value.is_absolute():
            raise WorktreeError(
                code="git_invalid_cwd",
                phase="git",
                message=f"{field_name} 必须是绝对路径",
            )
        try:
            resolved = value.resolve(strict=True)
        except OSError as exc:
            raise WorktreeError(
                code="git_invalid_cwd",
                phase="git",
                message=f"{field_name} 无法解析",
                path=value,
            ) from exc
        if not resolved.is_dir():
            raise WorktreeError(
                code="git_invalid_cwd",
                phase="git",
                message=f"{field_name} 不是目录",
                path=resolved,
            )
        return resolved


def _bounded_bytes(value: bytes | str | None) -> bytes:
    if value is None:
        return b""
    if isinstance(value, str):
        value = value.encode("utf-8", errors="replace")
    return bytes(value[:_MAX_STREAM_BYTES])


def _decode_single_line(data: bytes, field_name: str) -> str:
    text = data.decode("utf-8", errors="replace").strip()
    if not text or "\n" in text or "\r" in text:
        raise WorktreeError(
            code="git_parse_failed",
            phase="git",
            message=f"{field_name} Git 输出格式非法",
        )
    return text


def _resolve_git_path(raw: str, cwd: Path) -> Path:
    path = Path(raw)
    if not path.is_absolute():
        path = cwd / path
    try:
        return path.resolve(strict=True)
    except OSError as exc:
        raise WorktreeError(
            code="git_parse_failed",
            phase="git",
            message="Git 路径无法解析",
            path=path if path.is_absolute() else None,
        ) from exc


def _parse_worktree_list(data: bytes) -> tuple[GitWorktreeEntry, ...]:
    if not data:
        return ()
    entries: list[GitWorktreeEntry] = []
    for record_index, raw_record in enumerate(data.split(b"\0\0")):
        if not raw_record:
            continue
        lines = [line for line in raw_record.split(b"\0") if line]
        entries.append(_parse_worktree_record(lines, record_index))
    return tuple(entries)


def _parse_status(data: bytes) -> GitStatus:
    has_staged = False
    has_unstaged = False
    untracked_paths: list[str] = []
    records = data.split(b"\0")
    index = 0
    while index < len(records):
        raw = records[index]
        if not raw:
            index += 1
            continue
        line = raw.decode("utf-8", errors="replace")
        tag = line[:1]
        if tag == "#":
            index += 1
            continue
        if tag == "?":
            path = line[2:]
            if not path:
                raise _status_error("untracked 路径为空")
            untracked_paths.append(path)
            index += 1
            continue
        if tag == "!":
            index += 1
            continue
        if tag in {"1", "u"}:
            xy = _status_xy(line)
            has_staged = has_staged or xy[0] != "."
            has_unstaged = has_unstaged or xy[1] != "."
            index += 1
            continue
        if tag == "2":
            xy = _status_xy(line)
            if index + 1 >= len(records) or not records[index + 1]:
                raise _status_error("rename 记录缺少原路径")
            has_staged = has_staged or xy[0] != "."
            has_unstaged = has_unstaged or xy[1] != "."
            index += 2
            continue
        raise _status_error(f"未知记录 {tag}")
    return GitStatus(
        has_staged_changes=has_staged,
        has_unstaged_changes=has_unstaged,
        untracked_paths=tuple(untracked_paths),
    )


def _status_xy(line: str) -> str:
    if len(line) < 4 or line[1] != " ":
        raise _status_error("status 记录格式非法")
    xy = line[2:4]
    if len(xy) != 2:
        raise _status_error("status XY 字段非法")
    return xy


def _status_error(message: str) -> WorktreeError:
    return WorktreeError(
        code="git_parse_failed",
        phase="git",
        message=f"status porcelain 解析失败：{message}",
    )


def _parse_worktree_record(lines: list[bytes], record_index: int) -> GitWorktreeEntry:
    fields: dict[str, str | bool] = {}
    for raw_line in lines:
        line = raw_line.decode("utf-8", errors="replace")
        key, separator, value = line.partition(" ")
        if key in fields:
            raise _parse_error(record_index, f"{key} 字段重复")
        if key in {"worktree", "HEAD", "branch"}:
            if not separator or not value:
                raise _parse_error(record_index, f"{key} 字段缺少值")
            fields[key] = value
        elif key == "detached":
            fields[key] = True
        elif key == "locked":
            fields[key] = True
        elif key == "prunable":
            fields[key] = True
        else:
            raise _parse_error(record_index, f"未知字段 {key}")

    raw_path = fields.get("worktree")
    if type(raw_path) is not str:
        raise _parse_error(record_index, "缺少 worktree 字段")
    path = Path(raw_path)
    if not path.is_absolute():
        raise _parse_error(record_index, "worktree 路径必须是绝对路径")
    raw_head = fields.get("HEAD")
    if type(raw_head) is not str:
        raise _parse_error(record_index, "缺少 HEAD 字段")
    if not _OID_RE.match(raw_head):
        raise _parse_error(record_index, "HEAD OID 非法")

    branch = _parse_branch(fields, record_index)
    return GitWorktreeEntry(
        path=path.resolve(strict=False),
        head=raw_head.lower(),
        branch=branch,
        locked=fields.get("locked") is True,
        prunable=fields.get("prunable") is True,
    )


def _parse_branch(fields: dict[str, str | bool], record_index: int) -> str | None:
    has_branch = "branch" in fields
    is_detached = fields.get("detached") is True
    if has_branch and is_detached:
        raise _parse_error(record_index, "branch 与 detached 冲突")
    if not has_branch:
        return None
    raw_branch = fields["branch"]
    if type(raw_branch) is not str or not raw_branch.startswith(_BRANCH_PREFIX):
        raise _parse_error(record_index, "branch 字段非法")
    branch = raw_branch[len(_BRANCH_PREFIX) :]
    if not branch:
        raise _parse_error(record_index, "branch 字段为空")
    return branch


def _parse_error(record_index: int, message: str) -> WorktreeError:
    return WorktreeError(
        code="git_parse_failed",
        phase="git",
        message=f"worktree list 第 {record_index + 1} 条记录{message}",
    )


def _is_relative_to(child: Path, parent: Path) -> bool:
    try:
        child_text = os.path.normcase(str(child))
        parent_text = os.path.normcase(str(parent))
        return os.path.commonpath([child_text, parent_text]) == parent_text
    except ValueError:
        return False


def _is_missing_upstream_error(message: str) -> bool:
    lowered = message.lower()
    return "upstream" in lowered and (
        "no upstream" in lowered
        or "no such branch" in lowered
        or "has no upstream" in lowered
        or "没有" in message
        or "未配置" in message
    )


def _is_safe_local_branch(branch: object) -> bool:
    if type(branch) is not str or not branch:
        return False
    if branch.startswith("-") or branch.startswith("/") or branch.endswith("/"):
        return False
    if branch.endswith(".") or branch.endswith(".lock"):
        return False
    if branch in {".", "..", "HEAD"}:
        return False
    forbidden = set(" ~^:?*[\\")
    if any(character in forbidden for character in branch):
        return False
    if ".." in branch or "@{" in branch or "//" in branch:
        return False
    return all(segment not in {"", ".", ".."} for segment in branch.split("/"))


def _nonzero_message(command: Sequence[str], returncode: int, stdout: bytes, stderr: bytes) -> str:
    summary = (
        f"Git 命令失败：exit={returncode}，args={_format_args(command)}，"
        f"stdout={_diagnostic_text(stdout)}，stderr={_diagnostic_text(stderr)}"
    )
    return _limit_utf8(summary, _MAX_DIAGNOSTIC_BYTES)


def _diagnostic_text(data: bytes) -> str:
    if not data:
        return "<empty>"
    return data.decode("utf-8", errors="replace")


def _format_args(command: Sequence[str]) -> str:
    return " ".join(command[:8])


def _limit_utf8(text: str, limit: int) -> str:
    encoded = text.encode("utf-8")
    if len(encoded) <= limit:
        return text
    return encoded[:limit].decode("utf-8", errors="ignore")
