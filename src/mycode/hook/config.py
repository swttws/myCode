from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml

from mycode.hook.models import (
    HookAction,
    HookActionType,
    HookCondition,
    HookConfig,
    HookConfigError,
    HookEvent,
    HookPredicate,
    HookRule,
)
from mycode.hook.matcher import parse_matcher
from mycode.team.tooling.tool_names import LEGACY_TEAM_TOOL_NAMES


_TOP_LEVEL_FIELDS = {"version", "hooks"}
_RULE_FIELDS = {"id", "event", "if", "action", "once", "background", "timeout_seconds"}
_ACTION_FIELDS = {
    HookActionType.COMMAND: {"type", "command", "cwd", "env", "block", "reason"},
    HookActionType.PROMPT: {"type", "content", "block", "reason"},
    HookActionType.HTTP: {"type", "method", "url", "headers", "json", "block", "reason"},
    HookActionType.SUB_AGENT: {"type", "task", "input", "output", "block", "reason"},
}


def load_hook_file(path: str | Path) -> HookConfig:
    hook_path = Path(path)
    if not hook_path.exists():
        return HookConfig(version=1, rules=(), path=None)
    try:
        raw = yaml.safe_load(hook_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise HookConfigError(f"Hook YAML 解析失败: {hook_path}: {exc}") from exc
    return _parse_config(raw, path=hook_path)


def load_hook_config(
    *,
    workspace_root: Path,
    explicit_path: Path | None = None,
) -> HookConfig:
    path = explicit_path or workspace_root / "mycode.hooks.yaml"
    if explicit_path is not None and not path.exists():
        raise HookConfigError(f"Hook 配置文件不存在: {path}")
    return load_hook_file(path)


def _parse_config(raw: object, *, path: Path) -> HookConfig:
    if raw is None:
        raw = {}
    if not isinstance(raw, Mapping):
        raise HookConfigError("Hook 配置顶层必须是 mapping")
    _reject_unknown_fields(raw, _TOP_LEVEL_FIELDS, "Hook 配置顶层")
    version = raw.get("version")
    if isinstance(version, bool) or version != 1:
        raise HookConfigError("Hook 配置 version 必须为 1")
    hooks = raw.get("hooks", [])
    if hooks is None:
        hooks = []
    if not isinstance(hooks, list):
        raise HookConfigError("Hook 配置 hooks 必须是列表")

    rules: list[HookRule] = []
    seen_ids: set[str] = set()
    for index, item in enumerate(hooks):
        rule = _parse_rule(item, index=index)
        if rule.id in seen_ids:
            raise HookConfigError(f"Hook 规则 ID 重复: {rule.id}")
        seen_ids.add(rule.id)
        rules.append(rule)
    return HookConfig(version=1, rules=tuple(rules), path=path)


def _parse_rule(raw: object, *, index: int) -> HookRule:
    location = f"hooks[{index}]"
    if not isinstance(raw, Mapping):
        raise HookConfigError(f"{location} 必须是 mapping")
    _reject_unknown_fields(raw, _RULE_FIELDS, location)

    event_value = raw.get("event")
    if not isinstance(event_value, str) or not event_value:
        raise HookConfigError(f"{location}.event 必须声明")
    try:
        event = HookEvent(event_value)
    except ValueError as exc:
        raise HookConfigError(f"{location}.event 未知事件: {event_value}") from exc

    action = _parse_action(raw.get("action"), event=event, location=f"{location}.action")
    condition = _parse_condition(raw.get("if"), location=f"{location}.if")
    rule_id = raw.get("id")
    if rule_id is None:
        rule_id = f"hook-{index + 1}"
    if not isinstance(rule_id, str) or not rule_id.strip():
        raise HookConfigError(f"{location}.id 必须是非空字符串")

    once = _optional_bool(raw, "once", location, default=False)
    background = _optional_bool(raw, "background", location, default=False)
    if event is HookEvent.TOOL_BEFORE and background:
        # 工具前拦截必须同步决定，不能把阻断结果交给后台任务。
        raise HookConfigError(f"{location}.tool_before 不允许 background: true")
    timeout = _optional_timeout(raw, location)
    return HookRule(
        id=rule_id,
        event=event,
        condition=condition,
        action=action,
        once=once,
        background=background,
        timeout_seconds=timeout,
        index=index,
    )


def _parse_condition(raw: object, *, location: str) -> HookCondition | None:
    if raw is None:
        return None
    if not isinstance(raw, Mapping):
        raise HookConfigError(f"{location} 必须是 mapping")
    keys = set(raw)
    if keys not in ({"all"}, {"any"}):
        raise HookConfigError(f"{location} 只能声明 all 或 any")
    mode = "all" if "all" in raw else "any"
    predicates_raw = raw[mode]
    if not isinstance(predicates_raw, Mapping) or not predicates_raw:
        raise HookConfigError(f"{location}.{mode} 必须是非空 mapping")
    predicates = tuple(
        _parse_predicate(field, value, location=f"{location}.{mode}")
        for field, value in predicates_raw.items()
    )
    return HookCondition(mode=mode, predicates=predicates)


def _parse_predicate(field: object, value: object, *, location: str) -> HookPredicate:
    field_name = _parse_field_name(field, location=location)
    if field_name == "tool" and isinstance(value, str) and value in LEGACY_TEAM_TOOL_NAMES:
        raise HookConfigError(f"{location}.tool 使用了已移除的旧团队工具 {value}，请改用新的 team_* 工具名")
    return HookPredicate(field=field_name, matcher=parse_matcher(value, location=f"{location}.{field_name}"))


def _parse_action(raw: object, *, event: HookEvent, location: str) -> HookAction:
    if not isinstance(raw, Mapping):
        raise HookConfigError(f"{location} 必须声明")
    type_value = raw.get("type")
    if not isinstance(type_value, str) or not type_value:
        raise HookConfigError(f"{location}.type 必须声明")
    try:
        action_type = HookActionType(type_value)
    except ValueError as exc:
        raise HookConfigError(f"{location}.type 未知动作: {type_value}") from exc
    _reject_unknown_fields(raw, _ACTION_FIELDS[action_type], location)

    block = _optional_bool(raw, "block", location, default=False)
    if block and event is not HookEvent.TOOL_BEFORE:
        raise HookConfigError(f"{location}.block 只能用于 tool_before")
    reason = _optional_str(raw, "reason", location)

    if action_type is HookActionType.PROMPT:
        content = _required_str(raw, "content", location)
        return HookAction(type=action_type, content=content, block=block, reason=reason)
    if action_type is HookActionType.COMMAND:
        command = _required_str(raw, "command", location)
        return HookAction(
            type=action_type,
            command=command,
            cwd=_optional_str(raw, "cwd", location),
            env=_optional_str_mapping(raw, "env", location),
            block=block,
            reason=reason,
        )
    if action_type is HookActionType.HTTP:
        return HookAction(
            type=action_type,
            method=_optional_str(raw, "method", location) or "POST",
            url=_required_str(raw, "url", location),
            headers=_optional_str_mapping(raw, "headers", location),
            json_body=_optional_mapping(raw, "json", location),
            block=block,
            reason=reason,
        )
    task = _required_str(raw, "task", location)
    return HookAction(
        type=action_type,
        task=task,
        input=_optional_mapping(raw, "input", location),
        output=_optional_str(raw, "output", location),
        block=block,
        reason=reason,
    )


def _reject_unknown_fields(raw: Mapping[object, object], allowed: set[str], location: str) -> None:
    for key in raw:
        if key not in allowed:
            raise HookConfigError(f"{location} 包含未知字段: {key}")


def _parse_field_name(value: object, *, location: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise HookConfigError(f"{location} 条件字段必须是非空字符串")
    if value in {"all", "any"}:
        raise HookConfigError(f"{location} 不支持嵌套逻辑: {value}")
    return value


def _optional_bool(
    raw: Mapping[str, object],
    field: str,
    location: str,
    *,
    default: bool,
) -> bool:
    value = raw.get(field, default)
    if not isinstance(value, bool):
        raise HookConfigError(f"{location}.{field} 必须是布尔值")
    return value


def _optional_timeout(raw: Mapping[str, object], location: str) -> float | None:
    value = raw.get("timeout_seconds")
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        raise HookConfigError(f"{location}.timeout_seconds 必须大于 0")
    return float(value)


def _required_str(raw: Mapping[str, object], field: str, location: str) -> str:
    value = raw.get(field)
    if not isinstance(value, str) or not value:
        raise HookConfigError(f"{location}.{field} 必须声明")
    return value


def _optional_str(raw: Mapping[str, object], field: str, location: str) -> str | None:
    value = raw.get(field)
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise HookConfigError(f"{location}.{field} 必须是非空字符串")
    return value


def _optional_str_mapping(raw: Mapping[str, object], field: str, location: str) -> dict[str, str]:
    value = raw.get(field)
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise HookConfigError(f"{location}.{field} 必须是 mapping")
    result: dict[str, str] = {}
    for key, item in value.items():
        if not isinstance(key, str) or not isinstance(item, str):
            raise HookConfigError(f"{location}.{field} 的键和值必须是字符串")
        result[key] = item
    return result


def _optional_mapping(raw: Mapping[str, object], field: str, location: str) -> dict[str, Any] | None:
    value = raw.get(field)
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise HookConfigError(f"{location}.{field} 必须是 mapping")
    return dict(value)
