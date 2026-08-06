from __future__ import annotations

import argparse
import asyncio
import sys
from dataclasses import dataclass
from pathlib import Path

from mycode.agent import AgentMode
from mycode.team.context import JsonConversationMemory
from mycode.team.mailbox import MailboxStore
from mycode.team.models import TeamError
from mycode.team.runtime import TeamMemberRuntime
from mycode.team.storage import TeamStore


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
    mailbox = MailboxStore(request.team_name, store=store)
    mailbox.register_lead()
    for current in snapshot.members:
        mailbox.register(current)
    context_path = member.context_path or store.context_path(request.team_name, request.member_name)
    memory = JsonConversationMemory(path=context_path)
    return TeamMemberRuntime(
        team_name=request.team_name,
        member_name=request.member_name,
        store=store,
        mailbox=mailbox,
        memory=memory,
        agent=_MailboxOnlyAgent(),
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
    parser.add_argument("--home", type=Path, default=Path.home(), help="myCode home directory.")
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


__all__ = ["TeamWorkerRequest", "create_worker_runtime", "main"]
