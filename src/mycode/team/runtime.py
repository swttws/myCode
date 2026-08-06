from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone

from mycode.agent import AgentMode
from mycode.team.context import JsonConversationMemory
from mycode.team.mailbox import MailboxStore
from mycode.team.models import MemberState
from mycode.team.storage import TeamStore


class TeamMemberRuntime:
    def __init__(
        self,
        *,
        team_name: str,
        member_name: str,
        store: TeamStore,
        mailbox: MailboxStore,
        memory: JsonConversationMemory,
        agent,
        clock=None,
    ) -> None:
        self._team_name = team_name
        self._member_name = member_name
        self._store = store
        self._mailbox = mailbox
        self._memory = memory
        self._agent = agent
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    async def run_until_idle(self) -> None:
        self._memory.reload()
        for message in self._mailbox.unread(self._member_name):
            if message.message_id in self._memory.applied_message_ids:
                continue
            async for _event in self._agent.run(message.body, mode=AgentMode()):
                pass
            checkpoint = self._memory.checkpoint
            checkpoint["last_message_id"] = message.message_id
            self._memory.set_checkpoint(checkpoint)
            self._memory.mark_applied(message.message_id)
            self._mailbox.acknowledge(self._member_name, message.message_id)
        self._set_member_state(MemberState.IDLE)

    async def graceful_stop(self) -> None:
        checkpoint = self._memory.checkpoint
        checkpoint["member_state"] = "stopped"
        self._memory.set_checkpoint(checkpoint)
        self._set_member_state(MemberState.STOPPED)

    async def resume_from_checkpoint(self) -> None:
        self._memory.reload()

    def _set_member_state(self, state: MemberState) -> None:
        snapshot = self._store.load(self._team_name)
        members = []
        for member in snapshot.members:
            if member.member_name == self._member_name:
                member = replace(
                    member,
                    state=state,
                    revision=member.revision + 1,
                    updated_at=self._clock(),
                    last_seen_at=self._clock(),
                )
            members.append(member)
        self._store.save(replace(snapshot, members=tuple(members)))


__all__ = ["TeamMemberRuntime"]
