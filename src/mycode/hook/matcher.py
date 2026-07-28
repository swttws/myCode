from __future__ import annotations

import fnmatch
import re
from collections.abc import Mapping

from mycode.hook.models import (
    HookCondition,
    HookConfigError,
    HookContext,
    MatchKind,
    ValueMatcher,
)


_GLOB_CHARACTERS = set("*?[")


def parse_matcher(value: object, *, location: str) -> ValueMatcher:
    if isinstance(value, Mapping):
        return _parse_mapping_matcher(value, location=location)
    if isinstance(value, str):
        negate = value.startswith("!")
        body = value[1:] if negate else value
        if not body:
            raise HookConfigError(f"{location} 匹配值不能为空")
        if body.startswith("re:"):
            pattern = body[3:]
            _compile_regex(pattern, location=location)
            return ValueMatcher(MatchKind.REGEX, pattern, negate)
        if body.startswith("glob:"):
            pattern = body[5:]
            if not pattern:
                raise HookConfigError(f"{location} glob 不能为空")
            return ValueMatcher(MatchKind.GLOB, pattern, negate)
        if _has_glob(body):
            return ValueMatcher(MatchKind.GLOB, body, negate)
        return ValueMatcher(MatchKind.EXACT, body, negate)
    if _is_permission_scalar(value):
        return ValueMatcher(MatchKind.EXACT, value)
    raise HookConfigError(f"{location} 条件值必须是标量或匹配器 mapping")


def match_condition(condition: HookCondition | None, context: HookContext) -> bool:
    if condition is None:
        return True
    flattened = flatten_context(context)
    results = [
        _match_predicate(flattened, predicate.field, predicate.matcher)
        for predicate in condition.predicates
    ]
    if condition.mode == "all":
        return all(results)
    return any(results)


def flatten_context(context: HookContext) -> Mapping[str, object]:
    flattened: dict[str, object] = {
        "event": context.event.value,
        "session.plan_only": context.plan_only,
    }
    _put_if_present(flattened, "turn_id", context.turn_id)
    _put_if_present(flattened, "round_index", context.round_index)
    _put_if_present(flattened, "user_text", context.user_text)
    _put_if_present(flattened, "error.code", context.error_code)
    _put_if_present(flattened, "error.message", context.error_message)
    if context.message is not None:
        flattened["message.role"] = context.message.role
        flattened["message.content"] = context.message.content
    if context.tool_call is not None:
        flattened["tool"] = context.tool_call.name
        flattened["tool_call_id"] = context.tool_call.id
    for name, value in context.normalized_arguments.items():
        flattened[f"arguments.{name}"] = value
    for name, value in context.raw_arguments.items():
        flattened[f"raw_arguments.{name}"] = value
    if context.tool_result is not None:
        flattened["result.ok"] = context.tool_result.ok
        if context.tool_result.error is not None:
            flattened["result.error"] = context.tool_result.error
    return flattened


def _parse_mapping_matcher(value: Mapping[object, object], *, location: str) -> ValueMatcher:
    negate = value.get("not", False)
    if not isinstance(negate, bool):
        raise HookConfigError(f"{location}.not 必须是布尔值")
    kinds = [key for key in ("exact", "glob", "regex") if key in value]
    unknown = set(value) - {"exact", "glob", "regex", "not"}
    if unknown:
        raise HookConfigError(f"{location} 包含未知匹配字段: {sorted(unknown)[0]}")
    if len(kinds) != 1:
        raise HookConfigError(f"{location} 必须且只能声明 exact/glob/regex 之一")
    kind_name = kinds[0]
    expected = value[kind_name]
    if kind_name == "exact":
        if not _is_permission_scalar(expected):
            raise HookConfigError(f"{location}.exact 必须是标量")
        return ValueMatcher(MatchKind.EXACT, expected, negate)
    if not isinstance(expected, str) or not expected:
        raise HookConfigError(f"{location}.{kind_name} 必须是非空字符串")
    if kind_name == "regex":
        _compile_regex(expected, location=location)
        return ValueMatcher(MatchKind.REGEX, expected, negate)
    return ValueMatcher(MatchKind.GLOB, expected, negate)


def _match_predicate(
    flattened: Mapping[str, object],
    field: str,
    matcher: ValueMatcher,
) -> bool:
    if field not in flattened:
        return False
    actual = flattened[field]
    matched = _match_value(actual, matcher)
    return not matched if matcher.negate else matched


def _match_value(actual: object, matcher: ValueMatcher) -> bool:
    expected = matcher.expected
    if matcher.kind is MatchKind.EXACT:
        if isinstance(expected, bool):
            return isinstance(actual, bool) and actual is expected
        if isinstance(expected, (int, float)) and not isinstance(expected, bool):
            return not isinstance(actual, bool) and actual == expected
        return actual == expected
    if not isinstance(actual, str) or not isinstance(expected, str):
        return False
    if matcher.kind is MatchKind.REGEX:
        return re.search(expected, actual) is not None
    return _glob_matches(actual, expected)


def _glob_matches(actual: str, pattern: str) -> bool:
    normalized_actual = actual.replace("\\", "/")
    normalized_pattern = pattern.replace("\\", "/")
    if fnmatch.fnmatchcase(normalized_actual, normalized_pattern):
        return True
    if "**/" in normalized_pattern:
        return fnmatch.fnmatchcase(normalized_actual, normalized_pattern.replace("**/", ""))
    return False


def _compile_regex(pattern: str, *, location: str) -> None:
    try:
        re.compile(pattern)
    except re.error as exc:
        raise HookConfigError(f"{location} regex 非法: {exc}") from exc


def _put_if_present(target: dict[str, object], key: str, value: object | None) -> None:
    if value is not None:
        target[key] = value


def _has_glob(value: str) -> bool:
    return any(character in value for character in _GLOB_CHARACTERS)


def _is_permission_scalar(value: object) -> bool:
    return isinstance(value, (str, int, float, bool)) and value is not None
