from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Iterable

from mycode.team.tooling.tool_names import MEMBER_TEAM_TOOL_NAMES, PARENT_TEAM_TOOL_NAMES


class TeamRuntimeRole(str, Enum):
    """当前 Agent 在 Team 运行时中的职责角色。"""

    PARENT = "parent"  # 尚未激活团队的普通主会话。
    LEAD = "lead"  # 负责团队编排的唯一根 Agent。
    MEMBER = "member"  # 执行绑定任务的成员 Agent。


class TeamPhase(str, Enum):
    """Lead 可见工具和下一步动作对应的团队阶段。"""

    INACTIVE = "inactive"  # 没有激活团队。
    LEAD_READY = "lead_ready"  # 团队已激活，等待批次规划。
    TASK_PLANNING = "task_planning"  # 批次已启动，正在创建和规划任务。
    DISPATCH_READY = "dispatch_ready"  # 存在可派发任务，可以创建成员。
    EXECUTING = "executing"  # 至少一个任务或成员正在执行。
    INTEGRATING = "integrating"  # 任务完成，等待本地整合。
    ARCHIVED = "archived"  # 团队已归档，只允许读取。


class SupervisorState(str, Enum):
    """后台 Lead Supervisor 的生命周期状态。"""

    IDLE = "idle"  # 没有待运行的 Lead 事件。
    RUNNING_LEAD = "running_lead"  # 当前正在运行 Lead AgentLoop。
    WAITING_MEMBER = "waiting_member"  # batch 未完成，等待成员事件。
    WAITING_USER = "waiting_user"  # 等待用户解决业务决策请求。
    COMPLETED = "completed"  # 当前 batch 已完成。
    FAILED = "failed"  # Supervisor 遇到无法自动恢复的错误。
    STOPPING = "stopping"  # 正在停止后台任务并保存状态。


@dataclass(frozen=True)
class TeamRuntimeState:
    role: TeamRuntimeRole
    phase: TeamPhase
    team_name: str | None = None
    batch_id: str | None = None
    manifest_epoch: int = 0
    coordinator_mode: bool = False
    ordinary_agent_allowed: bool = True
    local_write_allowed: bool = True
    command_allowed: bool = True

    def __post_init__(self) -> None:
        if not isinstance(self.role, TeamRuntimeRole):
            raise ValueError("role must be a TeamRuntimeRole")
        if not isinstance(self.phase, TeamPhase):
            raise ValueError("phase must be a TeamPhase")
        if self.team_name is not None and not self.team_name.strip():
            raise ValueError("team_name must not be empty")
        if self.batch_id is not None and not self.batch_id.strip():
            raise ValueError("batch_id must not be empty")
        if isinstance(self.manifest_epoch, bool) or type(self.manifest_epoch) is not int or self.manifest_epoch < 0:
            raise ValueError("manifest_epoch must be a non-negative integer")
        for name in (
            "coordinator_mode",
            "ordinary_agent_allowed",
            "local_write_allowed",
            "command_allowed",
        ):
            if type(getattr(self, name)) is not bool:
                raise ValueError(f"{name} must be a bool")
        if self.phase is TeamPhase.INACTIVE:
            if self.team_name is not None or self.batch_id is not None:
                raise ValueError("inactive state cannot contain team or batch")
            if self.role is not TeamRuntimeRole.PARENT:
                raise ValueError("inactive state must use parent role")
        elif self.team_name is None:
            raise ValueError("active state must contain team_name")
        if self.role is TeamRuntimeRole.LEAD and not self.ordinary_agent_allowed:
            return

    @classmethod
    def inactive(cls) -> TeamRuntimeState:
        return cls(
            role=TeamRuntimeRole.PARENT,
            phase=TeamPhase.INACTIVE,
            coordinator_mode=False,
            ordinary_agent_allowed=True,
            local_write_allowed=True,
            command_allowed=True,
        )

    def prompt_context(self) -> str:
        if self.phase is TeamPhase.INACTIVE:
            return "当前未激活团队。你可以创建或接管团队。"
        role_text = {
            TeamRuntimeRole.LEAD: "唯一 Team Lead（根 Agent）",
            TeamRuntimeRole.MEMBER: "Team Member",
            TeamRuntimeRole.PARENT: "普通会话 Agent",
        }[self.role]
        phase_text = {
            TeamPhase.LEAD_READY: "Lead 已就绪，等待批次规划",
            TeamPhase.TASK_PLANNING: "批次已启动，正在规划任务",
            TeamPhase.DISPATCH_READY: "任务已就绪，可以派生成员",
            TeamPhase.EXECUTING: "成员正在执行任务",
            TeamPhase.INTEGRATING: "任务已完成，可以进行本地整合",
            TeamPhase.ARCHIVED: "团队已归档，只读",
            TeamPhase.INACTIVE: "团队未激活",
        }[self.phase]
        allowed = "、".join(self.allowed_actions_zh()) or "无"
        forbidden = "、".join(self.forbidden_actions_zh()) or "无"
        lead_rule = (
            "Lead 必须自主完成 dispatch、request、response、integrate 链路；"
            "只有业务判断不确定时才创建用户决策请求，不询问用户工具名称。"
            if self.role is TeamRuntimeRole.LEAD
            else ""
        )
        return (
            f"当前角色：{role_text}；团队：{self.team_name}；阶段：{phase_text}。"
            f"允许动作：{allowed}。禁止动作：{forbidden}。{lead_rule}"
        )

    def allowed_actions_zh(self) -> tuple[str, ...]:
        if self.phase is TeamPhase.INACTIVE:
            return ("创建团队", "接管团队")
        if self.role is TeamRuntimeRole.MEMBER:
            return ("读取成员任务", "执行已批准任务", "提交状态")
        if self.phase is TeamPhase.LEAD_READY:
            return ("读取状态", "启动批次", "归档团队")
        if self.phase is TeamPhase.TASK_PLANNING:
            return ("读取状态", "创建和管理任务", "审批计划", "发送消息")
        if self.phase is TeamPhase.DISPATCH_READY:
            return ("读取状态", "派生成员", "管理任务", "发送消息")
        if self.phase is TeamPhase.EXECUTING:
            return ("读取状态", "审批计划", "发送消息", "停止成员")
        if self.phase is TeamPhase.INTEGRATING:
            return ("读取状态", "本地整合", "归档团队")
        return ("读取状态",)

    def forbidden_actions_zh(self) -> tuple[str, ...]:
        if self.phase is TeamPhase.INACTIVE:
            return ("访问 Lead/Member 工具",)
        if self.role is TeamRuntimeRole.MEMBER:
            return ("管理团队生命周期", "派生成员", "执行本地整合", "启动普通 Agent")
        actions = ["启动普通 Agent", "直接修改业务代码"]
        if self.phase is not TeamPhase.DISPATCH_READY:
            actions.append("派生成员")
        if self.phase is not TeamPhase.INTEGRATING:
            actions.append("本地整合")
        return tuple(actions)


@dataclass(frozen=True)
class TeamToolManifest:
    epoch: int
    role: TeamRuntimeRole
    phase: TeamPhase
    visible_names: frozenset[str]
    next_actions_zh: tuple[str, ...]
    forbidden_actions_zh: tuple[str, ...]


def phase_for_snapshot(
    *,
    active: bool,
    archived: bool = False,
    has_batch: bool = False,
    has_tasks: bool = False,
    has_dispatchable_task: bool = False,
    has_running_work: bool = False,
    all_tasks_completed: bool = False,
) -> TeamPhase:
    if not active:
        return TeamPhase.INACTIVE
    if archived:
        return TeamPhase.ARCHIVED
    if not has_batch:
        return TeamPhase.LEAD_READY
    if all_tasks_completed:
        return TeamPhase.INTEGRATING
    if has_running_work:
        return TeamPhase.EXECUTING
    if has_dispatchable_task:
        return TeamPhase.DISPATCH_READY
    if not has_tasks:
        return TeamPhase.TASK_PLANNING
    return TeamPhase.TASK_PLANNING


def build_tool_manifest(state: TeamRuntimeState, candidates: Iterable[str]) -> TeamToolManifest:
    candidate_names = frozenset(candidates)
    if state.role is TeamRuntimeRole.PARENT:
        allowed = PARENT_TEAM_TOOL_NAMES | _ordinary_tools()
    elif state.role is TeamRuntimeRole.MEMBER:
        allowed = MEMBER_TEAM_TOOL_NAMES | frozenset({"read_file"})
        if state.local_write_allowed:
            allowed |= frozenset({"write_file", "edit_file"})
        if state.command_allowed:
            allowed |= frozenset({"run_command"})
    else:
        allowed = _lead_tools_for_phase(state)
        allowed |= frozenset({"read_file", "find_files", "search_code"})
        if state.command_allowed:
            allowed |= frozenset({"run_command"})
    return TeamToolManifest(
        epoch=state.manifest_epoch,
        role=state.role,
        phase=state.phase,
        visible_names=frozenset(name for name in candidate_names if name in allowed),
        next_actions_zh=state.allowed_actions_zh(),
        forbidden_actions_zh=state.forbidden_actions_zh(),
    )


def _ordinary_tools() -> frozenset[str]:
    return frozenset({"Agent", "find_files", "search_code", "read_file", "write_file", "edit_file", "run_command"})


def _lead_tools_for_phase(state: TeamRuntimeState) -> frozenset[str]:
    common = frozenset({
        "team_status",
        "team_archive",
        "team_message_send",
        "team_shutdown_request",
        "team_request_list",
        "team_clarification_respond",
        "team_tool_approval_respond",
        "team_user_decision_request",
    })
    task_tool_set = frozenset({
        "team_task_create",
        "team_task_list",
        "team_task_get",
        "team_task_update",
        "team_task_delete",
        "team_task_claim",
        "team_task_transition",
        "team_plan_decide",
    })
    if state.phase is TeamPhase.LEAD_READY:
        return common | frozenset({"team_batch_start"})
    if state.phase is TeamPhase.TASK_PLANNING:
        return common | frozenset({"team_batch_start"}) | task_tool_set
    if state.phase is TeamPhase.DISPATCH_READY:
        return common | task_tool_set | frozenset({"team_member_spawn", "team_member_terminate"})
    if state.phase is TeamPhase.EXECUTING:
        return common | task_tool_set | frozenset({"team_member_terminate"})
    if state.phase is TeamPhase.INTEGRATING:
        return frozenset({"team_status", "team_archive", "team_batch_integrate"}) | task_tool_set
    if state.phase is TeamPhase.ARCHIVED:
        return frozenset({"team_status"})
    return common


__all__ = [
    "SupervisorState",
    "TeamPhase",
    "TeamRuntimeRole",
    "TeamRuntimeState",
    "TeamToolManifest",
    "build_tool_manifest",
    "phase_for_snapshot",
]
