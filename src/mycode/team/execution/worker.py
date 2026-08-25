from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from dataclasses import dataclass
from pathlib import Path

from mycode.agent import AgentConfig, AgentLoop
from mycode.config import load_config
from mycode.permission.service import PermissionInterceptor, PermissionService
from mycode.protocols import create_llm
from mycode.skill.context import EphemeralContextManager
from mycode.team.infrastructure.config import TeamConfig
from mycode.team.infrastructure.context import JsonConversationMemory
from mycode.team.infrastructure.events import TeamEventStore
from mycode.team.execution.notifier import TeamEventNotifier
from mycode.team.domain.models import MemberLaunchSpec, TeamError
from mycode.team.tooling.policy import TeamPermissionInterceptor, TeamRuntimeRole, TeamToolPolicy
from mycode.team.execution.runtime import TeamMemberRuntime
from mycode.team.infrastructure.storage import TeamStore
from mycode.team.application.tasks import TaskBoard
from mycode.tool import ToolExecutor, create_default_tool_registry
from mycode.workspace import WorkspaceContext, WorkspaceKind, WorkspaceTaskIdentity


logger = logging.getLogger("mycode.team.worker")


def _worker_context(request: TeamWorkerRequest | None = None, **extra: object) -> dict[str, object]:
    context = {key: value for key, value in extra.items() if value is not None and value != ""}
    if request is not None:
        context.update(
            {
                "team_name": request.team_name,
                "member_name": request.member_name,
                "path": request.home,
            }
        )
    return context


@dataclass(frozen=True)
class TeamWorkerRequest:
    team_name: str
    member_name: str
    home: Path
    config_path: Path | None = None


def main(argv: list[str] | None = None, *, runtime_factory=None) -> int:
    request: TeamWorkerRequest | None = None
    try:
        request = _parse_args(argv)
        logger.info(
            "team.worker.started",
            extra=_worker_context(request, action="start", phase="worker"),
        )
        runtime = (runtime_factory or create_worker_runtime)(request)
        logger.info(
            "team.worker.runtime.created",
            extra=_worker_context(request, action="create_runtime", phase="worker"),
        )
        asyncio.run(_run_runtime(runtime, request=request))
        logger.info(
            "team.worker.completed",
            extra=_worker_context(request, action="run", phase="worker"),
        )
        return 0
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else 2
        return code
    except Exception as exc:
        logger.exception(
            "team.worker.failed",
            extra=_worker_context(request, action="run", phase="worker"),
        )
        print(f"myCode Team worker error: {str(exc) or exc.__class__.__name__}", file=sys.stderr)
        return 1


def create_worker_runtime(
    request: TeamWorkerRequest,
    *,
    notifier: TeamEventNotifier | None = None,
) -> TeamMemberRuntime:
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
    llm_config = load_config(
        explicit_path=request.config_path or os.environ.get("MYCODE_CONFIG"),
        cwd=workspace_root,
        home=request.home,
    )
    team_config = getattr(llm_config, "team", TeamConfig())
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
        event_store=TeamEventStore(request.team_name, store=store, config=team_config),
        notifier=notifier or TeamEventNotifier(),
        memory=memory,
        agent=agent,
        tool_registry=tool_registry,
    )


def create_worker_runtime_from_spec(
    spec: MemberLaunchSpec,
    *,
    home: Path | None = None,
    notifier: TeamEventNotifier | None = None,
) -> TeamMemberRuntime:
    home = home or _home_from_environment(spec.environment)
    return create_worker_runtime(
        TeamWorkerRequest(
            team_name=spec.team_name,
            member_name=spec.member_name,
            home=home.resolve(strict=False),
            config_path=(Path(spec.environment["MYCODE_CONFIG"]) if spec.environment.get("MYCODE_CONFIG") else None),
        ),
        notifier=notifier,
    )


async def _run_runtime(runtime, *, request: TeamWorkerRequest | None = None) -> None:
    started = asyncio.get_running_loop().time()
    logger.info(
        "team.worker.runtime.started",
        extra=_worker_context(request, action="run_runtime", phase="worker"),
)
    await runtime.resume_from_checkpoint()
    await runtime.run_event_consumer()
    logger.info(
        "team.worker.runtime.completed",
        extra=_worker_context(
            request,
            action="run_runtime",
            phase="worker",
            duration_ms=int((asyncio.get_running_loop().time() - started) * 1000),
        ),
    )


def _parse_args(argv: list[str] | None) -> TeamWorkerRequest:
    parser = argparse.ArgumentParser(prog="mycode team-worker")
    parser.add_argument("member", help="Team/member identifier, for example team-a/dev.")
    parser.add_argument("member_name", nargs="?", help="Member name when team and member are separate arguments.")
    parser.add_argument("--home", type=Path, default=_default_home(), help="myCode home directory.")
    parser.add_argument("--config", type=Path, default=None, help="myCode YAML config file.")
    args = parser.parse_args(argv)
    team_name, member_name = _parse_member_reference(args.member, args.member_name)
    return TeamWorkerRequest(
        team_name=team_name,
        member_name=member_name,
        home=args.home.resolve(strict=False),
        config_path=(
            args.config.resolve(strict=False)
            if args.config is not None
            else (Path(os.environ["MYCODE_CONFIG"]).resolve(strict=False) if os.environ.get("MYCODE_CONFIG") else None)
        ),
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
        tool_executor=ToolExecutor(
            tool_registry,
            timeout_seconds=getattr(config, "tool_timeout_seconds", 10.0),
        ),
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
