from __future__ import annotations

import json
import logging
import time
from dataclasses import replace
from datetime import datetime, timezone

from mycode.agent import AgentMode
from mycode.agent import AgentEventType
from mycode.team.infrastructure.config import TeamConfig
from mycode.team.infrastructure.context import JsonConversationMemory
from mycode.team.execution.consumer import RoleEventConsumer
from mycode.team.infrastructure.events import TeamEventStore
from mycode.team.execution.notifier import TeamEventNotifier
from mycode.team.domain.models import EventFailure, MemberState, MessageProtocol, TeamError, TeamMessage, TeamTaskState
from mycode.team.infrastructure.requests import TeamRequestState, TeamRequestStore
from mycode.team.infrastructure.storage import TeamStore
from mycode.team.application.tasks import TaskBoard
from mycode.team.tooling.member_tools import register_member_team_tools
from mycode.log_context import use_log_identity

logger = logging.getLogger("mycode.team.runtime")


def _log_fields(**context: object) -> dict[str, object]:
    return {key: value for key, value in context.items() if value is not None and value != ""}


def _elapsed_ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)


class TeamMemberRuntime:
    def __init__(
        self,
        *,
        team_name: str,
        member_name: str,
        store: TeamStore,
        event_store: TeamEventStore,
        notifier: TeamEventNotifier,
        memory: JsonConversationMemory,
        agent,
        tool_registry=None,
        member_tool=None,
        clock=None,
    ) -> None:
        self._team_name = team_name
        self._member_name = member_name
        self._store = store
        self._event_store = event_store
        self._notifier = notifier
        self._memory = memory
        self._agent = agent
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._consumer: RoleEventConsumer | None = None
        self._event_store.register_role("lead")
        self._event_store.register_role(member_name)
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
                        event_store=event_store,
                        notifier=notifier,
                        config=TeamConfig(),
                    ),
                    member_name=member_name,
                )

    async def run_event_consumer(self) -> None:
        with use_log_identity(
            agent_role="member",
            team_name=self._team_name,
            member_name=self._member_name,
        ):
            self._consumer = RoleEventConsumer(
                self._member_name,
                events=self._event_store,
                notifier=self._notifier,
                handler=self._handle_event,
                on_terminal_failure=self._on_terminal_failure,
            )
            try:
                await self._consumer.run()
            finally:
                self._consumer = None

    async def stop_consumer(self) -> None:
        if self._consumer is not None:
            await self._consumer.stop()

    async def run_until_idle(self) -> None:
        """Process all currently pending events and return."""
        with use_log_identity(
            agent_role="member",
            team_name=self._team_name,
            member_name=self._member_name,
        ):
            logger.info(
                "team.runtime.started",
                extra=_log_fields(
                    team_name=self._team_name,
                    member_name=self._member_name,
                ),
            )
            consumer = RoleEventConsumer(
                self._member_name,
                events=self._event_store,
                notifier=self._notifier,
                handler=self._handle_event,
                on_terminal_failure=self._on_terminal_failure,
            )
            await consumer.run_until_idle()

    async def _handle_event(self, event) -> None:
        message = event.message
        event_id = event.event_id
        self._memory.reload()
        if message.message_id in self._memory.applied_message_ids:
            return
        self._set_member_state(MemberState.RUNNING)
        if message.task_id:
            logger.info(
                "team.task.started",
                extra=_log_fields(
                    team_name=self._team_name,
                    member_name=self._member_name,
                    task_id=message.task_id,
                    batch_id=message.batch_id,
                    event_id=event_id,
                    message_id=message.message_id,
                    protocol=message.protocol.value,
                    status="running",
                    task_summary=_log_summary(message.summary),
                ),
            )
        logger.info(
            "team.runtime.message.started",
            extra=_log_fields(
                team_name=self._team_name,
                member_name=self._member_name,
                message_id=message.message_id,
                event_id=event_id,
            ),
        )

        if message.protocol is MessageProtocol.SHUTDOWN_REQUEST:
            await self._handle_shutdown_request(message)
            return

        if message.protocol in {
            MessageProtocol.CLARIFICATION_RESPONSE,
            MessageProtocol.TOOL_APPROVAL_RESPONSE,
        }:
            if not await self._prepare_response(message):
                return

        result_summary = ""
        try:
            with use_log_identity(
                agent_role="member",
                team_name=self._team_name,
                member_name=self._member_name,
                task_id=message.task_id,
                batch_id=message.batch_id,
                event_id=event_id,
            ):
                async for agent_event in self._agent.run(message.body, mode=AgentMode()):
                    if _is_agent_event(agent_event.type, AgentEventType.FINAL_RESPONSE):
                        result_summary = _log_summary(agent_event.content)
                    if _is_agent_event(agent_event.type, AgentEventType.ERROR):
                        raise RuntimeError(agent_event.content or "member agent returned an error")
        except Exception:
            if message.task_id:
                logger.error(
                    "team.task.failed",
                    exc_info=True,
                    extra=_log_fields(
                        team_name=self._team_name,
                        member_name=self._member_name,
                        task_id=message.task_id,
                        batch_id=message.batch_id,
                        event_id=event_id,
                        message_id=message.message_id,
                        status="failed",
                        result_summary="执行失败",
                    ),
                )
            logger.exception(
                "team.runtime.message.failed",
                extra=_log_fields(
                    team_name=self._team_name,
                    member_name=self._member_name,
                    message_id=message.message_id,
                    event_id=event_id,
                    sender=message.sender,
                    task_id=message.task_id,
                    batch_id=message.batch_id,
                ),
            )
            raise

        self._memory.mark_applied(message.message_id)
        checkpoint = self._memory.checkpoint
        checkpoint["last_message_id"] = message.message_id
        self._memory.set_checkpoint(checkpoint)
        if message.task_id:
            logger.info(
                "team.task.result",
                extra=_log_fields(
                    team_name=self._team_name,
                    member_name=self._member_name,
                    task_id=message.task_id,
                    batch_id=message.batch_id,
                    event_id=event_id,
                    message_id=message.message_id,
                    status="completed",
                    result_summary=result_summary or message.summary,
                ),
            )
        logger.info(
            "team.runtime.message.completed",
            extra=_log_fields(
                team_name=self._team_name,
                member_name=self._member_name,
                message_id=message.message_id,
                event_id=event_id,
            ),
        )
        if self._member_is_awaiting_input(message.task_id):
            return

        self._set_member_state(MemberState.IDLE)
        logger.info(
            "team.runtime.idle",
            extra=_log_fields(
                team_name=self._team_name,
                member_name=self._member_name,
            ),
        )
        await self._send_event_message(
            protocol=MessageProtocol.STATUS_UPDATE,
            message_id=f"status-{self._member_name}-{_message_suffix(message.message_id)}",
            body="idle",
            summary="idle",
        )

    async def _handle_shutdown_request(self, message: TeamMessage) -> None:
        checkpoint = self._memory.checkpoint
        checkpoint["last_message_id"] = message.message_id
        checkpoint["shutdown_request_id"] = message.message_id
        self._memory.set_checkpoint(checkpoint)
        self._memory.mark_applied(message.message_id)
        await self.graceful_stop()

    async def graceful_stop(self) -> None:
        started = time.perf_counter()
        logger.info(
            "team.runtime.shutdown.started",
            extra=_log_fields(
                team_name=self._team_name,
                member_name=self._member_name,
            ),
        )
        checkpoint = self._memory.checkpoint
        checkpoint["member_state"] = "stopped"
        self._memory.set_checkpoint(checkpoint)
        self._set_member_state(MemberState.STOPPED)
        response_suffix = _message_suffix(checkpoint.get("shutdown_request_id"))
        await self._send_event_message(
            protocol=MessageProtocol.SHUTDOWN_RESPONSE,
            message_id=f"shutdown-response-{self._member_name}-{response_suffix}",
            body="checkpoint saved",
            summary="checkpoint saved",
        )
        logger.info(
            "team.runtime.shutdown.completed",
            extra=_log_fields(
                team_name=self._team_name,
                member_name=self._member_name,
                state="stopped",
                duration_ms=_elapsed_ms(started),
            ),
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

    def _member_record(self):
        snapshot = self._store.load(self._team_name)
        return next(member for member in snapshot.members if member.member_name == self._member_name)

    def _member_is_awaiting_input(self, task_id: str | None) -> bool:
        if self._member_record().state is MemberState.AWAITING_INPUT:
            return True
        if task_id is None:
            return False
        try:
            task = TaskBoard(self._store, self._team_name).get(task_id)
        except Exception:
            return False
        return task.state is TeamTaskState.AWAITING_INPUT

    async def _prepare_response(self, message: TeamMessage) -> bool:
        if message.protocol not in {
            MessageProtocol.CLARIFICATION_RESPONSE,
            MessageProtocol.TOOL_APPROVAL_RESPONSE,
        }:
            return True
        payload = _decode_message_body(message.body)
        request_id = payload.get("request_id")
        if isinstance(request_id, str) and request_id:
            try:
                requests = TeamRequestStore(self._store)
                request = requests.get(self._team_name, request_id)
                if request.state is TeamRequestState.PENDING:
                    requests.resolve(
                        self._team_name,
                        request_id,
                        state=(
                            TeamRequestState.REJECTED
                            if payload.get("approved") is False
                            else TeamRequestState.RESOLVED
                        ),
                        resolution=str(payload.get("resolution") or message.summary),
                        resolved_by=message.sender,
                    )
            except Exception:
                logger.exception("team.runtime.response.request_update_failed", extra=_log_fields(request_id=request_id))
        task_id = message.task_id or payload.get("task_id")
        if message.protocol is MessageProtocol.TOOL_APPROVAL_RESPONSE and payload.get("approved") is False:
            reason = str(payload.get("resolution") or "tool approval rejected")
            self._set_member_state(MemberState.FAILED)
            self._set_task_failed(reason)
            await self._send_event_message(
                protocol=MessageProtocol.STATUS_UPDATE,
                message_id=f"tool-rejected-{self._member_name}-{_message_suffix(message.message_id)}",
                body=json.dumps({"event": "tool_approval_rejected", "reason": reason}, separators=(",", ":")),
                summary="tool approval rejected",
            )
            return False
        if task_id is not None:
            try:
                board = TaskBoard(self._store, self._team_name, lock_owner=f"{self._member_name}:runtime")
                task = board.get(task_id)
                if task.state is TeamTaskState.AWAITING_INPUT:
                    board.transition(task.task_id, task.revision, TeamTaskState.RUNNING)
            except Exception:
                logger.exception("team.runtime.response.resume_failed", extra=_log_fields(task_id=task_id))
                return False
        self._set_member_state(MemberState.RUNNING)
        return True

    def _set_task_failed(self, error: str) -> None:
        try:
            snapshot = self._store.load(self._team_name)
            member = next(item for item in snapshot.members if item.member_name == self._member_name)
            if member.task_id is None:
                return
            board = TaskBoard(self._store, self._team_name, lock_owner=f"{self._member_name}:runtime")
            task = board.get(member.task_id)
            if task.state in {
                TeamTaskState.CLAIMED,
                TeamTaskState.AWAITING_APPROVAL,
                TeamTaskState.AWAITING_INPUT,
                TeamTaskState.RUNNING,
            }:
                board.transition(
                    task.task_id,
                    task.revision,
                    TeamTaskState.FAILED,
                    error=error[:512] or "member agent failed",
                )
        except Exception:
            return

    def _find_event(self, event_id: str):
        for event in self._event_store.events_for_role(self._member_name):
            if event.event_id == event_id:
                return event
        return None

    async def _on_terminal_failure(self, failure: EventFailure) -> None:
        """重试耗尽后的终态处理：置 FAILED、task FAILED、回报 lead。"""
        event = self._find_event(failure.event_id)
        if event is None:
            logger.warning(
                "team.runtime.terminal_failure.event_missing",
                extra=_log_fields(
                    team_name=self._team_name,
                    member_name=self._member_name,
                ),
            )
            return
        message = event.message
        self._set_member_state(MemberState.FAILED)
        self._set_task_failed(failure.reason)
        await self._send_event_message(
            protocol=MessageProtocol.STATUS_UPDATE,
            message_id=f"failed-{self._member_name}-{_message_suffix(message.message_id)}",
            body=json.dumps(
                {
                    "event": "member_failed",
                    "reason_code": "agent_failed",
                    "message": failure.reason,
                    "task_id": message.task_id,
                    "batch_id": message.batch_id,
                },
                ensure_ascii=False,
                separators=(",", ":"),
            ),
            summary="member failed",
        )

    async def _send_event_message(
        self,
        *,
        protocol: MessageProtocol,
        message_id: str,
        body: str,
        summary: str,
    ) -> None:
        self._event_store.append_message(
            TeamMessage(
                message_id=message_id,
                protocol=protocol,
                sender=self._member_name,
                target_name="lead",
                broadcast=False,
                body=body,
                summary=summary,
                timestamp=self._clock(),
            ),
            recipients=("lead",),
        )
        await self._notifier.notify("lead")


class _MemberRuntimeToolService:
    def __init__(
        self,
        *,
        team_name: str,
        member_name: str,
        store: TeamStore,
        event_store: TeamEventStore,
        config: TeamConfig | None,
        notifier: TeamEventNotifier | None = None,
    ) -> None:
        self._team_name = team_name
        self._member_name = member_name
        self._store = store
        self._event_store = event_store
        self._notifier = notifier
        self._config = config or TeamConfig()

    @property
    def team_name(self) -> str:
        return self._team_name

    def create_request(self, request):
        return TeamRequestStore(self._store).create(request)

    def set_member_state(self, state: MemberState) -> None:
        snapshot = self._store.load(self._team_name)
        updated_members = []
        now = datetime.now(timezone.utc)
        for member in snapshot.members:
            if member.member_name == self._member_name:
                member = replace(
                    member,
                    state=state,
                    revision=member.revision + 1,
                    updated_at=now,
                    last_seen_at=now,
                )
            updated_members.append(member)
        self._store.save(replace(snapshot, members=tuple(updated_members)))

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
        if message.sender != self._member_name:
            raise TeamError(
                code="member_identity_mismatch",
                phase="message",
                message="runtime message sender must match the member identity",
                team_name=self._team_name,
                member_name=self._member_name,
            )
        if message.broadcast:
            recipients = tuple(
                role_name
                for role_name in self._event_store.registered_roles()
                if role_name != self._member_name
            )
        else:
            if message.target_name is None:
                raise TeamError(
                    code="missing_recipient",
                    phase="message",
                    message="direct runtime messages require a target",
                    team_name=self._team_name,
                    member_name=self._member_name,
                )
            recipients = (message.target_name,)
        receipt = self._event_store.append_message(message, recipients=recipients)
        if self._notifier is not None:
            await self._notifier.notify_many(receipt.recipient_names)
        return receipt


def _message_suffix(value: object) -> str:
    return value if type(value) is str and value else "idle"


def _is_agent_event(value: object, expected: AgentEventType) -> bool:
    return value is expected if isinstance(value, AgentEventType) else value == expected.value


def _log_summary(value: object, *, limit: int = 240) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return f"{text[:limit - 3]}..."


def _decode_message_body(body: str) -> dict[str, object]:
    try:
        payload = json.loads(body)
    except (TypeError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


__all__ = ["TeamMemberRuntime"]
