from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone

from mycode.agent import AgentMode
from mycode.team.config import TeamConfig
from mycode.team.context import JsonConversationMemory
from mycode.team.mailbox import MailboxStore
from mycode.team.models import MemberState, MessageProtocol, TeamMessage, TeamTaskState
from mycode.team.storage import TeamStore
from mycode.team.tasks import TaskBoard
from mycode.team.tools import register_member_team_tools


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
        tool_registry=None,
        member_tool=None,
        clock=None,
    ) -> None:
        self._team_name = team_name
        self._member_name = member_name
        self._store = store
        self._mailbox = mailbox
        self._memory = memory
        self._agent = agent
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._mailbox.register_lead()
        if tool_registry is not None:
            if member_tool is not None:
                tool_registry.register(member_tool)
            else:
                register_member_team_tools(
                    tool_registry,
                    _MemberRuntimeToolService(
                        team_name=team_name,
                        member_name=member_name,
                        store=store,
                        mailbox=mailbox,
                        config=getattr(mailbox, "_config", None),
                    ),
                    member_name=member_name,
                )

    async def run_until_idle(self) -> None:
        self._memory.reload()
        for message in self._mailbox.unread(self._member_name):
            if message.message_id in self._memory.applied_message_ids:
                continue
            if message.protocol is MessageProtocol.SHUTDOWN_REQUEST:
                checkpoint = self._memory.checkpoint
                checkpoint["last_message_id"] = message.message_id
                checkpoint["shutdown_request_id"] = message.message_id
                self._memory.set_checkpoint(checkpoint)
                self._memory.mark_applied(message.message_id)
                self._mailbox.acknowledge(self._member_name, message.message_id)
                await self.graceful_stop()
                return
            try:
                async for _event in self._agent.run(message.body, mode=AgentMode()):
                    pass
            except Exception as exc:
                self._set_member_state(MemberState.BLOCKED)
                self._set_task_blocked(str(exc) or exc.__class__.__name__)
                self._send_member_protocol_message(
                    protocol=MessageProtocol.STATUS_UPDATE,
                    message_id=f"blocked-{self._member_name}-{_message_suffix(message.message_id)}",
                    body="blocked",
                    summary="blocked",
                )
                return
            checkpoint = self._memory.checkpoint
            checkpoint["last_message_id"] = message.message_id
            self._memory.set_checkpoint(checkpoint)
            self._memory.mark_applied(message.message_id)
            self._mailbox.acknowledge(self._member_name, message.message_id)
        self._set_member_state(MemberState.IDLE)
        self._send_member_protocol_message(
            protocol=MessageProtocol.STATUS_UPDATE,
            message_id=f"status-{self._member_name}-{_message_suffix(self._memory.checkpoint.get('last_message_id'))}",
            body="idle",
            summary="idle",
        )

    async def graceful_stop(self) -> None:
        checkpoint = self._memory.checkpoint
        checkpoint["member_state"] = "stopped"
        self._memory.set_checkpoint(checkpoint)
        self._set_member_state(MemberState.STOPPED)
        response_suffix = _message_suffix(checkpoint.get("shutdown_request_id"))
        self._send_member_protocol_message(
            protocol=MessageProtocol.SHUTDOWN_RESPONSE,
            message_id=f"shutdown-response-{self._member_name}-{response_suffix}",
            body="checkpoint saved",
            summary="checkpoint saved",
        )

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

    def _set_task_blocked(self, error: str) -> None:
        try:
            snapshot = self._store.load(self._team_name)
            member = next(item for item in snapshot.members if item.member_name == self._member_name)
            if member.task_id is None:
                return
            board = TaskBoard(self._store, self._team_name, lock_owner=f"{self._member_name}:runtime")
            task = board.get(member.task_id)
            if task.state in {TeamTaskState.CLAIMED, TeamTaskState.AWAITING_APPROVAL, TeamTaskState.RUNNING}:
                board.transition(
                    task.task_id,
                    task.revision,
                    TeamTaskState.BLOCKED,
                    error=error[:512] or "member agent failed",
                )
        except Exception:
            return

    def _send_member_protocol_message(
        self,
        *,
        protocol: MessageProtocol,
        message_id: str,
        body: str,
        summary: str,
    ) -> None:
        self._mailbox.send(
            TeamMessage(
                message_id=message_id,
                protocol=protocol,
                sender=self._member_name,
                target_name="lead",
                broadcast=False,
                body=body,
                summary=summary,
                timestamp=self._clock(),
            )
        )


class _MemberRuntimeToolService:
    def __init__(
        self,
        *,
        team_name: str,
        member_name: str,
        store: TeamStore,
        mailbox: MailboxStore,
        config: TeamConfig | None,
    ) -> None:
        self._team_name = team_name
        self._member_name = member_name
        self._store = store
        self._mailbox = mailbox
        self._config = config or TeamConfig()

    @property
    def task_board(self) -> TaskBoard:
        return TaskBoard(
            self._store,
            self._team_name,
            config=self._config,
            lock_owner=f"{self._member_name}:member-runtime",
        )

    def create_task(self, task):
        return self.task_board.create(task)

    def list_tasks(self, batch_id: str | None = None):
        return self.task_board.list(batch_id)

    def get_task(self, task_id: str):
        return self.task_board.get(task_id)

    def update_task(self, task_id: str, expected_revision: int, patch):
        return self.task_board.update(task_id, expected_revision, patch)

    def claim_task(self, task_id: str, member_name: str, expected_revision: int):
        return self.task_board.claim(task_id, member_name, expected_revision)

    def transition_task(self, task_id: str, expected_revision: int, state, result=None, error=None):
        return self.task_board.transition(task_id, expected_revision, state, result, error)

    async def send_message(self, message):
        return self._mailbox.send(message)


def _message_suffix(value: object) -> str:
    return value if type(value) is str and value else "idle"


__all__ = ["TeamMemberRuntime"]
