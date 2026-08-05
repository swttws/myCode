from __future__ import annotations

import os
import re
import stat
from pathlib import Path

from mycode.worktree.models import WorktreeError


_SAFE_SEGMENT_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?$")
_WINDOWS_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}


class WorktreePathPolicy:
    branch_prefix = "mycode/worktree/"
    team_branch_prefix = "mycode/team/"

    def __init__(self, *, repository_root: Path, worktrees_root_name: str = ".worktrees") -> None:
        self.repository_root = repository_root
        self.worktrees_root_name = worktrees_root_name

    def validate_root(self, repository_root: Path) -> Path:
        repository = self._resolve_existing_directory("repository_root", repository_root)
        root = repository / self.worktrees_root_name
        resolved_root = self._resolve_existing_directory("worktrees_root", root)
        if self._is_reparse_point(root):
            raise self._boundary_error("Worktree 根目录不能是符号链接或重解析点", path=root)
        if not _is_relative_to(resolved_root, repository):
            raise self._boundary_error("Worktree 根目录越过仓库边界", path=root)
        return resolved_root

    def resolve_target(self, relative_name: str) -> Path:
        relative = self.validate_relative_name(relative_name)
        target = self._worktrees_root() / Path(*relative.split("/"))
        return self.assert_target_boundary(target)

    def resolve_metadata_path(self, relative_name: str) -> Path:
        relative = self.validate_relative_name(relative_name)
        path = self._worktrees_root() / ".metadata" / Path(*relative.split("/"))
        path = path.with_name(path.name + ".json")
        return self.assert_target_boundary(path)

    def assert_target_boundary(self, target: Path) -> Path:
        root = self._worktrees_root()
        return self._assert_inside_root(
            target,
            root,
            field_name="target",
            phase="pathing",
            message_prefix="目标路径边界检查失败",
        )

    def resolve_rule_source(self, source: str) -> Path:
        repository = self._repository_root()
        path = self._join_relative_path(repository, source, field_name="source")
        return self._assert_inside_root(
            path,
            repository,
            field_name="source",
            phase="pathing",
            message_prefix="规则来源边界检查失败",
        )

    def resolve_rule_target(self, workspace_root: Path, target: str) -> Path:
        workspace = self.assert_target_boundary(workspace_root)
        path = self._join_relative_path(workspace, target, field_name="target")
        return self._assert_inside_root(
            path,
            workspace,
            field_name="target",
            phase="pathing",
            message_prefix="规则目标边界检查失败",
        )

    def resolve_config_rule_target(self, target: str) -> Path:
        repository = self._repository_root()
        workspace = repository / self.worktrees_root_name / "__config_validation__"
        path = self._join_relative_path(workspace, target, field_name="target")
        return self._assert_inside_root(
            path,
            workspace,
            field_name="target",
            phase="pathing",
            message_prefix="规则目标边界检查失败",
        )

    def validate_relative_name(self, value: str) -> str:
        try:
            return self._validate_relative_name(value)
        except WorktreeError:
            raise
        except Exception as exc:
            raise self._name_error("名称非法") from exc

    def validate_branch_name(self, value: str) -> str:
        if type(value) is not str or not value:
            raise self._branch_error("分支名称不能为空")
        prefix = self._branch_prefix(value)
        if prefix is None:
            raise self._branch_error("分支名称必须使用 mycode/worktree/ 前缀")

        suffix = value[len(prefix) :]
        if not suffix:
            raise self._branch_error("分支名称缺少任务路径")
        try:
            self._validate_relative_name(suffix)
        except WorktreeError as exc:
            raise self._branch_error(f"分支名称包含非法任务路径：{exc.message}") from exc
        if any(segment.lower().endswith(".lock") for segment in suffix.split("/")):
            raise self._branch_error("分支名称不能包含 .lock 段")
        return value

    def _branch_prefix(self, value: str) -> str | None:
        for prefix in (self.branch_prefix, self.team_branch_prefix):
            if value.startswith(prefix):
                return prefix
        return None

    def _validate_relative_name(self, value: str) -> str:
        if type(value) is not str or not value:
            raise self._name_error("名称不能为空")
        if len(value) > 200:
            raise self._name_error("名称总长度不能超过 200 字符")
        if "\\" in value:
            raise self._name_error("名称不能包含反斜杠")
        if value.startswith("/") or re.match(r"^[A-Za-z]:", value):
            raise self._name_error("名称不能是绝对路径")
        try:
            value.encode("ascii")
        except UnicodeEncodeError as exc:
            raise self._name_error("名称只能包含 ASCII 字符") from exc

        segments = value.split("/")
        for index, segment in enumerate(segments):
            if not segment:
                raise self._name_error(f"名称第 {index + 1} 段不能为空")
            if segment in {".", ".."}:
                raise self._name_error(f"名称第 {index + 1} 段不能是 . 或 ..")
            if len(segment) > 64:
                raise self._name_error(f"名称第 {index + 1} 段不能超过 64 字符")
            if not _SAFE_SEGMENT_RE.match(segment):
                raise self._name_error(
                    f"名称第 {index + 1} 段只能以字母或数字开头和结尾"
                )
            device_name = segment.split(".", 1)[0].upper()
            if device_name in _WINDOWS_RESERVED_NAMES:
                raise self._name_error(f"名称第 {index + 1} 段使用平台保留名")
        return value

    def _name_error(self, message: str) -> WorktreeError:
        return WorktreeError(
            code="invalid_worktree_name",
            phase="pathing",
            message=message,
        )

    def _branch_error(self, message: str) -> WorktreeError:
        return WorktreeError(
            code="invalid_worktree_branch",
            phase="pathing",
            message=message,
        )

    def _boundary_error(self, message: str, *, path: Path | None = None) -> WorktreeError:
        return WorktreeError(
            code="worktree_path_boundary",
            phase="pathing",
            message=message,
            path=path if path is None or path.is_absolute() else path.absolute(),
        )

    def _repository_root(self) -> Path:
        return self._resolve_existing_directory("repository_root", self.repository_root)

    def _worktrees_root(self) -> Path:
        return self.validate_root(self.repository_root)

    def _resolve_existing_directory(self, field_name: str, value: Path) -> Path:
        if not isinstance(value, Path):
            raise self._boundary_error(f"{field_name} 必须是路径")
        if not value.is_absolute():
            raise self._boundary_error(f"{field_name} 必须是绝对路径", path=value)
        try:
            resolved = value.resolve(strict=True)
        except OSError as exc:
            raise self._boundary_error(f"{field_name} 无法解析", path=value) from exc
        if not resolved.is_dir():
            raise self._boundary_error(f"{field_name} 不是目录", path=value)
        return resolved

    def _join_relative_path(self, root: Path, raw: str, *, field_name: str) -> Path:
        if type(raw) is not str or not raw:
            raise self._boundary_error(f"{field_name} 不能为空")
        if "\\" in raw:
            raise self._boundary_error(f"{field_name} 不能包含反斜杠")
        candidate = Path(raw)
        if raw.startswith("/") or candidate.is_absolute() or re.match(r"^[A-Za-z]:", raw):
            raise self._boundary_error(f"{field_name} 边界检查失败：不能是绝对路径")
        if any(part in {"", ".", ".."} for part in candidate.parts):
            raise self._boundary_error(f"{field_name} 边界检查失败：不能包含空段、. 或 ..")
        return root / candidate

    def _assert_inside_root(
        self,
        target: Path,
        root: Path,
        *,
        field_name: str,
        phase: str,
        message_prefix: str,
    ) -> Path:
        if not isinstance(target, Path):
            raise self._boundary_error(f"{field_name} 必须是路径")
        if not target.is_absolute():
            raise self._boundary_error(f"{field_name} 必须是绝对路径", path=target)
        self._reject_reparse_ancestors(target, root)
        try:
            resolved = target.resolve(strict=False)
        except (OSError, RuntimeError) as exc:
            raise self._boundary_error(f"{message_prefix}：路径无法解析", path=target) from exc
        if not _is_relative_to(resolved, root):
            raise WorktreeError(
                code="worktree_path_boundary",
                phase=phase,
                message=f"{message_prefix}：路径越过允许边界",
                path=target,
            )
        return resolved

    def _reject_reparse_ancestors(self, target: Path, root: Path) -> None:
        current = target
        stop = root.parent
        while True:
            if current.exists() or current.is_symlink():
                if self._is_reparse_point(current):
                    raise self._boundary_error("路径边界检查失败：存在符号链接或重解析点", path=current)
            elif not _same_path(current, target):
                pass
            else:
                current = current.parent
                continue
            if _same_path(current, stop):
                return
            parent = current.parent
            if parent == current:
                return
            current = parent

    def _is_reparse_point(self, path: Path) -> bool:
        if path.is_symlink():
            return True
        try:
            attributes = path.stat(follow_symlinks=False).st_file_attributes
        except AttributeError:
            return False
        except OSError as exc:
            raise self._boundary_error("路径边界检查失败：无法读取路径状态", path=path) from exc
        return bool(attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT)


def _is_relative_to(child: Path, parent: Path) -> bool:
    try:
        child_text = os.path.normcase(str(child))
        parent_text = os.path.normcase(str(parent))
        return os.path.commonpath([child_text, parent_text]) == parent_text
    except ValueError:
        return False


def _same_path(left: Path, right: Path) -> bool:
    return os.path.normcase(str(left)) == os.path.normcase(str(right))
