from __future__ import annotations

import json
from pathlib import Path

import pytest

from mycode.llm import ChatMessage, MessageOrigin
from mycode.team import TeamError
from mycode.team.context import JsonConversationMemory


def _read_payload(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def test_context_json_conversation_memory_appends_replaces_and_clears(tmp_path: Path):
    path = tmp_path / "context.json"
    memory = JsonConversationMemory(path=path, max_bytes=4096)

    memory.append(ChatMessage(role="user", content="hello"))
    memory.append(
        ChatMessage(
            role="assistant",
            content="ready",
            tool_call_id="call-1",
            tool_name="tool",
            tool_arguments="{\"x\":1}",
            origin=MessageOrigin.SYSTEM_REMINDER,
        )
    )

    assert memory.messages() == [
        ChatMessage(role="user", content="hello"),
        ChatMessage(
            role="assistant",
            content="ready",
            tool_call_id="call-1",
            tool_name="tool",
            tool_arguments="{\"x\":1}",
            origin=MessageOrigin.SYSTEM_REMINDER,
        ),
    ]
    payload = _read_payload(path)
    assert payload["version"] == 1
    assert payload["schema_version"] == 1
    assert len(payload["messages"]) == 2
    assert payload["messages"][1]["origin"] == MessageOrigin.SYSTEM_REMINDER.value

    memory.replace([ChatMessage(role="user", content="replaced")])
    assert memory.messages() == [ChatMessage(role="user", content="replaced")]

    memory.clear()
    assert memory.messages() == []
    assert _read_payload(path)["messages"] == []


def test_context_json_conversation_memory_reload_picks_up_external_changes(tmp_path: Path):
    path = tmp_path / "context.json"
    memory = JsonConversationMemory(path=path, max_bytes=4096)
    memory.append(ChatMessage(role="user", content="initial"))

    path.write_text(
        json.dumps(
            {
                "version": 1,
                "schema_version": 1,
                "messages": [
                    {
                        "role": "assistant",
                        "content": "external",
                        "origin": "conversation",
                    }
                ],
            },
            ensure_ascii=False,
            sort_keys=True,
        ),
        encoding="utf-8",
    )

    memory.reload()

    assert memory.messages() == [ChatMessage(role="assistant", content="external")]


def test_context_json_conversation_memory_rejects_corrupt_files_without_overwriting(tmp_path: Path):
    path = tmp_path / "context.json"
    memory = JsonConversationMemory(path=path, max_bytes=4096)
    memory.append(ChatMessage(role="user", content="keep me"))
    original = path.read_text(encoding="utf-8")
    path.write_text("{not-json", encoding="utf-8")

    with pytest.raises(TeamError, match="corrupt"):
        memory.reload()

    assert memory.messages() == [ChatMessage(role="user", content="keep me")]
    assert path.read_text(encoding="utf-8") == "{not-json"
    assert original != path.read_text(encoding="utf-8")


def test_context_json_conversation_memory_enforces_size_limit(tmp_path: Path):
    path = tmp_path / "context.json"
    memory = JsonConversationMemory(path=path, max_bytes=220)
    memory.append(ChatMessage(role="user", content="small"))

    with pytest.raises(TeamError, match="exceeds"):
        memory.append(ChatMessage(role="assistant", content="x" * 300))

    assert memory.messages() == [ChatMessage(role="user", content="small")]
    assert len(path.read_text(encoding="utf-8").encode("utf-8")) <= 220


import asyncio
from dataclasses import replace
from datetime import datetime, timezone

from mycode.team import (
    MemberBackend,
    MemberRecord,
    MemberState,
    MessageProtocol,
    ResolvedBackend,
    TeamMessage,
    TeamRecord,
    TeamState,
    WakeEndpoint,
)
from mycode.team.config import TeamConfig
from mycode.team.mailbox import MailboxStore
from mycode.team.runtime import TeamMemberRuntime
from mycode.team.storage import TeamStore


NOW = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)


class FakeAgentLoop:
    def __init__(self) -> None:
        self.prompts = []
        self.clear_count = 0

    async def run(self, user_text, *, mode, approval_provider=None):
        self.prompts.append(user_text)
        yield type("Event", (), {"type": "final_response", "content": "done"})()

    def clear_memory(self):
        self.clear_count += 1


def make_member(store: TeamStore, tmp_path: Path) -> MemberRecord:
    endpoint = WakeEndpoint(
        member_name="dev",
        backend=ResolvedBackend.IN_PROCESS,
        endpoint="in-process:dev",
        revision=1,
    )
    return MemberRecord(
        member_name="dev",
        role_name="general",
        role_revision=1,
        requested_backend=MemberBackend.IN_PROCESS,
        resolved_backend=ResolvedBackend.IN_PROCESS,
        state=MemberState.RUNNING,
        worktree_root=tmp_path / "worktree",
        branch_name="mycode/team-a/dev",
        mailbox_path=store.mailbox_path("team-a", "dev"),
        context_path=store.context_path("team-a", "dev"),
        wake_endpoint=endpoint,
        task_id="task-1",
        batch_id="batch-1",
        created_at=NOW,
        updated_at=NOW,
    )


def make_runtime(tmp_path: Path):
    store = TeamStore(home=tmp_path / "home")
    store.create(
        TeamRecord(
            team_name="team-a",
            repository_root=tmp_path,
            repository_id="repo-123",
            target_branch="main",
            state=TeamState.ACTIVE,
            created_at=NOW,
            updated_at=NOW,
        )
    )
    member = make_member(store, tmp_path)
    store.write_member("team-a", member)
    mailbox = MailboxStore("team-a", store=store, config=TeamConfig())
    mailbox.register(member)
    memory = JsonConversationMemory(path=member.context_path)
    agent = FakeAgentLoop()
    runtime = TeamMemberRuntime(
        team_name="team-a",
        member_name="dev",
        store=store,
        mailbox=mailbox,
        memory=memory,
        agent=agent,
    )
    return runtime, store, mailbox, memory, agent, member


def test_member_runtime_injects_unread_messages_then_acks_after_checkpoint(tmp_path: Path):
    runtime, store, mailbox, memory, agent, member = make_runtime(tmp_path)
    mailbox.register(
        replace(
            member,
            member_name="lead",
            mailbox_path=store.mailbox_path("team-a", "lead"),
            context_path=store.context_path("team-a", "lead"),
            wake_endpoint=WakeEndpoint(
                member_name="lead",
                backend=ResolvedBackend.IN_PROCESS,
                endpoint="in-process:lead",
                revision=1,
            ),
        )
    )
    mailbox.send(
        TeamMessage(
            message_id="msg-1",
            protocol=MessageProtocol.MESSAGE,
            sender="lead",
            target_name="dev",
            broadcast=False,
            body="please continue",
            summary="continue",
            timestamp=NOW,
        )
    )

    asyncio.run(runtime.run_until_idle())

    assert agent.prompts == ["please continue"]
    assert memory.applied_message_ids == ("msg-1",)
    assert memory.checkpoint["last_message_id"] == "msg-1"
    assert mailbox.unread("dev") == ()
    assert store.load("team-a").members[0].state is MemberState.IDLE
    lead_messages = mailbox.receive("lead")
    assert len(lead_messages) == 1
    assert lead_messages[0].protocol is MessageProtocol.STATUS_UPDATE
    assert lead_messages[0].sender == "dev"


def test_member_runtime_deduplicates_replayed_messages(tmp_path: Path):
    runtime, _store, _mailbox, memory, agent, _member = make_runtime(tmp_path)
    memory.set_checkpoint({"last_message_id": "msg-1"})
    memory.set_applied_message_ids(("msg-1",))

    asyncio.run(runtime.run_until_idle())

    assert agent.prompts == []
    assert memory.applied_message_ids == ("msg-1",)


def test_member_runtime_checkpoint_helpers_are_stable(tmp_path: Path):
    runtime, _store, mailbox, memory, agent, _member = make_runtime(tmp_path)
    memory.append(ChatMessage(role="user", content="old"))

    asyncio.run(runtime.graceful_stop())
    asyncio.run(runtime.resume_from_checkpoint())

    assert memory.checkpoint["member_state"] == "stopped"
    assert memory.messages() == [ChatMessage(role="user", content="old")]
    assert agent.clear_count == 0
    lead_messages = mailbox.receive("lead")
    assert len(lead_messages) == 1
    assert lead_messages[0].protocol is MessageProtocol.SHUTDOWN_RESPONSE


def test_member_runtime_shutdown_request_triggers_shutdown_response(tmp_path: Path):
    runtime, store, mailbox, memory, agent, member = make_runtime(tmp_path)
    mailbox.register(
        replace(
            member,
            member_name="lead",
            mailbox_path=store.mailbox_path("team-a", "lead"),
            context_path=store.context_path("team-a", "lead"),
            wake_endpoint=WakeEndpoint(
                member_name="lead",
                backend=ResolvedBackend.IN_PROCESS,
                endpoint="in-process:lead",
                revision=1,
            ),
        )
    )
    mailbox.send(
        TeamMessage(
            message_id="shutdown-dev-1",
            protocol=MessageProtocol.SHUTDOWN_REQUEST,
            sender="lead",
            target_name="dev",
            broadcast=False,
            body="shutdown now",
            summary="shutdown now",
            timestamp=NOW,
        )
    )

    asyncio.run(runtime.run_until_idle())

    assert memory.checkpoint["member_state"] == "stopped"
    assert memory.checkpoint["shutdown_request_id"] == "shutdown-dev-1"
    responses = mailbox.receive("lead")
    assert len(responses) == 1
    assert responses[0].protocol is MessageProtocol.SHUTDOWN_RESPONSE
    assert responses[0].message_id.startswith("shutdown-response-dev-shutdown-dev-1")
    assert agent.prompts == []


def test_member_runtime_registers_team_member_tool_when_registry_is_provided(tmp_path: Path):
    class Registry:
        def __init__(self):
            self.registered = []

        def register(self, tool):
            self.registered.append(tool)

    _runtime, store, mailbox, memory, agent, _member = make_runtime(tmp_path)
    registry = Registry()

    TeamMemberRuntime(
        team_name="team-a",
        member_name="dev",
        store=store,
        mailbox=mailbox,
        memory=memory,
        agent=agent,
        tool_registry=registry,
    )

    assert [tool.definition.name for tool in registry.registered] == ["team_member"]


def test_member_runtime_team_member_tool_can_send_status_to_lead_mailbox(tmp_path: Path):
    class Registry:
        def __init__(self):
            self.registered = []

        def register(self, tool):
            self.registered.append(tool)

    _runtime, store, mailbox, _memory, agent, _member = make_runtime(tmp_path)
    registry = Registry()
    TeamMemberRuntime(
        team_name="team-a",
        member_name="dev",
        store=store,
        mailbox=mailbox,
        memory=JsonConversationMemory(path=store.context_path("team-a", "dev")),
        agent=agent,
        tool_registry=registry,
    )

    result = asyncio.run(
        registry.registered[0].execute_async(
            {
                "action": "status_update",
                "message_id": "status-1",
                "body": "idle",
            }
        )
    )

    assert result.ok is True
    messages = mailbox.receive("lead")
    assert len(messages) == 1
    assert messages[0].protocol is MessageProtocol.STATUS_UPDATE
    assert messages[0].sender == "dev"
