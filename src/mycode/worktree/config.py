from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

import yaml

from mycode.worktree.models import (
    WorktreeConfig,
    WorktreeError,
    WorktreeInitRule,
    WorktreeRuleType,
)
from mycode.worktree.pathing import WorktreePathPolicy


_TOP_LEVEL_FIELDS = frozenset({"version", "git_timeout_seconds", "cleanup", "rules"})
_CLEANUP_FIELDS = frozenset(
    {"interval_seconds", "expire_after_seconds", "scan_batch_size"}
)
_RULE_FIELDS = frozenset({"type", "source", "target"})
_MAX_RULES = 128


class WorktreeConfigLoader:
    def load(self, repository_root: Path) -> WorktreeConfig:
        if not isinstance(repository_root, Path):
            raise self._error("repository_root 必须是路径")
        if not repository_root.is_absolute():
            raise self._error("repository_root 必须是绝对路径")

        config_path = repository_root / ".mycode" / "worktree.yaml"
        if not config_path.exists():
            return self._build_config(_default_payload())

        try:
            raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        except yaml.YAMLError as exc:
            raise self._error("Worktree 配置 YAML 无法解析", path=config_path) from exc
        except OSError as exc:
            raise self._error("Worktree 配置无法读取", path=config_path) from exc

        if raw is None:
            raw = {}
        if not isinstance(raw, dict):
            raise self._error("Worktree 配置必须是 mapping", path=config_path)
        _reject_unknown_fields(raw, _TOP_LEVEL_FIELDS, "Worktree 配置")

        payload = _default_payload()
        if "version" in raw:
            payload["version"] = _parse_int(raw["version"], "version", minimum=1, maximum=1)
        if "git_timeout_seconds" in raw:
            payload["git_timeout_seconds"] = _parse_number(
                raw["git_timeout_seconds"],
                "git_timeout_seconds",
                minimum=1.0,
                maximum=120.0,
            )
        if "cleanup" in raw:
            cleanup = raw["cleanup"]
            if not isinstance(cleanup, dict):
                raise self._error("cleanup 必须是 mapping", path=config_path)
            _reject_unknown_fields(cleanup, _CLEANUP_FIELDS, "cleanup")
            if "interval_seconds" in cleanup:
                payload["cleanup"]["interval_seconds"] = _parse_number(
                    cleanup["interval_seconds"],
                    "cleanup.interval_seconds",
                    minimum=1.0,
                )
            if "expire_after_seconds" in cleanup:
                payload["cleanup"]["expire_after_seconds"] = _parse_number(
                    cleanup["expire_after_seconds"],
                    "cleanup.expire_after_seconds",
                    minimum=1.0,
                )
            if "scan_batch_size" in cleanup:
                payload["cleanup"]["scan_batch_size"] = _parse_int(
                    cleanup["scan_batch_size"],
                    "cleanup.scan_batch_size",
                    minimum=1,
                    maximum=64,
                )
        if "rules" in raw:
            rules = raw["rules"]
            if not isinstance(rules, list):
                raise self._error("rules 必须是 list", path=config_path)
            if len(rules) > _MAX_RULES:
                raise self._error(f"rules 不能超过 {_MAX_RULES} 条", path=config_path)
            payload["rules"] = [_parse_rule(rule, index) for index, rule in enumerate(rules)]
        self._validate_rules(repository_root, payload["rules"], config_path)

        return self._build_config(payload)

    def _build_config(self, payload: dict[str, Any]) -> WorktreeConfig:
        digest = hashlib.sha256(_canonical_config_text(payload).encode("utf-8")).hexdigest()
        cleanup = payload["cleanup"]
        return WorktreeConfig(
            version=payload["version"],
            rules=tuple(
                WorktreeInitRule(
                    type=WorktreeRuleType(rule["type"]),
                    source=rule["source"],
                    target=rule["target"],
                )
                for rule in payload["rules"]
            ),
            git_timeout_seconds=payload["git_timeout_seconds"],
            cleanup_interval_seconds=cleanup["interval_seconds"],
            expire_after_seconds=cleanup["expire_after_seconds"],
            scan_batch_size=cleanup["scan_batch_size"],
            digest=digest,
        )

    def _error(self, message: str, *, path: Path | None = None) -> WorktreeError:
        return WorktreeError(
            code="invalid_worktree_config",
            phase="config",
            message=message,
            path=path,
        )

    def _validate_rules(
        self,
        repository_root: Path,
        rules: list[dict[str, str]],
        config_path: Path,
    ) -> None:
        policy = WorktreePathPolicy(repository_root=repository_root)
        targets: list[tuple[int, str, Path]] = []
        hooks_index: int | None = None
        for index, rule in enumerate(rules):
            self._validate_rule_source(policy, rule["source"], index, config_path)
            target_path = self._validate_rule_target(policy, rule["target"], index, config_path)

            if rule["type"] == WorktreeRuleType.HOOKS.value:
                if hooks_index is not None:
                    raise self._error(
                        f"rules[{index}] 与 rules[{hooks_index}] hooks 规则冲突",
                        path=config_path,
                    )
                hooks_index = index

            target_key = _path_key(target_path)
            for previous_index, previous_key, previous_path in targets:
                if _targets_conflict(previous_key, target_key):
                    raise self._error(
                        (
                            f"rules[{index}].target 与 "
                            f"rules[{previous_index}].target 冲突"
                        ),
                        path=target_path if target_path.is_absolute() else previous_path,
                    )
            targets.append((index, target_key, target_path))

    def _validate_rule_source(
        self,
        policy: WorktreePathPolicy,
        source: str,
        index: int,
        config_path: Path,
    ) -> None:
        try:
            policy.resolve_rule_source(source)
        except WorktreeError as exc:
            raise self._error(
                f"rules[{index}].source 边界检查失败：{exc.message}",
                path=exc.path or config_path,
            ) from exc

    def _validate_rule_target(
        self,
        policy: WorktreePathPolicy,
        target: str,
        index: int,
        config_path: Path,
    ) -> Path:
        try:
            return policy.resolve_config_rule_target(target)
        except WorktreeError as exc:
            raise self._error(
                f"rules[{index}].target 边界检查失败：{exc.message}",
                path=exc.path or config_path,
            ) from exc


def _default_payload() -> dict[str, Any]:
    return {
        "version": 1,
        "git_timeout_seconds": 30.0,
        "cleanup": {
            "interval_seconds": 3600.0,
            "expire_after_seconds": 604800.0,
            "scan_batch_size": 64,
        },
        "rules": [],
    }


def _reject_unknown_fields(raw: dict[Any, Any], allowed: frozenset[str], location: str) -> None:
    unknown = [key for key in raw if key not in allowed]
    if unknown:
        raise WorktreeError(
            code="invalid_worktree_config",
            phase="config",
            message=f"{location} 包含未知字段 {unknown[0]}",
        )


def _parse_rule(raw: Any, index: int) -> dict[str, str]:
    if not isinstance(raw, dict):
        raise WorktreeError(
            code="invalid_worktree_config",
            phase="config",
            message=f"rules[{index}] 必须是 mapping",
        )
    _reject_unknown_fields(raw, _RULE_FIELDS, f"rules[{index}]")
    try:
        rule_type = WorktreeRuleType(raw["type"]).value
    except KeyError as exc:
        raise WorktreeError(
            code="invalid_worktree_config",
            phase="config",
            message=f"rules[{index}].type 缺失",
        ) from exc
    except ValueError as exc:
        raise WorktreeError(
            code="invalid_worktree_config",
            phase="config",
            message=f"rules[{index}].type 未知",
        ) from exc
    source = _parse_non_empty_string(raw.get("source"), f"rules[{index}].source")
    target = _parse_non_empty_string(raw.get("target"), f"rules[{index}].target")
    return {"type": rule_type, "source": source, "target": target}


def _parse_non_empty_string(value: Any, field_name: str) -> str:
    if type(value) is not str or not value:
        raise WorktreeError(
            code="invalid_worktree_config",
            phase="config",
            message=f"{field_name} 必须是非空字符串",
        )
    if len(value) > 512:
        raise WorktreeError(
            code="invalid_worktree_config",
            phase="config",
            message=f"{field_name} 不能超过 512 字符",
        )
    return value


def _parse_number(
    value: Any,
    field_name: str,
    *,
    minimum: float,
    maximum: float | None = None,
) -> float:
    if type(value) not in (int, float):
        raise WorktreeError(
            code="invalid_worktree_config",
            phase="config",
            message=f"{field_name} 必须是数字",
        )
    if value < minimum:
        raise WorktreeError(
            code="invalid_worktree_config",
            phase="config",
            message=f"{field_name} 不能小于 {minimum:g}",
        )
    if maximum is not None and value > maximum:
        raise WorktreeError(
            code="invalid_worktree_config",
            phase="config",
            message=f"{field_name} 不能大于 {maximum:g}",
        )
    return float(value)


def _parse_int(value: Any, field_name: str, *, minimum: int, maximum: int) -> int:
    if type(value) is not int:
        raise WorktreeError(
            code="invalid_worktree_config",
            phase="config",
            message=f"{field_name} 必须是整数",
        )
    if value < minimum or value > maximum:
        raise WorktreeError(
            code="invalid_worktree_config",
            phase="config",
            message=f"{field_name} 必须在 {minimum} 到 {maximum} 范围内",
        )
    return value


def _path_key(path: Path) -> str:
    return os.path.normcase(str(path))


def _targets_conflict(left: str, right: str) -> bool:
    try:
        common = os.path.commonpath([left, right])
    except ValueError:
        return False
    return common == left or common == right


def _canonical_config_text(payload: dict[str, Any]) -> str:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
