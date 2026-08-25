from __future__ import annotations

import shlex
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from types import MappingProxyType

from mycode.permission.models import PermissionDecision, PermissionEffect, PermissionMode
from mycode.tool import ToolCall, ToolDefinition, ToolKind, ToolResult
from mycode.team.domain.state import TeamRuntimeRole
from mycode.team.tooling.tool_names import LEAD_TEAM_TOOL_NAMES, MEMBER_TEAM_TOOL_NAMES, PARENT_TEAM_TOOL_NAMES


_PARENT_TOOLS = PARENT_TEAM_TOOL_NAMES | frozenset({"team"})
_LEAD_CONTROL_TOOLS = LEAD_TEAM_TOOL_NAMES | frozenset({"team", "team_lead"})
_MEMBER_CONTROL_TOOLS = MEMBER_TEAM_TOOL_NAMES | frozenset({"team_member"})
_LOCAL_EDIT_TOOLS = frozenset({"read_file", "write_file", "edit_file", "run_command"})
_LEAD_EXTRA_TOOLS = frozenset({"Agent", "find_files", "search_code"})
_COORDINATOR_TOOLS = _LEAD_CONTROL_TOOLS | frozenset({"read_file", "run_command"})
_COORDINATOR_WRITE_TOOLS = LEAD_TEAM_TOOL_NAMES | frozenset({"team", "team_lead"})
_LOCAL_GIT_READ_COMMANDS = frozenset(
    {
        "branch",
        "diff",
        "log",
        "merge-base",
        "rev-list",
        "rev-parse",
        "show",
        "status",
    }
)
_LOCAL_GIT_INTEGRATION_COMMANDS = frozenset({"merge"})


@dataclass(frozen=True)
class TeamToolPolicy:
    role: TeamRuntimeRole
    coordinator_enabled: bool = False
    mode: PermissionMode = PermissionMode.DEFAULT
    member_write_allowed_provider: Callable[[], bool] | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.role, TeamRuntimeRole):
            raise ValueError("role must be a TeamRuntimeRole")
        if type(self.coordinator_enabled) is not bool:
            raise ValueError("coordinator_enabled must be a bool")
        if not isinstance(self.mode, PermissionMode):
            raise ValueError("mode must be a PermissionMode")
        if self.member_write_allowed_provider is not None and not callable(self.member_write_allowed_provider):
            raise ValueError("member_write_allowed_provider must be callable")

    def visible_names(self, candidates: frozenset[str]) -> frozenset[str]:
        if not isinstance(candidates, frozenset):
            candidates = frozenset(candidates)
        if self.role is TeamRuntimeRole.PARENT:
            allowed = _PARENT_TOOLS
        elif self.role is TeamRuntimeRole.MEMBER:
            allowed = _MEMBER_CONTROL_TOOLS | _LOCAL_EDIT_TOOLS
        elif self.coordinator_enabled:
            allowed = _COORDINATOR_TOOLS
        else:
            allowed = _LEAD_CONTROL_TOOLS | _LOCAL_EDIT_TOOLS | _LEAD_EXTRA_TOOLS
        return frozenset(name for name in candidates if name in allowed)

    def evaluate(self, call: ToolCall, definition: ToolDefinition) -> PermissionDecision:
        if call.name != definition.name:
            return self._deny(
                "team_tool_mismatch",
                "工具调用与定义不匹配",
                self.mode,
                MappingProxyType({}),
            )
        if (
            self.role is TeamRuntimeRole.MEMBER
            and call.name not in _MEMBER_CONTROL_TOOLS
            and definition.kind is ToolKind.WRITE
            and self.member_write_allowed_provider is not None
            and not self.member_write_allowed_provider()
        ):
            return self._deny(
                "member_approval_required",
                "成员在写入工作区前需要计划审批",
                self.mode,
                _display_arguments(call.arguments),
            )
        if self.coordinator_enabled and self.role is TeamRuntimeRole.LEAD:
            if call.name == "Agent":
                return self._deny(
                    "coordinator_agent_forbidden",
                "协调器模式不能启动子 Agent",
                    self.mode,
                    _display_arguments(call.arguments),
                )
            if call.name == "run_command":
                command = (call.arguments or {}).get("command") if isinstance(call.arguments, dict) else None
                if isinstance(command, str) and _is_coordinator_git_command(command):
                    return self._allow(_display_arguments(call.arguments))
                return self._deny(
                    "coordinator_shell_forbidden",
                    "协调器模式只允许本地 Git 检查命令",
                    self.mode,
                    _display_arguments(call.arguments),
                )
            if call.name in _COORDINATOR_WRITE_TOOLS:
                return self._allow(_display_arguments(call.arguments))
            if definition.kind is ToolKind.WRITE:
                return self._deny(
                    "coordinator_write_forbidden",
                    "协调器模式不能使用该写入工具",
                    self.mode,
                    _display_arguments(call.arguments),
                )
        if call.name not in self.visible_names(frozenset({call.name})):
            return self._deny(
                "team_tool_hidden",
                "该工具对当前团队角色不可见",
                self.mode,
                _display_arguments(call.arguments),
            )
        return self._allow(_display_arguments(call.arguments))

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

    def _allow(self, display_arguments: Mapping[str, object]) -> PermissionDecision:
        return PermissionDecision(
            effect=PermissionEffect.ALLOW,
            reason_code="team_tool_allowed",
            message_zh="团队工具允许执行。",
            mode=self.mode,
            display_arguments=display_arguments,
        )

    def _deny(
        self,
        reason_code: str,
            message: str,
        mode: PermissionMode,
        display_arguments: Mapping[str, object],
    ) -> PermissionDecision:
        return PermissionDecision(
            effect=PermissionEffect.DENY,
            reason_code=reason_code,
            message_zh=message,
            mode=mode,
            display_arguments=display_arguments,
        )


class TeamPermissionInterceptor:
    def __init__(self, *, policy_provider, permission) -> None:
        self._policy_provider = policy_provider
        self._permission = permission

    async def before_tool(
        self,
        call: ToolCall,
        definition: ToolDefinition,
        *,
        plan_only: bool,
        round_index: int,
    ) -> PermissionDecision:
        policy = self._policy_provider()
        if policy is None:
            return await self._permission.before_tool(
                call,
                definition,
                plan_only=plan_only,
                round_index=round_index,
            )
        decision = policy.evaluate(call, definition)
        if decision.effect is not PermissionEffect.ALLOW:
            return decision
        return await self._permission.before_tool(
            call,
            definition,
            plan_only=plan_only,
            round_index=round_index,
        )

    def denied_result(self, call: ToolCall, decision: PermissionDecision) -> ToolResult:
        reason = decision.reason_code
        if isinstance(reason, str) and (reason.startswith("team_") or reason.startswith("coordinator_")):
            policy = self._policy_provider()
            if policy is None:
                return self._permission.denied_result(call, decision)
            return policy.denied_result(call, decision)
        return self._permission.denied_result(call, decision)

    def create_approval_request(self, *args, **kwargs):
        return self._permission.create_approval_request(*args, **kwargs)

    async def resolve_approval(self, *args, **kwargs):
        return await self._permission.resolve_approval(*args, **kwargs)

    async def after_tool(self, call: ToolCall, result: ToolResult) -> ToolResult:
        return await self._permission.after_tool(call, result)


def _display_arguments(arguments: object) -> MappingProxyType:
    if not isinstance(arguments, dict):
        return MappingProxyType({})
    return MappingProxyType(dict(arguments))


def _is_coordinator_git_command(command: str) -> bool:
    try:
        tokens = shlex.split(command, posix=False)
    except ValueError:
        return False
    if not tokens:
        return False
    tokens = tuple(token.strip("\"'") for token in tokens)
    if tokens[0].lower() != "git":
        return False
    index = 1
    while index < len(tokens):
        token = tokens[index]
        lowered = token.lower()
        if lowered in {"-c", "--config", "-c"}:
            index += 2
            continue
        if lowered in {"-C", "--git-dir", "--work-tree", "--namespace"}:
            index += 2
            continue
        if lowered.startswith("--git-dir=") or lowered.startswith("--work-tree="):
            index += 1
            continue
        if lowered.startswith("-"):
            index += 1
            continue
        subcommand = lowered
        if subcommand in _LOCAL_GIT_READ_COMMANDS:
            return True
        return subcommand in _LOCAL_GIT_INTEGRATION_COMMANDS and "--no-commit" in {
            item.lower() for item in tokens[index + 1 :]
        }
    return False


__all__ = ["TeamPermissionInterceptor", "TeamRuntimeRole", "TeamToolPolicy"]
