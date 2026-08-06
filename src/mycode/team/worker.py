from __future__ import annotations

import argparse
import asyncio
import os
import sys
from dataclasses import dataclass
from pathlib import Path

from mycode.agent import AgentConfig, AgentLoop, AgentMode
from mycode.config import load_config
from mycode.permission.service import PermissionInterceptor, PermissionService
from mycode.protocols import create_llm
from mycode.skill.context import EphemeralContextManager
from mycode.team.config import TeamConfig
from mycode.team.context import JsonConversationMemory
from mycode.team.mailbox import MailboxStore
from mycode.team.models import MemberLaunchSpec, TeamError
from mycode.team.policy import TeamPermissionInterceptor, TeamRuntimeRole, TeamToolPolicy
from mycode.team.runtime import TeamMemberRuntime
from mycode.team.storage import TeamStore
from mycode.team.tasks import TaskBoard
from mycode.tool import ToolExecutor, create_default_tool_registry
from mycode.workspace import WorkspaceContext, WorkspaceKind, WorkspaceTaskIdentity


@dataclass(frozen=True)
class TeamWorkerRequest:
    team_name: str
    member_name: str
    home: Path


def main(argv: list[str] | None = None, *, runtime_factory=None) -> int:
    try:
        request = _parse_args(argv)
        runtime = (runtime_factory or create_worker_runtime)(request)
        asyncio.run(_run_runtime(runtime))
        return 0
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else 2
        return code
    except Exception as exc:
        print(f"myCode Team worker error: {str(exc) or exc.__class__.__name__}", file=sys.stderr)
        return 1


def create_worker_runtime(request: TeamWorkerRequest) -> TeamMemberRuntime:
    store = TeamStore(home=request.home)
    snapshot = store.load(request.team_name)
    member = next(
        (candidate for candidate in snapshot.members if candidate.member_name == request.member_name),
        None,
    )
    if member is None:
        raise TeamError(
            code="missing_member",
            phase="worker",
            message=f"missing member: {request.member_name}",
            team_name=request.team_name,
            member_name=request.member_name,
        )
    workspace_root = _member_worktree_root(member)
    llm_config = load_config(cwd=workspace_root, home=request.home)
    team_config = getattr(llm_config, "team", TeamConfig())
    mailbox = MailboxStore(request.team_name, store=store, config=team_config)
    mailbox.register_lead()
    for current in snapshot.members:
        mailbox.register(current)
    context_path = member.context_path or store.context_path(request.team_name, request.member_name)
    memory = JsonConversationMemory(path=context_path, max_bytes=team_config.context_max_bytes)
    tool_registry = create_default_tool_registry(workspace_root)
    agent = _create_member_agent(
        config=llm_config,
        memory=memory,
        tool_registry=tool_registry,
        workspace=_member_workspace(snapshot, member, workspace_root),
        home=request.home,
        member_write_allowed_provider=lambda: _member_workspace_writes_allowed(
            store,
            request.team_name,
            request.member_name,
        ),
    )
    return TeamMemberRuntime(
        team_name=request.team_name,
        member_name=request.member_name,
        store=store,
        mailbox=mailbox,
        memory=memory,
        agent=agent,
        tool_registry=tool_registry,
    )


def create_worker_runtime_from_spec(
    spec: MemberLaunchSpec,
    *,
    home: Path | None = None,
) -> TeamMemberRuntime:
    home = home or _home_from_environment(spec.environment)
    return create_worker_runtime(
        TeamWorkerRequest(
            team_name=spec.team_name,
            member_name=spec.member_name,
            home=home.resolve(strict=False),
        )
    )


async def _run_runtime(runtime) -> None:
    resume_from_checkpoint = getattr(runtime, "resume_from_checkpoint", None)
    if callable(resume_from_checkpoint):
        result = resume_from_checkpoint()
        if asyncio.iscoroutine(result):
            await result
    result = runtime.run_until_idle()
    if asyncio.iscoroutine(result):
        await result


def _parse_args(argv: list[str] | None) -> TeamWorkerRequest:
    parser = argparse.ArgumentParser(prog="mycode team-worker")
    parser.add_argument("member", help="Team/member identifier, for example team-a/dev.")
    parser.add_argument("member_name", nargs="?", help="Member name when team and member are separate arguments.")
    parser.add_argument("--home", type=Path, default=_default_home(), help="myCode home directory.")
    args = parser.parse_args(argv)
    team_name, member_name = _parse_member_reference(args.member, args.member_name)
    return TeamWorkerRequest(
        team_name=team_name,
        member_name=member_name,
        home=args.home.resolve(strict=False),
    )


def _parse_member_reference(first: str, second: str | None) -> tuple[str, str]:
    if second is not None:
        _require_non_empty("team_name", first)
        _require_non_empty("member_name", second)
        return first, second
    if "/" not in first:
        raise ValueError("member reference must use <team>/<member>")
    team_name, member_name = first.split("/", 1)
    _require_non_empty("team_name", team_name)
    _require_non_empty("member_name", member_name)
    return team_name, member_name


def _require_non_empty(name: str, value: str) -> None:
    if type(value) is not str or not value:
        raise ValueError(f"{name} must be a non-empty string")


class _MailboxOnlyAgent:
    async def run(self, user_text: str, *, mode: AgentMode, approval_provider=None):
        if False:
            yield None


def _create_member_agent(
    *,
    config,
    memory: JsonConversationMemory,
    tool_registry,
    workspace: WorkspaceContext,
    home: Path,
    member_write_allowed_provider=None,
):
    permissions = PermissionService.create(workspace.root, home=home)
    policy = TeamToolPolicy(
        role=TeamRuntimeRole.MEMBER,
        member_write_allowed_provider=member_write_allowed_provider,
    )
    permission = TeamPermissionInterceptor(
        policy_provider=lambda: policy,
        permission=PermissionInterceptor(permissions),
    )
    return AgentLoop(
        llm=create_llm(config),
        memory=memory,
        tool_executor=ToolExecutor(tool_registry),
        tool_registry=tool_registry,
        permission=permission,
        context_manager=EphemeralContextManager(memory),
        config=AgentConfig(),
        main_model_id=getattr(config, "model", None),
        workspace=workspace,
        permission_mode_provider=permissions.effective_mode,
        visible_tool_names_provider=policy.visible_names,
    )


def _member_workspace_writes_allowed(store: TeamStore, team_name: str, member_name: str) -> bool:
    snapshot = store.load(team_name)
    member = next((item for item in snapshot.members if item.member_name == member_name), None)
    if member is None:
        return False
    if not member.approval_required:
        return True
    if member.task_id is None:
        return False
    task = TaskBoard(store, team_name).get(member.task_id)
    return task.state.value == "running" and task.approval_state.value == "approved"


def _member_workspace(snapshot, member, workspace_root: Path) -> WorkspaceContext:
    task_id = member.task_id or member.member_name
    branch_name = member.branch_name or f"mycode/team/{snapshot.team.team_name}/{member.member_name}"
    identity = WorkspaceTaskIdentity(
        repository_id=snapshot.team.repository_id,
        task_id=task_id,
        role_name=member.role_name,
        task_token=member.member_name,
        relative_name=f"{snapshot.team.team_name}/{member.member_name}",
        branch_name=branch_name,
        base_commit=_member_base_commit(snapshot, member),
    )
    return WorkspaceContext(
        kind=WorkspaceKind.WORKTREE,
        root=workspace_root,
        repository_root=snapshot.team.repository_root,
        repository_id=snapshot.team.repository_id,
        task_identity=identity,
        branch_name=branch_name,
        hooks_path=None,
    )


def _member_base_commit(snapshot, member) -> str:
    for batch in snapshot.batches:
        if batch.batch_id == member.batch_id:
            return batch.baseline_commit
    return "0" * 40


def _member_worktree_root(member) -> Path:
    if member.worktree_root is None:
        raise TeamError(
            code="missing_worktree",
            phase="worker",
            message="member worktree root is missing",
            member_name=member.member_name,
        )
    return member.worktree_root


def _home_from_environment(environment) -> Path:
    return Path(environment.get("MYCODE_HOME") or os.environ.get("MYCODE_HOME") or Path.home())


def _default_home() -> Path:
    return Path(os.environ.get("MYCODE_HOME") or Path.home())


__all__ = [
    "TeamWorkerRequest",
    "create_worker_runtime",
    "create_worker_runtime_from_spec",
    "main",
]
