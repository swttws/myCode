from __future__ import annotations

import shutil
from pathlib import Path
from typing import Protocol

from mycode.workspace import WorkspaceTaskIdentity
from mycode.worktree.models import (
    InitializationResult,
    WorktreeConfig,
    WorktreeError,
    WorktreeInitRule,
    WorktreeRuleType,
)
from mycode.worktree.pathing import WorktreePathPolicy


class IgnoreValidator(Protocol):
    def validate_ignored_root(self, path: Path) -> None: ...


class WorktreeInitializer:
    def __init__(self, *, path_policy: WorktreePathPolicy, git: IgnoreValidator) -> None:
        if not isinstance(path_policy, WorktreePathPolicy):
            raise ValueError("path_policy must be a WorktreePathPolicy")
        self._path_policy = path_policy
        self._git = git

    def initialize(
        self,
        identity: WorkspaceTaskIdentity,
        workspace_root: Path,
        config: WorktreeConfig,
    ) -> InitializationResult:
        if not isinstance(identity, WorkspaceTaskIdentity):
            raise ValueError("identity must be a WorkspaceTaskIdentity")
        if not isinstance(config, WorktreeConfig):
            raise ValueError("config must be a WorktreeConfig")
        workspace = self._path_policy.assert_target_boundary(workspace_root)
        completed: list[str] = []
        hooks_path: Path | None = None

        for index, rule in enumerate(config.rules):
            if rule.type in {WorktreeRuleType.COPY, WorktreeRuleType.IGNORED_COPY}:
                self._copy_rule(index, rule, workspace)
            elif rule.type is WorktreeRuleType.SYMLINK:
                self._symlink_rule(index, rule, workspace)
            elif rule.type is WorktreeRuleType.HOOKS:
                hooks_path = self._hooks_rule(index, rule, workspace)
            else:
                raise self._rule_error(index, rule, "规则类型尚未实现")
            completed.append(_rule_id(index, rule))

        return InitializationResult(
            completed_rules=tuple(completed),
            hooks_path=hooks_path,
        )

    def _copy_rule(self, index: int, rule: WorktreeInitRule, workspace: Path) -> None:
        source = self._source(index, rule)
        target = self._target(index, rule, workspace)
        if rule.type is WorktreeRuleType.IGNORED_COPY:
            self._validate_ignored(index, rule, source)
            self._validate_ignored(index, rule, target)
        self._copy_path(index, rule, source, target)
        self._source(index, rule)
        self._target(index, rule, workspace)

    def _symlink_rule(self, index: int, rule: WorktreeInitRule, workspace: Path) -> None:
        source = self._source(index, rule)
        target = self._target(index, rule, workspace)
        if not source.exists():
            raise self._rule_error(index, rule, "来源不存在", path=source)
        if target.exists() or target.is_symlink():
            raise self._rule_error(index, rule, "目标已存在", path=target)
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.symlink_to(source, target_is_directory=source.is_dir())
        except OSError as exc:
            raise self._rule_error(index, rule, "符号链接创建失败", path=target) from exc
        try:
            if not target.is_symlink() or target.resolve(strict=True) != source.resolve(strict=True):
                raise self._rule_error(index, rule, "符号链接目标不匹配", path=target)
        except OSError as exc:
            raise self._rule_error(index, rule, "符号链接解析失败", path=target) from exc

    def _hooks_rule(self, index: int, rule: WorktreeInitRule, workspace: Path) -> Path:
        source = self._source(index, rule)
        target = self._target(index, rule, workspace)
        if not source.exists():
            raise self._rule_error(index, rule, "hooks 来源不存在", path=source)
        if not source.is_dir():
            raise self._rule_error(index, rule, "hooks 来源必须是目录", path=source)
        if target.exists():
            raise self._rule_error(index, rule, "目标已存在", path=target)
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(source, target, copy_function=shutil.copy2)
        except OSError as exc:
            raise self._rule_error(index, rule, "hooks 复制失败", path=target) from exc
        self._source(index, rule)
        return self._target(index, rule, workspace)

    def _copy_path(self, index: int, rule: WorktreeInitRule, source: Path, target: Path) -> None:
        if not source.exists():
            raise self._rule_error(index, rule, "来源不存在", path=source)
        if target.exists():
            raise self._rule_error(index, rule, "目标已存在", path=target)
        try:
            if source.is_file():
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
                return
            if source.is_dir():
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copytree(source, target, copy_function=shutil.copy2)
                return
        except OSError as exc:
            raise self._rule_error(index, rule, "复制失败", path=target) from exc
        raise self._rule_error(index, rule, "来源类型不支持", path=source)

    def _validate_ignored(self, index: int, rule: WorktreeInitRule, path: Path) -> None:
        try:
            self._git.validate_ignored_root(path)
        except WorktreeError as exc:
            raise self._rule_error(index, rule, "Git 忽略检查失败", path=path) from exc

    def _source(self, index: int, rule: WorktreeInitRule) -> Path:
        try:
            return self._path_policy.resolve_rule_source(rule.source)
        except WorktreeError as exc:
            raise self._rule_error(index, rule, "来源边界检查失败", path=exc.path) from exc

    def _target(self, index: int, rule: WorktreeInitRule, workspace: Path) -> Path:
        try:
            return self._path_policy.resolve_rule_target(workspace, rule.target)
        except WorktreeError as exc:
            raise self._rule_error(index, rule, "目标边界检查失败", path=exc.path) from exc

    def _rule_error(
        self,
        index: int,
        rule: WorktreeInitRule,
        message: str,
        *,
        path: Path | None = None,
    ) -> WorktreeError:
        return WorktreeError(
            code="worktree_initialization_failed",
            phase="initializer",
            message=f"初始化规则 {index}:{rule.type.value} 失败：{message}",
            path=path,
        )


def _rule_id(index: int, rule: WorktreeInitRule) -> str:
    return f"{index}:{rule.type.value}:{rule.target}"
