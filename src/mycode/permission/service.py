from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from types import MappingProxyType

from mycode.permission.command import CommandAnalyzer
from mycode.permission.config import PermissionStore
from mycode.permission.models import (
    ApprovalDecision,
    ApprovalDecisionType,
    ApprovalOutcome,
    ApprovalRequest,
    ApprovalResolution,
    ArgumentCondition,
    PermissionDecision,
    PermissionEffect,
    PermissionEvaluationError,
    PermissionGrant,
    PermissionMode,
    PermissionPersistenceError,
    PermissionRule,
    PermissionSubject,
    RuleSource,
)
from mycode.permission.pathing import PathGuard, ToolPathError
from mycode.permission.policy import PermissionPolicy
from mycode.tool.base import ToolCall, ToolDefinition, ToolResult


class PermissionService:
    def __init__(
        self,
        *,
        store: PermissionStore,
        policy: PermissionPolicy,
        path_guard: PathGuard,
    ) -> None:
        self._store = store
        self._policy = policy
        self._path_guard = path_guard
        self._pending: dict[str, tuple[PermissionSubject, PermissionDecision]] = {}

    @classmethod
    def create(
        cls,
        workspace_root: str | Path,
        *,
        home: str | Path | None = None,
    ) -> "PermissionService":
        path_guard = PathGuard(workspace_root)
        store = PermissionStore.load(path_guard.workspace_root, home=home)
        analyzer = CommandAnalyzer(path_guard.workspace_root, home=home)
        policy = PermissionPolicy(
            store=store,
            path_guard=path_guard,
            command_analyzer=analyzer,
        )
        return cls(store=store, policy=policy, path_guard=path_guard)

    @property
    def path_guard(self) -> PathGuard:
        return self._path_guard

    @property
    def local_project_path(self) -> Path:
        return self._store.paths.local_project

    def evaluate(
        self,
        call: ToolCall,
        definition: ToolDefinition,
        *,
        plan_only: bool,
        round_index: int,
    ) -> PermissionDecision:
        try:
            subject, decision = self._policy.evaluate(call, definition, plan_only=plan_only)
        except ToolPathError:
            decision = self._failure_decision(
                "path_outside_workspace",
                "工具路径超出工作区或无法安全确认，已拒绝执行。",
            )
            self._pending.pop(call.id, None)
            return decision
        except PermissionEvaluationError:
            decision = self._failure_decision(
                "invalid_tool_arguments",
                "工具参数不符合安全执行契约，已拒绝执行。",
            )
            self._pending.pop(call.id, None)
            return decision
        except Exception:
            decision = self._failure_decision(
                "security_check_failed",
                "权限安全检查失败，已拒绝执行。",
            )
            self._pending.pop(call.id, None)
            return decision

        if decision.effect is PermissionEffect.ASK:
            self._pending[call.id] = (subject, decision)
        else:
            self._pending.pop(call.id, None)
        return decision

    def create_approval_request(
        self,
        call: ToolCall,
        decision: PermissionDecision,
        *,
        plan_only: bool,
        round_index: int,
    ) -> ApprovalRequest:
        if decision.effect is not PermissionEffect.ASK:
            raise PermissionEvaluationError("当前权限决定不需要审批")
        cached = self._pending.pop(call.id, None)
        if cached is None:
            raise PermissionEvaluationError("审批上下文不存在或已经失效")
        subject, cached_decision = cached
        if subject.call != call or cached_decision != decision:
            raise PermissionEvaluationError("审批上下文与当前工具调用不一致")

        candidate = None
        if not plan_only and subject.grant_arguments:
            conditions = tuple(
                ArgumentCondition(name, value)
                for name, value in sorted(subject.grant_arguments.items())
            )
            fingerprint = _grant_fingerprint(call.name, conditions)
            candidate = PermissionGrant(call.name, conditions, fingerprint)

        options = (
            ApprovalDecisionType.APPROVE_ONCE,
            ApprovalDecisionType.REJECT,
            ApprovalDecisionType.CANCEL,
        )
        if candidate is not None:
            options = (
                ApprovalDecisionType.APPROVE_ONCE,
                ApprovalDecisionType.APPROVE_SESSION,
                ApprovalDecisionType.APPROVE_PROJECT,
                ApprovalDecisionType.REJECT,
                ApprovalDecisionType.CANCEL,
            )
        return ApprovalRequest(
            id=f"approval-{call.id}",
            tool_call=call,
            decision=decision,
            options=options,
            candidate_grant=candidate,
            plan_only=plan_only,
            round_index=round_index,
        )

    async def resolve_approval(
        self,
        request: ApprovalRequest,
        decision: ApprovalDecision,
    ) -> ApprovalResolution:
        if decision.type not in request.options:
            return ApprovalResolution(
                ApprovalOutcome.ERROR,
                _approval_result(
                    request.tool_call,
                    "invalid_approval_choice",
                    "审批选项无效，当前工具不会执行。",
                ),
            )
        if decision.type is ApprovalDecisionType.APPROVE_ONCE:
            return ApprovalResolution(ApprovalOutcome.EXECUTE)
        if decision.type is ApprovalDecisionType.REJECT:
            return ApprovalResolution(
                ApprovalOutcome.REJECTED,
                _approval_result(
                    request.tool_call,
                    "tool_rejected_by_user",
                    "用户拒绝了本次工具调用。",
                ),
            )
        if decision.type is ApprovalDecisionType.CANCEL:
            return ApprovalResolution(ApprovalOutcome.CANCELLED)

        grant = request.candidate_grant
        if grant is None:
            return ApprovalResolution(
                ApprovalOutcome.ERROR,
                _approval_result(
                    request.tool_call,
                    "approval_scope_unavailable",
                    "该工具调用不能创建持久授权，当前工具不会执行。",
                ),
            )
        source = (
            RuleSource.SESSION
            if decision.type is ApprovalDecisionType.APPROVE_SESSION
            else RuleSource.LOCAL_PROJECT
        )
        rule = PermissionRule(
            id=_grant_rule_id(grant),
            effect=PermissionEffect.ALLOW,
            tool=grant.tool,
            arguments=grant.arguments,
            source=source,
        )
        try:
            if source is RuleSource.SESSION:
                self._store.add_session_rule(rule)
            else:
                # 项目授权落盘成功前绝不执行当前调用，避免界面声称“永久允许”但磁盘实际未保存。
                await self._store.persist_local_project_rule(rule)
        except PermissionPersistenceError:
            return ApprovalResolution(
                ApprovalOutcome.ERROR,
                _approval_result(
                    request.tool_call,
                    "permission_persist_failed",
                    "项目授权保存失败，当前工具不会执行。",
                ),
            )
        except Exception:
            return ApprovalResolution(
                ApprovalOutcome.ERROR,
                _approval_result(
                    request.tool_call,
                    "security_check_failed",
                    "审批处理失败，当前工具不会执行。",
                ),
            )
        return ApprovalResolution(ApprovalOutcome.EXECUTE)

    def denied_result(self, call: ToolCall, decision: PermissionDecision) -> ToolResult:
        return ToolResult(
            ok=False,
            tool_name=call.name,
            content={
                "tool_call_id": call.id,
                "reason_code": decision.reason_code,
                "decision": decision.effect.value,
                "message": decision.message_zh,
            },
            error=decision.message_zh,
        )

    def effective_mode(self) -> tuple[PermissionMode, RuleSource | None]:
        return self._store.effective_mode()

    def set_session_mode(self, mode: PermissionMode) -> None:
        self._store.set_session_mode(mode)

    def clear_session(self) -> None:
        self._pending.clear()
        self._store.clear_session()

    def _failure_decision(self, reason_code: str, message: str) -> PermissionDecision:
        mode, _source = self._store.effective_mode()
        return PermissionDecision(
            effect=PermissionEffect.DENY,
            reason_code=reason_code,
            message_zh=message,
            mode=mode,
            display_arguments=MappingProxyType({}),
        )


class PermissionInterceptor:
    def __init__(self, service: PermissionService) -> None:
        self._service = service

    async def before_tool(
        self,
        call: ToolCall,
        definition: ToolDefinition,
        *,
        plan_only: bool,
        round_index: int,
    ) -> PermissionDecision:
        return self._service.evaluate(
            call,
            definition,
            plan_only=plan_only,
            round_index=round_index,
        )

    def create_approval_request(
        self,
        call: ToolCall,
        decision: PermissionDecision,
        *,
        plan_only: bool,
        round_index: int,
    ) -> ApprovalRequest:
        return self._service.create_approval_request(
            call,
            decision,
            plan_only=plan_only,
            round_index=round_index,
        )

    def denied_result(self, call: ToolCall, decision: PermissionDecision) -> ToolResult:
        return self._service.denied_result(call, decision)

    async def resolve_approval(
        self,
        request: ApprovalRequest,
        decision: ApprovalDecision,
    ) -> ApprovalResolution:
        return await self._service.resolve_approval(request, decision)

    async def after_tool(self, call: ToolCall, result: ToolResult) -> ToolResult:
        return result


def _grant_fingerprint(tool: str, conditions: tuple[ArgumentCondition, ...]) -> str:
    payload = {
        "tool": tool,
        "arguments": [
            {"name": condition.name, "expected": condition.expected}
            for condition in conditions
        ],
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _grant_rule_id(grant: PermissionGrant) -> str:
    tool = re.sub(r"[^a-zA-Z0-9_-]+", "-", grant.tool).strip("-") or "tool"
    return f"hitl-{tool}-{grant.fingerprint[:12]}"


def _approval_result(call: ToolCall, reason_code: str, message: str) -> ToolResult:
    return ToolResult(
        ok=False,
        tool_name=call.name,
        content={
            "tool_call_id": call.id,
            "reason_code": reason_code,
            "decision": "deny",
            "message": message,
        },
        error=message,
    )
