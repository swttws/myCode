from __future__ import annotations

import asyncio
from dataclasses import MISSING, fields, is_dataclass
from datetime import datetime, timezone
from types import SimpleNamespace
from pathlib import Path

import pytest

from mycode.agent import AgentLoop, AgentMode
from mycode.compact import models as compact_models
from mycode.compact.manager import ContextManager
from mycode.compact.models import CompactConfig, ContextTokenStatus
from mycode.llm import ChatMessage, MessageOrigin
from mycode.memory import models as memory_models
from mycode.memory.base import ConversationMemory
from mycode.memory.manager import ProjectMemoryManager
from mycode.memory.paths import MemoryPaths
from mycode.memory.sessions import SessionArchiveStore
from mycode.prompt.models import EnvironmentSnapshot, TurnPromptContext
from mycode.session import ChatSession
from mycode.tool import ToolDefinition, ToolKind


def test_compact_context_token_status_is_exported_and_frozen():
    import mycode.compact as compact_package

    assert hasattr(compact_package, "ContextTokenStatus")
    model = compact_package.ContextTokenStatus

    assert model is compact_models.ContextTokenStatus
    assert is_dataclass(model)
    assert model.__dataclass_params__.frozen is True
    assert [(field.name, field.default) for field in fields(model)] == [
        ("estimated_tokens", MISSING),
        ("context_window_tokens", MISSING),
        ("usage_ratio", MISSING),
        ("source", MISSING),
    ]


def test_memory_session_and_scope_status_models_are_exported_and_frozen():
    import mycode.memory as memory_package

    for name in (
        "SessionSource",
        "SessionStatusSnapshot",
        "MemoryScopeStatus",
        "MemoryStatusSnapshot",
    ):
        assert hasattr(memory_package, name)

    assert memory_package.SessionSource is memory_models.SessionSource
    assert memory_package.SessionStatusSnapshot is memory_models.SessionStatusSnapshot
    assert memory_package.MemoryScopeStatus is memory_models.MemoryScopeStatus
    assert memory_package.MemoryStatusSnapshot is memory_models.MemoryStatusSnapshot

    assert memory_models.SessionSource.NEW.value == "new"
    assert memory_models.SessionSource.RESTORED.value == "restored"

    assert is_dataclass(memory_models.SessionStatusSnapshot)
    assert is_dataclass(memory_models.MemoryScopeStatus)
    assert is_dataclass(memory_models.MemoryStatusSnapshot)

    assert memory_models.SessionStatusSnapshot.__dataclass_params__.frozen is True
    assert memory_models.MemoryScopeStatus.__dataclass_params__.frozen is True
    assert memory_models.MemoryStatusSnapshot.__dataclass_params__.frozen is True

    assert [(field.name, field.default) for field in fields(memory_models.SessionStatusSnapshot)] == [
        ("session_id", MISSING),
        ("message_count", MISSING),
        ("source", MISSING),
        ("restored_from_session_id", None),
        ("updated_at", None),
    ]
    assert [(field.name, field.default) for field in fields(memory_models.MemoryScopeStatus)] == [
        ("scope", MISSING),
        ("path", MISSING),
        ("note_count", MISSING),
        ("index_line_count", MISSING),
        ("index_byte_count", MISSING),
        ("diagnostic_codes", MISSING),
    ]
    assert [(field.name, field.default) for field in fields(memory_models.MemoryStatusSnapshot)] == [
        ("user", MISSING),
        ("project", MISSING),
        ("diagnostic_codes", MISSING),
    ]


def test_context_manager_estimate_current_returns_token_status_without_mutation():
    class Memory:
        def __init__(self) -> None:
            self.replace_called = False
            self._messages = [
                ChatMessage(role="user", content="hello", origin=MessageOrigin.CONVERSATION),
            ]

        def messages(self):
            return list(self._messages)

        def replace(self, messages):
            self.replace_called = True
            self._messages = list(messages)

        def clear(self):
            self._messages.clear()

    memory = Memory()
    context_manager = ContextManager(
        llm=object(),
        memory=memory,
        config=CompactConfig(
            context_window_tokens=20_000,
            tool_result_threshold_tokens=3_000,
            tool_batch_threshold_tokens=4_000,
        ),
        store=object(),
    )
    observed_history = []

    def build_request(history):
        observed_history.append(tuple(history))
        return SimpleNamespace(messages=tuple(history), tools=())

    status = context_manager.estimate_current(build_request=build_request)

    assert isinstance(status, ContextTokenStatus)
    assert observed_history == [tuple(memory.messages())]
    assert memory.replace_called is False
    assert status.context_window_tokens == 20_000
    assert status.estimated_tokens > 0
    assert status.usage_ratio == status.estimated_tokens / 20_000


def test_session_archive_store_current_summary_uses_current_session_only(tmp_path: Path):
    workspace = tmp_path / "workspace"
    home = tmp_path / "home"
    workspace.mkdir()
    home.mkdir()

    paths = MemoryPaths(workspace_root=workspace, home=home)
    now = datetime(2026, 7, 24, 8, 0, tzinfo=timezone.utc)
    store = SessionArchiveStore(paths=paths, now=lambda: now)
    store.append_message(
        ChatMessage(role="user", content="hello", origin=MessageOrigin.CONVERSATION)
    )

    other_session = paths.sessions_dir / "20260101-010101-deadbeef.jsonl"
    other_session.write_text(
        '{"content":"ignored","origin":"conversation","role":"user","timestamp":"2020-01-01T00:00:00+00:00","type":"message"}\n',
        encoding="utf-8",
    )

    summary = store.current_summary()

    assert summary.session_id == store.current_session_id
    assert summary.path == str(store._current_session_path)
    assert summary.message_count == 1
    assert summary.updated_at == now.isoformat()
    assert summary.recoverable is True
    assert summary.title == "hello"


def test_project_memory_manager_reports_session_and_memory_status(tmp_path: Path):
    workspace = tmp_path / "workspace"
    home = tmp_path / "home"
    workspace.mkdir()
    home.mkdir()
    paths = MemoryPaths(workspace_root=workspace, home=home)

    class Memory(ConversationMemory):
        def __init__(self) -> None:
            self._messages = [
                ChatMessage(role="user", content="hello", origin=MessageOrigin.CONVERSATION),
                ChatMessage(role="assistant", content="world", origin=MessageOrigin.CONVERSATION),
            ]

        def append(self, message: ChatMessage) -> None:
            self._messages.append(message)

        def messages(self):
            return list(self._messages)

        def replace(self, messages):
            self._messages = list(messages)

        def clear(self):
            self._messages.clear()

    class Sessions:
        current_session_id = "session-123"

        def current_summary(self):
            return memory_models.SessionSummary(
                session_id=self.current_session_id,
                path=str(paths.sessions_dir / f"{self.current_session_id}.jsonl"),
                title="hello",
                message_count=2,
                updated_at="2026-07-24T08:00:00+00:00",
                recoverable=True,
            )

    class Notes:
        def load_index_bundle(self, scope):
            if scope is memory_models.MemoryScope.USER:
                return memory_models.MemoryIndexBundle(
                    scope=scope,
                    entries=("- user",),
                    rendered_text="- user",
                    line_count=1,
                    byte_count=6,
                    truncated=False,
                    diagnostics=(
                        memory_models.MemoryDiagnostic(
                            code="user_index_warning",
                            message="user diagnostic",
                            scope=scope,
                        ),
                    ),
                )
            return memory_models.MemoryIndexBundle(
                scope=scope,
                entries=("- project", "- more"),
                rendered_text="- project\n- more",
                line_count=2,
                byte_count=17,
                truncated=False,
                diagnostics=(
                    memory_models.MemoryDiagnostic(
                        code="project_index_warning",
                        message="project diagnostic",
                        scope=scope,
                    ),
                ),
            )

        def load_notes(self, scope):
            if scope is memory_models.MemoryScope.USER:
                return (
                    memory_models.MemoryNote(
                        note_id="u1",
                        scope=scope,
                        kind=memory_models.MemoryKind.USER_PREFERENCE,
                        path=str(paths.user_memory_dir / "u1.md"),
                        frontmatter={"title": "user note"},
                        body="body",
                        updated_at="2026-07-24T00:00:00+00:00",
                    ),
                )
            return (
                memory_models.MemoryNote(
                    note_id="p1",
                    scope=scope,
                    kind=memory_models.MemoryKind.PROJECT_KNOWLEDGE,
                    path=str(paths.project_memory_dir / "p1.md"),
                    frontmatter={"title": "project note"},
                    body="body",
                    updated_at="2026-07-24T00:00:00+00:00",
                ),
                memory_models.MemoryNote(
                    note_id="p2",
                    scope=scope,
                    kind=memory_models.MemoryKind.PROJECT_KNOWLEDGE,
                    path=str(paths.project_memory_dir / "p2.md"),
                    frontmatter={"title": "project note 2"},
                    body="body",
                    updated_at="2026-07-24T00:00:00+00:00",
                ),
            )

    manager = ProjectMemoryManager(
        paths=paths,
        instructions=object(),
        sessions=Sessions(),
        notes=Notes(),
        note_prompt=object(),
        llm=object(),
        memory=Memory(),
    )

    session_status = manager.session_status()
    memory_status = manager.memory_status()

    assert session_status.session_id == "session-123"
    assert session_status.message_count == 2
    assert session_status.source is memory_models.SessionSource.NEW
    assert session_status.restored_from_session_id is None
    assert session_status.updated_at == "2026-07-24T08:00:00+00:00"

    assert memory_status.user.path == str(paths.user_memory_dir)
    assert memory_status.user.note_count == 1
    assert memory_status.user.index_line_count == 1
    assert memory_status.project.path == str(paths.project_memory_dir)
    assert memory_status.project.note_count == 2
    assert memory_status.project.index_byte_count == 17
    assert memory_status.diagnostic_codes == ("user_index_warning", "project_index_warning")


def test_agent_loop_context_token_status_uses_current_memory_without_advancing_turn():
    class Memory(ConversationMemory):
        def __init__(self) -> None:
            self._messages = [
                ChatMessage(role="user", content="hello", origin=MessageOrigin.CONVERSATION),
            ]
            self.replace_called = False

        def append(self, message: ChatMessage) -> None:
            self._messages.append(message)

        def messages(self):
            return list(self._messages)

        def replace(self, messages):
            self.replace_called = True
            self._messages = list(messages)

        def clear(self):
            self._messages.clear()

    class PromptBuilder:
        def __init__(self) -> None:
            self.begin_turn_calls = []
            self.build_calls = []

        def begin_turn(self, *, turn_id, plan_only, reminders=(), framework_blocks=()):
            self.begin_turn_calls.append((turn_id, plan_only, tuple(reminders), tuple(framework_blocks)))
            return TurnPromptContext(
                turn_id=turn_id,
                environment=EnvironmentSnapshot(
                    workspace=None,
                    operating_system="windows",
                    current_time="2026-07-24T08:00:00Z",
                    timezone="UTC",
                    git_branch=None,
                    git_status=None,
                    diagnostics=(),
                ),
                plan_only=plan_only,
                reminders=tuple(reminders),
                framework_blocks=tuple(framework_blocks),
            )

        def build(self, *, history, tools, turn, round_index):
            self.build_calls.append((tuple(history), tuple(tools), turn, round_index))
            return SimpleNamespace(messages=tuple(history), tools=tuple(tools))

    class ToolRegistry:
        def model_definitions(self):
                return (
                    ToolDefinition(
                        name="search",
                        description="search",
                        parameters={"type": "object"},
                        kind=ToolKind.READ,
                    ),
                )

        def deferred_summaries(self):
            return (SimpleNamespace(name="search", description="search tools"),)

    class ProjectMemory:
        def __init__(self) -> None:
            self.before_user_request_called = False

        async def before_user_request(self, *, compact_prepare):
            self.before_user_request_called = True
            return memory_models.FrameworkContext(blocks=(), restored_history=())

    memory = Memory()
    prompt_builder = PromptBuilder()
    context_manager = ContextManager(
        llm=object(),
        memory=memory,
        config=CompactConfig(
            context_window_tokens=20_000,
            tool_result_threshold_tokens=3_000,
            tool_batch_threshold_tokens=4_000,
        ),
        store=object(),
    )
    project_memory = ProjectMemory()
    loop = AgentLoop(
        llm=object(),
        memory=memory,
        tool_executor=object(),
        tool_registry=ToolRegistry(),
        permission=object(),
        context_manager=context_manager,
        prompt_builder=prompt_builder,
        project_memory=project_memory,
    )

    status = loop.context_token_status(mode=AgentMode(plan_only=True))

    assert isinstance(status, ContextTokenStatus)
    assert loop._next_turn_id == 0
    assert memory.replace_called is False
    assert project_memory.before_user_request_called is False
    assert prompt_builder.begin_turn_calls == [(1, True, (), ())]
    assert len(prompt_builder.build_calls) == 1
    history, tools, turn, round_index = prompt_builder.build_calls[0]
    assert history == tuple(memory.messages())
    assert tools[0].name == "search"
    assert turn.plan_only is True
    assert turn.reminders[0].id == "mcp-deferred-tools"
    assert round_index == 1


def test_agent_loop_session_and_memory_status_forward_to_project_memory():
    session_snapshot = memory_models.SessionStatusSnapshot(
        session_id="session-1",
        message_count=4,
        source=memory_models.SessionSource.RESTORED,
        restored_from_session_id="session-0",
        updated_at="2026-07-24T08:00:00+00:00",
    )
    memory_snapshot = memory_models.MemoryStatusSnapshot(
        user=memory_models.MemoryScopeStatus(
            scope=memory_models.MemoryScope.USER,
            path="user",
            note_count=1,
            index_line_count=1,
            index_byte_count=3,
            diagnostic_codes=("user_index_warning",),
        ),
        project=memory_models.MemoryScopeStatus(
            scope=memory_models.MemoryScope.PROJECT,
            path="project",
            note_count=2,
            index_line_count=2,
            index_byte_count=5,
            diagnostic_codes=("project_index_warning",),
        ),
        diagnostic_codes=("user_index_warning", "project_index_warning"),
    )

    class ProjectMemory:
        def __init__(self) -> None:
            self.session_called = False
            self.memory_called = False

        def session_status(self):
            self.session_called = True
            return session_snapshot

        def memory_status(self):
            self.memory_called = True
            return memory_snapshot

    project_memory = ProjectMemory()
    loop = AgentLoop(
        llm=object(),
        memory=object(),
        tool_executor=object(),
        tool_registry=object(),
        permission=object(),
        context_manager=object(),
        project_memory=project_memory,
    )

    assert loop.session_status() is session_snapshot
    assert loop.memory_status() is memory_snapshot
    assert project_memory.session_called is True
    assert project_memory.memory_called is True


def test_agent_loop_session_and_memory_status_raise_when_project_memory_missing():
    loop = AgentLoop(
        llm=object(),
        memory=object(),
        tool_executor=object(),
        tool_registry=object(),
        permission=object(),
        context_manager=object(),
        project_memory=None,
    )

    with pytest.raises(RuntimeError, match="project memory unavailable"):
        loop.session_status()
    with pytest.raises(RuntimeError, match="project memory unavailable"):
        loop.memory_status()


def test_chat_session_forwards_status_methods_to_agent_loop():
    token_status = ContextTokenStatus(
        estimated_tokens=123,
        context_window_tokens=456,
        usage_ratio=0.27,
        source="full_chars",
    )
    session_status = memory_models.SessionStatusSnapshot(
        session_id="session-1",
        message_count=2,
        source=memory_models.SessionSource.NEW,
        restored_from_session_id=None,
        updated_at=None,
    )
    memory_status = memory_models.MemoryStatusSnapshot(
        user=memory_models.MemoryScopeStatus(
            scope=memory_models.MemoryScope.USER,
            path="user",
            note_count=1,
            index_line_count=1,
            index_byte_count=3,
            diagnostic_codes=(),
        ),
        project=memory_models.MemoryScopeStatus(
            scope=memory_models.MemoryScope.PROJECT,
            path="project",
            note_count=2,
            index_line_count=2,
            index_byte_count=5,
            diagnostic_codes=(),
        ),
        diagnostic_codes=(),
    )

    class Agent:
        def __init__(self) -> None:
            self.mode = AgentMode(plan_only=False)

        def context_token_status(self, *, mode):
            self.token_mode = mode
            return token_status

        def session_status(self):
            self.session_called = True
            return session_status

        def memory_status(self):
            self.memory_called = True
            return memory_status

        def clear_memory(self):
            self.cleared = True

    class Permissions:
        def effective_mode(self):
            return (memory_models.PermissionStatusSnapshot if False else None)

        def clear_session(self):
            self.cleared = True

        def set_session_mode(self, mode):
            self.mode = mode

    agent = Agent()
    permissions = Permissions()
    session = ChatSession(agent=agent, permissions=permissions)
    session.set_plan_only(True)

    assert asyncio.run(session.token_status()) is token_status
    assert asyncio.run(session.session_status()) is session_status
    assert asyncio.run(session.memory_status()) is memory_status
    assert agent.token_mode.plan_only is True
