import asyncio
from dataclasses import FrozenInstanceError

import pytest

from mycode.permission.models import (
    ApprovalDecision,
    ApprovalDecisionType,
    ApprovalOutcome,
    ArgumentCondition,
    PermissionEffect,
    PermissionMode,
    PermissionPersistenceError,
    PermissionRule,
    PermissionSessionState,
    RuleSource,
)
from mycode.permission.service import PermissionInterceptor, PermissionService
from mycode.tool import ToolCall, ToolDefinition, ToolKind, ToolResult


def test_permission_enums_have_stable_values():
    assert [item.value for item in PermissionEffect] == ["allow", "deny", "ask", "forbidden"]
    assert [item.value for item in PermissionMode] == ["strict", "default", "permissive"]
    assert [item.value for item in RuleSource] == [
        "session",
        "local_project",
        "repository_project",
        "user_global",
    ]
    assert [item.value for item in ApprovalDecisionType] == [
        "approve_once",
        "approve_session",
        "approve_project",
        "reject",
        "cancel",
    ]
    assert [item.value for item in ApprovalOutcome] == ["execute", "rejected", "cancelled", "error"]


def test_permission_rules_are_frozen_and_use_tuple_conditions():
    rule = PermissionRule(
        id="allow-read",
        effect=PermissionEffect.ALLOW,
        tool="read_file",
        arguments=(ArgumentCondition("path", "src/**"),),
        source=RuleSource.USER_GLOBAL,
    )

    assert isinstance(rule.arguments, tuple)
    with pytest.raises(FrozenInstanceError):
        rule.id = "changed"


def test_permission_session_state_reset_clears_only_session_values():
    state = PermissionSessionState(
        mode_override=PermissionMode.STRICT,
        rules=[
            PermissionRule(
                id="session-read",
                effect=PermissionEffect.ALLOW,
                tool="read_file",
                arguments=(),
                source=RuleSource.SESSION,
            )
        ],
    )

    state.reset()

    assert state.mode_override is None
    assert state.rules == []


def _definition(name="write_file", *, kind=ToolKind.WRITE, grants=("path",)):
    properties = {"path": {"type": "string"}}
    required = ["path"]
    if name == "run_command":
        properties = {"command": {"type": "string"}}
        required = ["command"]
    return ToolDefinition(
        name=name,
        description="test",
        parameters={"type": "object", "properties": properties, "required": required},
        kind=kind,
        grant_arguments=tuple(grants),
    )


def _call(name="write_file", *, call_id="call-1", arguments=None):
    if arguments is None:
        arguments = {"path": "note.txt"}
    return ToolCall(id=call_id, name=name, arguments=arguments, raw_arguments="{}")


def test_permission_service_create_shares_components_and_caches_only_ask(tmp_path):
    service = PermissionService.create(tmp_path, home=tmp_path / "home")
    read = _definition("read_file", kind=ToolKind.READ)
    write = _definition()

    read_decision = service.evaluate(
        _call("read_file"), read, plan_only=False, round_index=1
    )
    write_call = _call()
    write_decision = service.evaluate(write_call, write, plan_only=False, round_index=1)

    assert service.path_guard.workspace_root == tmp_path.resolve()
    assert read_decision.effect is PermissionEffect.ALLOW
    assert write_decision.effect is PermissionEffect.ASK
    with pytest.raises(Exception, match="审批"):
        service.create_approval_request(
            _call("read_file"), read_decision, plan_only=False, round_index=1
        )
    request = service.create_approval_request(
        write_call, write_decision, plan_only=False, round_index=1
    )
    assert request.tool_call == write_call


def test_permission_service_converts_invalid_arguments_and_denials_to_safe_chinese_result(tmp_path):
    service = PermissionService.create(tmp_path, home=tmp_path / "home")
    call = _call(arguments={})

    decision = service.evaluate(call, _definition(), plan_only=False, round_index=1)
    result = service.denied_result(call, decision)

    assert decision.effect is PermissionEffect.DENY
    assert decision.reason_code == "invalid_tool_arguments"
    assert result.ok is False
    assert result.content == {
        "tool_call_id": "call-1",
        "reason_code": "invalid_tool_arguments",
        "decision": "deny",
        "message": decision.message_zh,
    }
    assert "Traceback" not in repr(result)
    assert any("\u4e00" <= character <= "\u9fff" for character in result.error)


def test_approval_request_has_scoped_options_and_stable_candidate_grant(tmp_path):
    service = PermissionService.create(tmp_path, home=tmp_path / "home")
    call = _call(arguments={"path": "./note.txt"})
    decision = service.evaluate(call, _definition(), plan_only=False, round_index=2)

    request = service.create_approval_request(
        call, decision, plan_only=False, round_index=2
    )

    assert request.options == (
        ApprovalDecisionType.APPROVE_ONCE,
        ApprovalDecisionType.APPROVE_SESSION,
        ApprovalDecisionType.APPROVE_PROJECT,
        ApprovalDecisionType.REJECT,
        ApprovalDecisionType.CANCEL,
    )
    assert request.candidate_grant is not None
    assert request.candidate_grant.tool == "write_file"
    assert request.candidate_grant.arguments == (ArgumentCondition("path", "note.txt"),)
    assert len(request.candidate_grant.fingerprint) == 64

    with pytest.raises(Exception, match="审批"):
        service.create_approval_request(call, decision, plan_only=False, round_index=2)


@pytest.mark.parametrize("plan_only,grants", [(True, ("path",)), (False, ())])
def test_plan_only_or_empty_grant_arguments_only_offer_once_reject_cancel(tmp_path, plan_only, grants):
    service = PermissionService.create(tmp_path, home=tmp_path / "home")
    definition = _definition("custom", grants=grants)
    call = _call("custom")
    decision = service.evaluate(call, definition, plan_only=plan_only, round_index=1)

    request = service.create_approval_request(
        call, decision, plan_only=plan_only, round_index=1
    )

    assert request.options == (
        ApprovalDecisionType.APPROVE_ONCE,
        ApprovalDecisionType.REJECT,
        ApprovalDecisionType.CANCEL,
    )
    assert request.candidate_grant is None


def test_resolve_approval_once_and_session_scope(tmp_path):
    service = PermissionService.create(tmp_path, home=tmp_path / "home")
    definition = _definition()

    once_call = _call(call_id="once")
    once_decision = service.evaluate(once_call, definition, plan_only=False, round_index=1)
    once_request = service.create_approval_request(
        once_call, once_decision, plan_only=False, round_index=1
    )
    once = asyncio.run(
        service.resolve_approval(once_request, ApprovalDecision(ApprovalDecisionType.APPROVE_ONCE))
    )
    assert once.outcome is ApprovalOutcome.EXECUTE
    assert service.evaluate(
        _call(call_id="once-again"), definition, plan_only=False, round_index=1
    ).effect is PermissionEffect.ASK

    session_call = _call(call_id="session")
    session_decision = service.evaluate(session_call, definition, plan_only=False, round_index=1)
    session_request = service.create_approval_request(
        session_call, session_decision, plan_only=False, round_index=1
    )
    session = asyncio.run(
        service.resolve_approval(
            session_request, ApprovalDecision(ApprovalDecisionType.APPROVE_SESSION)
        )
    )
    assert session.outcome is ApprovalOutcome.EXECUTE
    assert service.evaluate(
        _call(call_id="session-again"), definition, plan_only=False, round_index=1
    ).effect is PermissionEffect.ALLOW


def test_resolve_project_approval_persists_before_execute(tmp_path):
    home = tmp_path / "home"
    service = PermissionService.create(tmp_path, home=home)
    call = _call()
    decision = service.evaluate(call, _definition(), plan_only=False, round_index=1)
    request = service.create_approval_request(call, decision, plan_only=False, round_index=1)

    resolution = asyncio.run(
        service.resolve_approval(
            request, ApprovalDecision(ApprovalDecisionType.APPROVE_PROJECT)
        )
    )

    assert resolution.outcome is ApprovalOutcome.EXECUTE
    assert service.local_project_path.is_file()
    assert service.local_project_path.is_relative_to(home)
    assert not (tmp_path / "mycode.permissions.yaml").exists()


def test_resolve_reject_cancel_and_invalid_choice(tmp_path):
    service = PermissionService.create(tmp_path, home=tmp_path / "home")
    definition = _definition()

    def request(call_id, *, plan_only=False):
        call = _call(call_id=call_id)
        decision = service.evaluate(call, definition, plan_only=plan_only, round_index=1)
        return service.create_approval_request(
            call, decision, plan_only=plan_only, round_index=1
        )

    rejected = asyncio.run(
        service.resolve_approval(
            request("reject"), ApprovalDecision(ApprovalDecisionType.REJECT)
        )
    )
    cancelled = asyncio.run(
        service.resolve_approval(
            request("cancel"), ApprovalDecision(ApprovalDecisionType.CANCEL)
        )
    )
    invalid = asyncio.run(
        service.resolve_approval(
            request("invalid", plan_only=True),
            ApprovalDecision(ApprovalDecisionType.APPROVE_SESSION),
        )
    )

    assert rejected.outcome is ApprovalOutcome.REJECTED
    assert rejected.tool_result.content["reason_code"] == "tool_rejected_by_user"
    assert cancelled.outcome is ApprovalOutcome.CANCELLED
    assert cancelled.tool_result is None
    assert invalid.outcome is ApprovalOutcome.ERROR
    assert invalid.tool_result.content["reason_code"] == "invalid_approval_choice"


def test_project_persistence_failure_returns_error_and_does_not_allow(tmp_path, monkeypatch):
    service = PermissionService.create(tmp_path, home=tmp_path / "home")
    call = _call()
    decision = service.evaluate(call, _definition(), plan_only=False, round_index=1)
    request = service.create_approval_request(call, decision, plan_only=False, round_index=1)

    async def fail(rule):
        raise PermissionPersistenceError("simulated secret failure")

    monkeypatch.setattr(service._store, "persist_local_project_rule", fail)
    resolution = asyncio.run(
        service.resolve_approval(
            request, ApprovalDecision(ApprovalDecisionType.APPROVE_PROJECT)
        )
    )

    assert resolution.outcome is ApprovalOutcome.ERROR
    assert resolution.tool_result.content["reason_code"] == "permission_persist_failed"
    assert "simulated secret failure" not in repr(resolution.tool_result)


def test_permission_interceptor_delegates_and_preserves_tool_result(tmp_path):
    service = PermissionService.create(tmp_path, home=tmp_path / "home")
    interceptor = PermissionInterceptor(service)
    call = _call("read_file")
    definition = _definition("read_file", kind=ToolKind.READ)

    decision = asyncio.run(
        interceptor.before_tool(call, definition, plan_only=False, round_index=1)
    )
    result = ToolResult(ok=True, tool_name="read_file", content={"text": "hello"})
    returned = asyncio.run(interceptor.after_tool(call, result))

    assert decision.effect is PermissionEffect.ALLOW
    assert returned is result
