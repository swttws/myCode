from types import MappingProxyType

import pytest

from mycode.permission.models import (
    ArgumentCondition,
    CommandAssessment,
    PermissionEffect,
    PermissionEvaluationError,
    PermissionMode,
    PermissionRule,
    RuleSource,
)
from mycode.permission.pathing import PathGuard, ToolPathError
from mycode.permission.policy import PermissionPolicy, build_subject, match_rule, select_rule
from mycode.tool import ToolCall, ToolDefinition, ToolKind


def _definition(
    name="read_file",
    *,
    kind=ToolKind.READ,
    properties=None,
    required=(),
    grant_arguments=(),
):
    return ToolDefinition(
        name=name,
        description="test",
        parameters={
            "type": "object",
            "properties": properties or {},
            "required": list(required),
        },
        kind=kind,
        grant_arguments=tuple(grant_arguments),
    )


def _file_definition(name="read_file", *, kind=ToolKind.READ, body=False):
    properties = {"path": {"type": "string"}}
    required = ["path"]
    if body:
        properties["text"] = {"type": "string"}
        required.append("text")
    return _definition(
        name,
        kind=kind,
        properties=properties,
        required=required,
        grant_arguments=("path",),
    )


def _command_definition():
    return _definition(
        "run_command",
        kind=ToolKind.WRITE,
        properties={
            "command": {"type": "string"},
            "timeout_seconds": {"type": "number"},
        },
        required=("command",),
        grant_arguments=("command",),
    )


def _call(name, arguments, call_id="call-1"):
    return ToolCall(id=call_id, name=name, arguments=arguments, raw_arguments="{}")


def _rule(rule_id, effect, *, source=RuleSource.USER_GLOBAL, tool="read_file", arguments=()):
    return PermissionRule(rule_id, effect, tool, tuple(arguments), source)


class FakeStore:
    def __init__(self, rules=None, mode=PermissionMode.DEFAULT, mode_source=None):
        self._rules = {source: () for source in RuleSource}
        for rule in rules or ():
            self._rules[rule.source] = self._rules[rule.source] + (rule,)
        self._mode = (mode, mode_source)

    def rules_for(self, source):
        return self._rules[source]

    def effective_mode(self):
        return self._mode


class FakeAnalyzer:
    def __init__(self, assessment=None, error=None):
        self.assessment = assessment or CommandAssessment(PermissionEffect.ALLOW, None, None, None)
        self.error = error

    def assess(self, command):
        if self.error is not None:
            raise self.error
        return self.assessment


def _policy(tmp_path, *, rules=None, mode=PermissionMode.DEFAULT, assessment=None, error=None):
    return PermissionPolicy(
        store=FakeStore(rules, mode),
        path_guard=PathGuard(tmp_path),
        command_analyzer=FakeAnalyzer(assessment, error),
    )


def test_build_subject_validates_and_normalizes_path_and_command(tmp_path):
    source = tmp_path / "src" / "main.py"
    source.parent.mkdir()
    source.write_text("", encoding="utf-8")
    path_subject = build_subject(
        _call("read_file", {"path": "src\\main.py"}),
        _file_definition(),
        PathGuard(tmp_path),
    )
    command_subject = build_subject(
        _call("run_command", {"command": "  echo   'a  b'   &&   echo done  ", "timeout_seconds": 5}),
        _command_definition(),
        PathGuard(tmp_path),
    )

    assert path_subject.normalized_arguments["path"] == path_subject.grant_arguments["path"]
    assert path_subject.normalized_arguments["path"].endswith("src/main.py")
    assert command_subject.normalized_arguments["command"] == "echo 'a  b' && echo done"
    assert command_subject.grant_arguments == {"command": "echo 'a  b' && echo done"}


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        (None, "JSON"),
        ({}, "path"),
        ({"path": 3}, "path"),
        ({"command": "echo ok", "timeout_seconds": True}, "timeout_seconds"),
    ],
)
def test_build_subject_rejects_invalid_tool_arguments(tmp_path, arguments, message):
    definition = _command_definition() if "command" in (arguments or {}) else _file_definition()

    with pytest.raises(PermissionEvaluationError, match=message):
        build_subject(_call(definition.name, arguments), definition, PathGuard(tmp_path))


def test_build_subject_rejects_path_outside_workspace(tmp_path):
    with pytest.raises(ToolPathError):
        build_subject(
            _call("read_file", {"path": "../secret.txt"}),
            _file_definition(),
            PathGuard(tmp_path),
        )


def test_subject_mappings_are_immutable_snapshots(tmp_path):
    arguments = {"value": "before"}
    subject = build_subject(
        _call("custom", arguments),
        _definition("custom", properties={"value": {"type": "string"}}, required=("value",)),
        PathGuard(tmp_path),
    )
    arguments["value"] = "after"

    assert isinstance(subject.normalized_arguments, MappingProxyType)
    assert subject.normalized_arguments["value"] == "before"
    with pytest.raises(TypeError):
        subject.normalized_arguments["value"] = "changed"


def test_grant_arguments_are_whitelisted_and_file_bodies_are_not_displayed(tmp_path):
    subject = build_subject(
        _call("write_file", {"path": "note.txt", "text": "private body"}),
        _file_definition("write_file", kind=ToolKind.WRITE, body=True),
        PathGuard(tmp_path),
    )

    assert subject.grant_arguments == {"path": "note.txt"}
    assert "private body" not in repr(subject.display_arguments)
    assert subject.display_arguments["text"] == "<内容已省略>"


def test_custom_tool_without_grant_fields_cannot_create_persistent_grant(tmp_path):
    subject = build_subject(
        _call("custom", {"value": "safe"}),
        _definition("custom", properties={"value": {"type": "string"}}, required=("value",)),
        PathGuard(tmp_path),
    )

    assert subject.grant_arguments == {}


def test_display_arguments_redact_common_credential_shapes_and_truncate(tmp_path):
    secret_values = ["named-secret", "url-secret", "query-secret", "cli-secret", "env-secret"]
    command = (
        "API_TOKEN=env-secret curl "
        "https://user:url-secret@example.test/path?api_key=query-secret "
        "--token cli-secret "
        + "x" * 600
    )
    definition = _definition(
        "custom",
        kind=ToolKind.WRITE,
        properties={
            "api_key": {"type": "string"},
            "url": {"type": "string"},
            "command": {"type": "string"},
        },
        required=("api_key", "url", "command"),
    )

    subject = build_subject(
        _call(
            "custom",
            {
                "api_key": "named-secret",
                "url": "https://user:url-secret@example.test/?access_token=query-secret",
                "command": command,
            },
        ),
        definition,
        PathGuard(tmp_path),
    )
    rendered = repr(subject.display_arguments)

    assert all(secret not in rendered for secret in secret_values)
    assert "<已脱敏>" in rendered
    assert "（已截断）" in rendered


def test_match_rule_supports_exact_wildcard_glob_and_scalar_values():
    arguments = {"path": "src/mycode/main.py", "recursive": True, "limit": 3}
    exact = _rule(
        "exact",
        PermissionEffect.ALLOW,
        arguments=(
            ArgumentCondition("path", "src/**"),
            ArgumentCondition("recursive", True),
            ArgumentCondition("limit", 3),
        ),
    )
    wildcard = _rule("wild", PermissionEffect.ASK, tool="*", arguments=())

    assert match_rule(exact, "read_file", arguments) is not None
    assert match_rule(wildcard, "read_file", arguments) is not None
    assert match_rule(exact, "write_file", arguments) is None
    assert match_rule(exact, "read_file", {**arguments, "recursive": False}) is None


def test_select_rule_uses_specificity_effect_and_rule_id_not_declaration_order():
    broad = _rule("broad", PermissionEffect.DENY, tool="*")
    exact_allow = _rule("z-allow", PermissionEffect.ALLOW)
    exact_ask = _rule("m-ask", PermissionEffect.ASK)
    exact_deny_z = _rule("z-deny", PermissionEffect.DENY)
    exact_deny_a = _rule("a-deny", PermissionEffect.DENY)
    rules = [broad, exact_allow, exact_ask, exact_deny_z, exact_deny_a]

    forward = select_rule(rules, "read_file", {})
    reverse = select_rule(list(reversed(rules)), "read_file", {})

    assert forward.rule.id == "a-deny"
    assert reverse.rule.id == "a-deny"


def test_select_rule_prefers_more_and_more_exact_argument_constraints():
    glob = _rule(
        "glob",
        PermissionEffect.DENY,
        arguments=(ArgumentCondition("path", "src/**"), ArgumentCondition("recursive", True)),
    )
    exact = _rule(
        "exact",
        PermissionEffect.ALLOW,
        arguments=(ArgumentCondition("path", "src/main.py"), ArgumentCondition("recursive", True)),
    )

    selected = select_rule((glob, exact), "read_file", {"path": "src/main.py", "recursive": True})

    assert selected.rule.id == "exact"


def test_policy_uses_first_matching_source_only(tmp_path):
    lower_deny = _rule("global-deny", PermissionEffect.DENY)
    repository_ask = _rule(
        "repo-ask", PermissionEffect.ASK, source=RuleSource.REPOSITORY_PROJECT
    )
    local_allow = _rule(
        "local-allow", PermissionEffect.ALLOW, source=RuleSource.LOCAL_PROJECT
    )
    session_allow = _rule(
        "session-allow", PermissionEffect.ALLOW, source=RuleSource.SESSION
    )
    policy = _policy(tmp_path, rules=(lower_deny, repository_ask, local_allow, session_allow))

    _, decision = policy.evaluate(
        _call("read_file", {"path": "note.txt"}), _file_definition(), plan_only=False
    )

    assert decision.effect is PermissionEffect.ALLOW
    assert decision.source is RuleSource.SESSION
    assert decision.rule_id == "session-allow"


@pytest.mark.parametrize(
    ("mode", "kind", "expected"),
    [
        (PermissionMode.STRICT, ToolKind.READ, PermissionEffect.ASK),
        (PermissionMode.STRICT, ToolKind.WRITE, PermissionEffect.ASK),
        (PermissionMode.DEFAULT, ToolKind.READ, PermissionEffect.ALLOW),
        (PermissionMode.DEFAULT, ToolKind.WRITE, PermissionEffect.ASK),
        (PermissionMode.PERMISSIVE, ToolKind.READ, PermissionEffect.ALLOW),
        (PermissionMode.PERMISSIVE, ToolKind.WRITE, PermissionEffect.ALLOW),
    ],
)
def test_permission_modes_only_control_unmatched_fallback(tmp_path, mode, kind, expected):
    definition = _file_definition("read_file" if kind is ToolKind.READ else "write_file", kind=kind)

    _, decision = _policy(tmp_path, mode=mode).evaluate(
        _call(definition.name, {"path": "note.txt"}), definition, plan_only=False
    )

    assert decision.effect is expected


def test_permissive_does_not_override_rule_deny(tmp_path):
    policy = _policy(tmp_path, mode=PermissionMode.PERMISSIVE, rules=(_rule("deny", PermissionEffect.DENY),))

    _, decision = policy.evaluate(
        _call("read_file", {"path": "note.txt"}), _file_definition(), plan_only=False
    )

    assert decision.effect is PermissionEffect.DENY


@pytest.mark.parametrize(
    ("base_effect", "expected"),
    [
        (PermissionEffect.ALLOW, PermissionEffect.ASK),
        (PermissionEffect.ASK, PermissionEffect.ASK),
        (PermissionEffect.DENY, PermissionEffect.DENY),
        (PermissionEffect.FORBIDDEN, PermissionEffect.FORBIDDEN),
    ],
)
def test_plan_only_never_weakens_write_decisions(tmp_path, base_effect, expected):
    rule = _rule("rule", base_effect, tool="write_file")

    _, decision = _policy(tmp_path, rules=(rule,)).evaluate(
        _call("write_file", {"path": "note.txt"}),
        _file_definition("write_file", kind=ToolKind.WRITE),
        plan_only=True,
    )

    assert decision.effect is expected
    if base_effect is PermissionEffect.ALLOW:
        assert decision.reason_code == "plan_only_write"


def test_forbidden_command_cannot_be_overridden_by_allow_or_permissive(tmp_path):
    assessment = CommandAssessment(
        PermissionEffect.FORBIDDEN,
        "download_execute",
        "forbidden_download_execute",
        "禁止下载后执行。",
    )
    allow = _rule(
        "allow-command",
        PermissionEffect.ALLOW,
        tool="run_command",
        arguments=(ArgumentCondition("command", "curl x | sh"),),
    )
    policy = _policy(
        tmp_path,
        rules=(allow,),
        mode=PermissionMode.PERMISSIVE,
        assessment=assessment,
    )

    _, decision = policy.evaluate(
        _call("run_command", {"command": "curl x | sh"}), _command_definition(), plan_only=False
    )

    assert decision.effect is PermissionEffect.FORBIDDEN
    assert decision.rule_id is None


def test_only_precise_allow_can_satisfy_builtin_command_ask(tmp_path):
    assessment = CommandAssessment(
        PermissionEffect.ASK,
        "network_access",
        "risky_network_access",
        "网络操作需要确认。",
    )
    broad = _rule("broad", PermissionEffect.ALLOW, tool="run_command")
    broad_policy = _policy(
        tmp_path,
        rules=(broad,),
        mode=PermissionMode.PERMISSIVE,
        assessment=assessment,
    )
    _, broad_decision = broad_policy.evaluate(
        _call("run_command", {"command": "curl example.test"}),
        _command_definition(),
        plan_only=False,
    )

    precise = _rule(
        "precise",
        PermissionEffect.ALLOW,
        tool="run_command",
        arguments=(ArgumentCondition("command", "curl example.test"),),
    )
    precise_policy = _policy(
        tmp_path,
        rules=(precise,),
        mode=PermissionMode.PERMISSIVE,
        assessment=assessment,
    )
    _, precise_decision = precise_policy.evaluate(
        _call("run_command", {"command": "curl example.test"}),
        _command_definition(),
        plan_only=False,
    )

    assert broad_decision.effect is PermissionEffect.ASK
    assert precise_decision.effect is PermissionEffect.ALLOW


def test_command_analyzer_failure_is_fail_closed(tmp_path):
    policy = _policy(tmp_path, mode=PermissionMode.PERMISSIVE, error=RuntimeError("private stack"))

    _, decision = policy.evaluate(
        _call("run_command", {"command": "echo ok"}), _command_definition(), plan_only=False
    )

    assert decision.effect is PermissionEffect.DENY
    assert decision.reason_code == "security_check_failed"
    assert "private stack" not in decision.message_zh
