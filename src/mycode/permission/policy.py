from __future__ import annotations

import copy
import fnmatch
import os
import re
from collections.abc import Iterable, Mapping
from types import MappingProxyType

from mycode.permission.command import CommandAnalyzer
from mycode.permission.config import PermissionStore
from mycode.permission.models import (
    CommandAssessment,
    PermissionDecision,
    PermissionEffect,
    PermissionEvaluationError,
    PermissionMode,
    PermissionRule,
    PermissionScalar,
    PermissionSubject,
    RuleMatch,
    RuleSource,
    RuleSpecificity,
)
from mycode.permission.pathing import PathGuard
from mycode.tool.base import ToolCall, ToolDefinition, ToolKind


_SOURCE_PRIORITY = (
    RuleSource.SESSION,
    RuleSource.LOCAL_PROJECT,
    RuleSource.REPOSITORY_PROJECT,
    RuleSource.USER_GLOBAL,
)
_GLOB_CHARACTERS = set("*?[")
_SENSITIVE_KEY = re.compile(
    r"api_?key|apikey|token|password|passwd|secret|credential", re.IGNORECASE
)
_BODY_KEYS = {"text", "old_text", "new_text", "content", "body"}
_REDACTED = "<已脱敏>"
_OMITTED = "<内容已省略>"
_TRUNCATED = "...（已截断）"
_DISPLAY_LIMIT = 512


def build_subject(
    call: ToolCall,
    definition: ToolDefinition,
    path_guard: PathGuard,
) -> PermissionSubject:
    if call.name != definition.name:
        raise PermissionEvaluationError("工具调用名称与定义不一致")
    if not isinstance(call.arguments, dict):
        raise PermissionEvaluationError("工具参数不是合法 JSON object")

    arguments = copy.deepcopy(call.arguments)
    properties = definition.parameters.get("properties", {})
    required = definition.parameters.get("required", [])
    if not isinstance(properties, dict) or not isinstance(required, list):
        raise PermissionEvaluationError("工具参数契约无效")
    for name in required:
        if name not in arguments:
            raise PermissionEvaluationError(f"缺少必填工具参数: {name}")
    for name, value in arguments.items():
        schema = properties.get(name)
        if isinstance(schema, dict):
            _validate_schema_type(name, value, schema.get("type"))

    for grant_name in definition.grant_arguments:
        if grant_name == "root" and grant_name not in arguments:
            arguments[grant_name] = "."

    normalized: dict[str, object] = {}
    display_source: dict[str, object] = {}
    for name, value in arguments.items():
        if name in {"path", "root"} and isinstance(value, str):
            guarded = path_guard.inspect(value)
            normalized[name] = guarded.match_value
            display_source[name] = guarded.relative
        elif name == "command" and isinstance(value, str):
            command = _normalize_command(value)
            normalized[name] = command
            display_source[name] = command
        else:
            normalized[name] = copy.deepcopy(value)
            display_source[name] = copy.deepcopy(value)

    grants: dict[str, PermissionScalar] = {}
    for name in definition.grant_arguments:
        value = normalized.get(name)
        if _is_permission_scalar(value):
            grants[name] = value

    display = {
        name: _redact_value(name, value)
        for name, value in display_source.items()
    }
    return PermissionSubject(
        call=call,
        definition=definition,
        normalized_arguments=MappingProxyType(copy.deepcopy(normalized)),
        grant_arguments=MappingProxyType(copy.deepcopy(grants)),
        display_arguments=MappingProxyType(copy.deepcopy(display)),
    )


def match_rule(
    rule: PermissionRule,
    tool_name: str,
    arguments: Mapping[str, object],
) -> RuleMatch | None:
    if rule.tool not in {"*", tool_name}:
        return None
    exact_arguments = 0
    for condition in rule.arguments:
        if condition.name not in arguments:
            return None
        actual = arguments[condition.name]
        expected = _normalize_condition_value(condition.name, condition.expected)
        if isinstance(expected, str):
            if not isinstance(actual, str):
                return None
            if _has_glob(expected):
                if not fnmatch.fnmatchcase(actual, expected):
                    return None
            else:
                if actual != expected:
                    return None
                exact_arguments += 1
        else:
            if isinstance(expected, bool):
                if not isinstance(actual, bool) or actual is not expected:
                    return None
            elif isinstance(actual, bool) or actual != expected:
                return None
            exact_arguments += 1
    return RuleMatch(
        rule=rule,
        specificity=RuleSpecificity(
            exact_tool=1 if rule.tool != "*" else 0,
            constrained_arguments=len(rule.arguments),
            exact_arguments=exact_arguments,
        ),
    )


def select_rule(
    rules: Iterable[PermissionRule],
    tool_name: str,
    arguments: Mapping[str, object],
) -> RuleMatch | None:
    matches = [
        matched
        for rule in rules
        if (matched := match_rule(rule, tool_name, arguments)) is not None
    ]
    if not matches:
        return None
    effect_rank = {
        PermissionEffect.ALLOW: 0,
        PermissionEffect.ASK: 1,
        PermissionEffect.DENY: 2,
        PermissionEffect.FORBIDDEN: 3,
    }
    # 同具体度优先拒绝，再用规则 ID 稳定收敛，避免声明顺序影响安全结果。
    matches.sort(
        key=lambda match: (
            -match.specificity.exact_tool,
            -match.specificity.constrained_arguments,
            -match.specificity.exact_arguments,
            -effect_rank[match.rule.effect],
            match.rule.id,
        )
    )
    return matches[0]


class PermissionPolicy:
    def __init__(
        self,
        *,
        store: PermissionStore,
        path_guard: PathGuard,
        command_analyzer: CommandAnalyzer,
    ) -> None:
        self._store = store
        self._path_guard = path_guard
        self._command_analyzer = command_analyzer

    def evaluate(
        self,
        call: ToolCall,
        definition: ToolDefinition,
        *,
        plan_only: bool,
    ) -> tuple[PermissionSubject, PermissionDecision]:
        subject = build_subject(call, definition, self._path_guard)
        mode, _mode_source = self._store.effective_mode()
        assessment = self._assess_command(subject)

        # FORBIDDEN 必须在所有可配置规则之前终止，任何档位和人工授权都不能覆盖安全底线。
        if assessment.effect is PermissionEffect.FORBIDDEN:
            return subject, _decision_from_assessment(subject, assessment, mode)
        if assessment.effect is PermissionEffect.DENY:
            return subject, _decision_from_assessment(subject, assessment, mode)

        selected = self._select_source_rule(subject)
        if assessment.effect is PermissionEffect.ASK:
            if selected is not None and selected.rule.effect in {
                PermissionEffect.DENY,
                PermissionEffect.ASK,
            }:
                decision = _decision_from_rule(subject, selected.rule, mode, assessment.category)
            elif selected is not None and _is_precise_allow(selected.rule, subject):
                decision = _decision_from_rule(subject, selected.rule, mode, assessment.category)
            else:
                # 高风险分类只能由当前调用的精确授权满足，宽泛规则和 permissive 不能静默放大风险。
                decision = _decision_from_assessment(subject, assessment, mode)
        elif selected is not None:
            decision = _decision_from_rule(subject, selected.rule, mode, assessment.category)
        else:
            decision = _fallback_decision(subject, mode)

        if plan_only and definition.kind is ToolKind.WRITE:
            decision = _apply_plan_only(decision)
        return subject, decision

    def _assess_command(self, subject: PermissionSubject) -> CommandAssessment:
        if subject.definition.name != "run_command":
            return CommandAssessment(PermissionEffect.ALLOW, None, None, None)
        command = subject.normalized_arguments.get("command")
        if not isinstance(command, str):
            return CommandAssessment(
                PermissionEffect.DENY,
                "security_failure",
                "security_check_failed",
                "命令安全检查失败，已拒绝执行。",
            )
        try:
            return self._command_analyzer.assess(command)
        except Exception:
            return CommandAssessment(
                PermissionEffect.DENY,
                "security_failure",
                "security_check_failed",
                "命令安全检查失败，已拒绝执行。",
            )

    def _select_source_rule(self, subject: PermissionSubject) -> RuleMatch | None:
        for source in _SOURCE_PRIORITY:
            selected = select_rule(
                self._store.rules_for(source),
                subject.call.name,
                subject.normalized_arguments,
            )
            if selected is not None:
                # 首个存在匹配项的来源即终止查找，防止较低来源参与合并后产生隐蔽放宽。
                return selected
        return None


def _validate_schema_type(name: str, value: object, expected: object) -> None:
    valid = True
    if expected == "string":
        valid = isinstance(value, str)
    elif expected == "number":
        valid = isinstance(value, (int, float)) and not isinstance(value, bool)
    elif expected == "integer":
        valid = isinstance(value, int) and not isinstance(value, bool)
    elif expected == "boolean":
        valid = isinstance(value, bool)
    elif expected == "object":
        valid = isinstance(value, dict)
    elif expected == "array":
        valid = isinstance(value, list)
    if not valid:
        raise PermissionEvaluationError(f"工具参数 {name} 类型不符合契约")


def _normalize_command(command: str) -> str:
    result: list[str] = []
    quote: str | None = None
    escaped = False
    pending_space = False
    for character in command.strip():
        if escaped:
            result.append(character)
            escaped = False
            continue
        if character in {"\\", "^", "`"}:
            if pending_space and result:
                result.append(" ")
                pending_space = False
            result.append(character)
            escaped = True
            continue
        if quote is not None:
            result.append(character)
            if character == quote:
                quote = None
            continue
        if character in {"'", '"'}:
            if pending_space and result:
                result.append(" ")
                pending_space = False
            quote = character
            result.append(character)
        elif character.isspace():
            pending_space = True
        else:
            if pending_space and result:
                result.append(" ")
            pending_space = False
            result.append(character)
    return "".join(result)


def _redact_value(name: str, value: object) -> object:
    if name.lower() in _BODY_KEYS:
        return _OMITTED
    if _SENSITIVE_KEY.search(name):
        return _REDACTED
    if isinstance(value, str):
        return _truncate(_redact_string(value))
    if isinstance(value, dict):
        return {key: _redact_value(str(key), item) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact_value(name, item) for item in value]
    return value


def _redact_string(value: str) -> str:
    value = re.sub(r"(?i)(https?://)[^/@\s]+@", rf"\1{_REDACTED}@", value)
    value = re.sub(
        r"(?i)([?&](?:access_token|api_key|apikey|token|password|secret)=)[^&\s]+",
        rf"\1{_REDACTED}",
        value,
    )
    value = re.sub(
        r"(?i)(--(?:api-key|apikey|token|password|secret)(?:=|\s+))([^\s]+)",
        rf"\1{_REDACTED}",
        value,
    )
    value = re.sub(
        r"(?i)\b([A-Z_][A-Z0-9_]*(?:API_KEY|TOKEN|PASSWORD|PASSWD|SECRET|CREDENTIAL)[A-Z0-9_]*)=([^\s]+)",
        rf"\1={_REDACTED}",
        value,
    )
    return value


def _truncate(value: str) -> str:
    if len(value) <= _DISPLAY_LIMIT:
        return value
    return value[: _DISPLAY_LIMIT - len(_TRUNCATED)] + _TRUNCATED


def _normalize_condition_value(name: str, value: PermissionScalar) -> PermissionScalar:
    if name in {"path", "root"} and isinstance(value, str):
        return os.path.normcase(value).replace("\\", "/")
    if name == "command" and isinstance(value, str):
        return _normalize_command(value)
    return value


def _has_glob(value: str) -> bool:
    return any(character in value for character in _GLOB_CHARACTERS)


def _is_permission_scalar(value: object) -> bool:
    return isinstance(value, (str, int, float, bool)) and value is not None


def _is_precise_allow(rule: PermissionRule, subject: PermissionSubject) -> bool:
    if rule.effect is not PermissionEffect.ALLOW or rule.tool != subject.call.name:
        return False
    if {condition.name for condition in rule.arguments} != set(subject.grant_arguments):
        return False
    return all(
        not isinstance(condition.expected, str) or not _has_glob(condition.expected)
        for condition in rule.arguments
    )


def _decision_from_assessment(
    subject: PermissionSubject,
    assessment: CommandAssessment,
    mode: PermissionMode,
) -> PermissionDecision:
    return PermissionDecision(
        effect=assessment.effect,
        reason_code=assessment.reason_code or "security_check_failed",
        message_zh=assessment.message_zh or "安全检查未能完成，已拒绝执行。",
        mode=mode,
        display_arguments=subject.display_arguments,
        risk_category=assessment.category,
    )


def _decision_from_rule(
    subject: PermissionSubject,
    rule: PermissionRule,
    mode: PermissionMode,
    risk_category: str | None,
) -> PermissionDecision:
    reasons = {
        PermissionEffect.ALLOW: ("rule_allow", "调用已由权限规则允许。"),
        PermissionEffect.ASK: ("rule_requires_approval", "权限规则要求人工确认。"),
        PermissionEffect.DENY: ("permission_denied", "调用已被权限规则拒绝。"),
        PermissionEffect.FORBIDDEN: ("forbidden_operation", "调用命中不可覆盖的安全底线。"),
    }
    reason_code, message = reasons[rule.effect]
    return PermissionDecision(
        effect=rule.effect,
        reason_code=reason_code,
        message_zh=message,
        mode=mode,
        display_arguments=subject.display_arguments,
        source=rule.source,
        rule_id=rule.id,
        risk_category=risk_category,
    )


def _fallback_decision(subject: PermissionSubject, mode: PermissionMode) -> PermissionDecision:
    if mode is PermissionMode.STRICT:
        effect = PermissionEffect.ASK
        reason = "strict_mode_requires_approval"
        message = "严格模式要求此工具调用先经人工确认。"
    elif mode is PermissionMode.DEFAULT and subject.definition.kind is ToolKind.WRITE:
        effect = PermissionEffect.ASK
        reason = "default_write_requires_approval"
        message = "默认模式要求写入或命令工具先经人工确认。"
    else:
        effect = PermissionEffect.ALLOW
        reason = "default_read_allowed" if mode is PermissionMode.DEFAULT else "permissive_mode_allowed"
        message = "当前权限档位允许此工具调用。"
    return PermissionDecision(
        effect=effect,
        reason_code=reason,
        message_zh=message,
        mode=mode,
        display_arguments=subject.display_arguments,
    )


def _apply_plan_only(decision: PermissionDecision) -> PermissionDecision:
    if decision.effect is not PermissionEffect.ALLOW:
        return decision
    # plan-only 是独立任务约束，普通授权只能改变常规权限，不能绕过当前只规划模式。
    return PermissionDecision(
        effect=PermissionEffect.ASK,
        reason_code="plan_only_write",
        message_zh="当前处于只规划模式，写工具必须进行本次确认。",
        mode=decision.mode,
        display_arguments=decision.display_arguments,
        source=decision.source,
        rule_id=decision.rule_id,
        risk_category=decision.risk_category,
    )
