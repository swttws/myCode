from __future__ import annotations

import asyncio
import hashlib
import os
import tempfile
from pathlib import Path
from typing import Any

import yaml

from mycode.permission.models import (
    ArgumentCondition,
    PermissionConfigError,
    PermissionEffect,
    PermissionFileConfig,
    PermissionMode,
    PermissionPaths,
    PermissionPersistenceError,
    PermissionRule,
    PermissionScalar,
    PermissionSessionState,
    RuleSource,
)


_RULE_FIELDS = {"id", "effect", "tool", "arguments"}
_GLOB_TOOL_CHARACTERS = set("*?[]")


def load_permission_file(path: str | Path, source: RuleSource) -> PermissionFileConfig:
    config_path = Path(path)
    if not config_path.exists():
        return PermissionFileConfig(version=1, mode=None, rules=())
    try:
        data = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise _error(config_path, "无法读取或解析 YAML") from exc
    return _parse_config(data, config_path, source)


def _parse_config(data: object, path: Path, source: RuleSource) -> PermissionFileConfig:
    if source is RuleSource.SESSION:
        raise _error(path, "会话规则不能从文件加载")
    if not isinstance(data, dict):
        raise _error(path, "配置根节点必须是 mapping")

    allowed_fields = {"version", "rules"}
    if source in (RuleSource.USER_GLOBAL, RuleSource.LOCAL_PROJECT):
        allowed_fields.add("mode")
    if source is RuleSource.LOCAL_PROJECT:
        allowed_fields.add("workspace")
    unknown = set(data) - allowed_fields
    if unknown:
        if source is RuleSource.REPOSITORY_PROJECT:
            # 仓库内容不受信任，未知授权字段必须阻止启动，不能静默忽略后继续运行。
            raise _error(path, f"仓库权限配置包含禁止字段: {sorted(unknown)}")
        raise _error(path, f"包含未知字段: {sorted(unknown)}")

    version = data.get("version")
    if type(version) is not int or version != 1:
        raise _error(path, "version 必须为 1")

    mode = _parse_mode(data.get("mode"), path)
    workspace = data.get("workspace")
    if workspace is not None and (not isinstance(workspace, str) or not workspace.strip()):
        raise _error(path, "workspace 必须是非空字符串")

    raw_rules = data.get("rules", [])
    if not isinstance(raw_rules, list):
        raise _error(path, "rules 必须是列表")
    rules = tuple(_parse_rule(item, index, path, source) for index, item in enumerate(raw_rules))
    _validate_rule_conflicts(rules, path)
    return PermissionFileConfig(version=1, mode=mode, rules=rules, workspace=workspace)


def _parse_mode(value: object, path: Path) -> PermissionMode | None:
    if value is None:
        return None
    try:
        return PermissionMode(value)
    except (TypeError, ValueError) as exc:
        raise _error(path, f"非法权限档位: {value!r}") from exc


def _parse_rule(item: object, index: int, path: Path, source: RuleSource) -> PermissionRule:
    location = f"rules[{index}]"
    if not isinstance(item, dict):
        raise _error(path, f"{location} 必须是 mapping")
    unknown = set(item) - _RULE_FIELDS
    if unknown:
        raise _error(path, f"{location} 包含未知字段: {sorted(unknown)}")

    rule_id = item.get("id")
    tool = item.get("tool")
    if not isinstance(rule_id, str) or not rule_id.strip():
        raise _error(path, f"{location}.id 必须是非空字符串")
    if not isinstance(tool, str) or not tool.strip():
        raise _error(path, f"{location}.tool 必须是非空字符串")
    if tool != "*" and any(character in tool for character in _GLOB_TOOL_CHARACTERS):
        raise _error(path, f"{location}.tool 只允许精确名称或单独的 *")

    try:
        effect = PermissionEffect(item.get("effect"))
    except (TypeError, ValueError) as exc:
        raise _error(path, f"{location}.effect 非法") from exc
    if effect is PermissionEffect.FORBIDDEN:
        raise _error(path, f"{location} 不能声明 forbidden")
    if source is RuleSource.REPOSITORY_PROJECT and effect is PermissionEffect.ALLOW:
        # 仓库随附策略只能收紧权限，绝不能把恶意仓库内容提升为本地用户授权。
        raise _error(path, f"仓库权限配置 {location} 不能声明 allow")

    raw_arguments = item.get("arguments", {})
    if not isinstance(raw_arguments, dict):
        raise _error(path, f"{location}.arguments 必须是 mapping")
    arguments: list[ArgumentCondition] = []
    for name, expected in raw_arguments.items():
        if not isinstance(name, str) or not name:
            raise _error(path, f"{location}.arguments 的名称必须是非空字符串")
        if not _is_scalar(expected):
            raise _error(path, f"{location}.arguments.{name} 必须是字符串、数字或布尔值")
        arguments.append(ArgumentCondition(name, expected))

    return PermissionRule(
        id=rule_id,
        effect=effect,
        tool=tool,
        arguments=tuple(arguments),
        source=source,
    )


def _is_scalar(value: object) -> bool:
    return isinstance(value, (str, int, float, bool)) and value is not None


def _validate_rule_conflicts(rules: tuple[PermissionRule, ...], path: Path) -> None:
    seen_ids: set[str] = set()
    conditions: dict[tuple[object, ...], PermissionEffect] = {}
    for rule in rules:
        if rule.id in seen_ids:
            raise _error(path, f"规则 ID 重复: {rule.id}")
        seen_ids.add(rule.id)
        key = _rule_condition_key(rule)
        previous = conditions.get(key)
        if previous is not None and previous is not rule.effect:
            raise _error(path, f"完全相同的规则条件存在冲突: {rule.id}")
        conditions[key] = rule.effect


def _rule_condition_key(rule: PermissionRule) -> tuple[object, ...]:
    values = tuple(
        sorted((condition.name, type(condition.expected).__name__, repr(condition.expected)) for condition in rule.arguments)
    )
    return (rule.tool, values)


def _same_rule_condition(first: PermissionRule, second: PermissionRule) -> bool:
    return _rule_condition_key(first) == _rule_condition_key(second)


def _error(path: Path, message: str) -> PermissionConfigError:
    return PermissionConfigError(f"权限配置 {path}: {message}")


class PermissionStore:
    def __init__(
        self,
        *,
        workspace_root: Path,
        paths: PermissionPaths,
        configs: dict[RuleSource, PermissionFileConfig],
    ) -> None:
        self._workspace_root = workspace_root
        self._workspace_text = workspace_root.as_posix()
        self._paths = paths
        self._configs = configs
        self._session = PermissionSessionState()

    @classmethod
    def load(
        cls,
        workspace_root: str | Path,
        *,
        home: str | Path | None = None,
    ) -> "PermissionStore":
        try:
            workspace = Path(workspace_root).resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise PermissionConfigError("无法确认权限工作区路径") from exc
        if not workspace.is_dir():
            raise PermissionConfigError("权限工作区必须是已存在目录")

        home_root = Path(home).expanduser().resolve(strict=False) if home is not None else Path.home().resolve()
        identity = os.path.normcase(str(workspace))
        digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()
        paths = PermissionPaths(
            user_global=home_root / ".mycode" / "permissions.yaml",
            local_project=home_root / ".mycode" / "projects" / digest / "permissions.yaml",
            repository_project=workspace / "mycode.permissions.yaml",
        )
        configs = {
            RuleSource.USER_GLOBAL: load_permission_file(paths.user_global, RuleSource.USER_GLOBAL),
            RuleSource.LOCAL_PROJECT: load_permission_file(paths.local_project, RuleSource.LOCAL_PROJECT),
            RuleSource.REPOSITORY_PROJECT: load_permission_file(
                paths.repository_project, RuleSource.REPOSITORY_PROJECT
            ),
        }
        local = configs[RuleSource.LOCAL_PROJECT]
        if paths.local_project.exists():
            if local.workspace is None:
                raise _error(paths.local_project, "本地项目授权缺少 workspace")
            if _normalize_workspace(local.workspace) != os.path.normcase(str(workspace)):
                raise _error(paths.local_project, "本地项目授权与当前工作区不匹配")
        return cls(workspace_root=workspace, paths=paths, configs=configs)

    @property
    def paths(self) -> PermissionPaths:
        return self._paths

    def rules_for(self, source: RuleSource) -> tuple[PermissionRule, ...]:
        if source is RuleSource.SESSION:
            return tuple(self._session.rules)
        return self._configs[source].rules

    def effective_mode(self) -> tuple[PermissionMode, RuleSource | None]:
        if self._session.mode_override is not None:
            return self._session.mode_override, RuleSource.SESSION
        local_mode = self._configs[RuleSource.LOCAL_PROJECT].mode
        if local_mode is not None:
            return local_mode, RuleSource.LOCAL_PROJECT
        global_mode = self._configs[RuleSource.USER_GLOBAL].mode
        if global_mode is not None:
            return global_mode, RuleSource.USER_GLOBAL
        return PermissionMode.DEFAULT, None

    def set_session_mode(self, mode: PermissionMode) -> None:
        if not isinstance(mode, PermissionMode):
            raise PermissionConfigError("会话权限档位非法")
        self._session.mode_override = mode

    def add_session_rule(self, rule: PermissionRule) -> None:
        if rule.source is not RuleSource.SESSION:
            raise PermissionConfigError("会话规则必须使用 session 来源")
        self._session.rules = _upsert_rule(self._session.rules, rule)

    async def persist_local_project_rule(self, rule: PermissionRule) -> None:
        if rule.source is not RuleSource.LOCAL_PROJECT:
            raise PermissionPersistenceError("项目授权保存失败：规则来源非法")
        await asyncio.to_thread(self._persist_local_project_rule, rule)

    def clear_session(self) -> None:
        self._session.reset()

    def _persist_local_project_rule(self, rule: PermissionRule) -> None:
        current = self._configs[RuleSource.LOCAL_PROJECT]
        try:
            rules = tuple(_upsert_rule(list(current.rules), rule))
        except PermissionConfigError as exc:
            raise PermissionPersistenceError("项目授权保存失败：规则 ID 冲突") from exc
        updated = PermissionFileConfig(
            version=1,
            mode=current.mode,
            rules=rules,
            workspace=self._workspace_text,
        )
        payload = _serialize_config(updated)
        target = self._paths.local_project
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                newline="\n",
                prefix=".permissions-",
                suffix=".tmp",
                dir=target.parent,
                delete=False,
            ) as stream:
                temporary_path = Path(stream.name)
                yaml.safe_dump(payload, stream, allow_unicode=True, sort_keys=False)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary_path, target)
            # 只有原子替换成功后才更新内存，否则当前调用可能误以为授权已经持久化。
            self._configs[RuleSource.LOCAL_PROJECT] = updated
        except Exception as exc:
            if temporary_path is not None:
                try:
                    temporary_path.unlink(missing_ok=True)
                except OSError:
                    pass
            raise PermissionPersistenceError("项目授权保存失败，当前操作未获准") from exc


def _upsert_rule(rules: list[PermissionRule], rule: PermissionRule) -> list[PermissionRule]:
    for index, existing in enumerate(rules):
        if existing.id != rule.id:
            continue
        if not _same_rule_condition(existing, rule):
            raise PermissionConfigError(f"规则 ID {rule.id} 对应不同条件")
        rules[index] = rule
        return rules
    rules.append(rule)
    return rules


def _serialize_config(config: PermissionFileConfig) -> dict[str, Any]:
    payload: dict[str, Any] = {"version": 1}
    if config.workspace is not None:
        payload["workspace"] = config.workspace
    if config.mode is not None:
        payload["mode"] = config.mode.value
    payload["rules"] = [
        {
            "id": rule.id,
            "effect": rule.effect.value,
            "tool": rule.tool,
            **(
                {"arguments": {condition.name: condition.expected for condition in rule.arguments}}
                if rule.arguments
                else {}
            ),
        }
        for rule in config.rules
    ]
    return payload


def _normalize_workspace(value: str) -> str:
    return os.path.normcase(str(Path(value).expanduser().resolve(strict=False)))
