from __future__ import annotations

import asyncio
import importlib.util
from dataclasses import FrozenInstanceError, MISSING, fields, is_dataclass
from pathlib import Path
import sys
from typing import get_origin

import pytest

from mycode.permission.models import PermissionMode, RuleSource


def _load_mcp_models():
    module_path = Path(__file__).resolve().parents[1] / "src" / "mycode" / "mcp" / "models.py"
    spec = importlib.util.spec_from_file_location("_stage09_mcp_models", module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


MCPServerState = _load_mcp_models().MCPServerState


def import_slash():
    import mycode.slash as slash

    return slash


def assert_frozen_dataclass(model: type) -> None:
    assert is_dataclass(model)
    assert model.__dataclass_params__.frozen is True


def assert_field_defaults(model: type, expected: list[tuple[str, object]]) -> None:
    assert [(field.name, field.default) for field in fields(model)] == expected


def _annotation_name(value: object) -> str:
    return getattr(value, "__forward_arg__", getattr(value, "__name__", str(value)))


def test_status_models_have_fixed_frozen_fields():
    slash = import_slash()

    for model in (
        slash.PermissionStatusSnapshot,
        slash.GitStatusSnapshot,
        slash.MCPServerStatus,
        slash.MCPStatusSnapshot,
        slash.StatusSection,
        slash.ApplicationStatusSnapshot,
    ):
        assert_frozen_dataclass(model)

    assert_field_defaults(
        slash.PermissionStatusSnapshot,
        [
            ("mode", MISSING),
            ("source", MISSING),
        ],
    )
    assert_field_defaults(
        slash.GitStatusSnapshot,
        [
            ("is_repository", MISSING),
            ("repository_root", MISSING),
            ("branch", MISSING),
            ("upstream", MISSING),
            ("ahead", MISSING),
            ("behind", MISSING),
            ("staged", MISSING),
            ("unstaged", MISSING),
            ("untracked", MISSING),
        ],
    )
    assert_field_defaults(
        slash.MCPServerStatus,
        [
            ("name", MISSING),
            ("state", MISSING),
            ("available", MISSING),
            ("tool_count", MISSING),
            ("diagnostic_categories", MISSING),
        ],
    )
    assert_field_defaults(
        slash.MCPStatusSnapshot,
        [
            ("servers", MISSING),
        ],
    )
    assert_field_defaults(
        slash.StatusSection,
        [
            ("value", MISSING),
            ("error", None),
        ],
    )
    assert_field_defaults(
        slash.ApplicationStatusSnapshot,
        [
            ("workspace_root", MISSING),
            ("mode", MISSING),
            ("permission", MISSING),
            ("token", MISSING),
            ("session", MISSING),
            ("memory", MISSING),
            ("git", MISSING),
            ("mcp", MISSING),
        ],
    )


def test_status_section_is_generic_and_status_models_are_immutable():
    slash = import_slash()

    permission_snapshot = slash.PermissionStatusSnapshot(
        mode=PermissionMode.DEFAULT,
        source=RuleSource.SESSION,
    )
    git_snapshot = slash.GitStatusSnapshot(
        is_repository=True,
        repository_root="D:/repo",
        branch="main",
        upstream="origin/main",
        ahead=1,
        behind=2,
        staged=3,
        unstaged=4,
        untracked=5,
    )
    server_status = slash.MCPServerStatus(
        name="files",
        state=MCPServerState.READY,
        available=True,
        tool_count=2,
        diagnostic_categories=("connection",),
    )
    mcp_snapshot = slash.MCPStatusSnapshot(servers=(server_status,))
    success = slash.StatusSection[slash.PermissionStatusSnapshot](value=permission_snapshot)
    failure = slash.StatusSection[slash.GitStatusSnapshot](value=None, error="git_unavailable")
    application = slash.ApplicationStatusSnapshot(
        workspace_root="D:/repo",
        mode=slash.SlashMode.DEFAULT,
        permission=success,
        token=slash.StatusSection(value=None, error="token_unavailable"),
        session=slash.StatusSection(value=None, error="session_unavailable"),
        memory=slash.StatusSection(value=None, error="memory_unavailable"),
        git=failure,
        mcp=slash.StatusSection(value=mcp_snapshot),
    )

    assert get_origin(slash.StatusSection) is None
    assert slash.StatusSection.__parameters__
    assert success.value is permission_snapshot
    assert success.error is None
    assert failure.value is None
    assert failure.error == "git_unavailable"
    assert application.git is failure
    assert application.mcp.value is mcp_snapshot

    for instance, attribute, value in (
        (permission_snapshot, "mode", PermissionMode.STRICT),
        (git_snapshot, "ahead", 0),
        (server_status, "tool_count", 0),
        (mcp_snapshot, "servers", ()),
        (success, "error", "changed"),
        (application, "workspace_root", "D:/other"),
    ):
        with pytest.raises(FrozenInstanceError):
            setattr(instance, attribute, value)


def test_status_model_annotations_keep_future_types_as_forward_references():
    slash = import_slash()

    application_annotations = slash.ApplicationStatusSnapshot.__annotations__
    permission_annotations = slash.PermissionStatusSnapshot.__annotations__
    mcp_annotations = slash.MCPStatusSnapshot.__annotations__

    assert _annotation_name(permission_annotations["mode"]) == "PermissionMode"
    assert "RuleSource" in str(permission_annotations["source"])
    assert "MCPServerStatus" in str(mcp_annotations["servers"])
    assert "ContextTokenStatus" in str(application_annotations["token"])
    assert "SessionStatusSnapshot" in str(application_annotations["session"])
    assert "MemoryStatusSnapshot" in str(application_annotations["memory"])


def test_slash_command_controller_is_runtime_checkable_protocol():
    slash = import_slash()

    class FakeController:
        def __init__(self) -> None:
            self.mode = slash.SlashMode.DEFAULT

        def show_message(self, text: str, *, error: bool = False) -> None:
            self.last_message = (text, error)

        async def send_user_message(self, text: str) -> None:
            self.sent_text = text

        async def execute_skill(self, name: str, arguments: str) -> None:
            self.executed_skill = (name, arguments)

        async def compact_context(self) -> None:
            self.compacted = True

        def clear_session(self) -> None:
            self.cleared = True

        def current_mode(self) -> slash.SlashMode:
            return self.mode

        def set_mode(self, mode: slash.SlashMode) -> None:
            self.mode = mode

        def permission_status(self) -> slash.PermissionStatusSnapshot:
            return slash.PermissionStatusSnapshot(
                mode=PermissionMode.DEFAULT,
                source=RuleSource.SESSION,
            )

        def set_permission_mode(self, mode: PermissionMode) -> None:
            self.permission_mode = mode

        async def token_status(self) -> "ContextTokenStatus":
            return object()  # pragma: no cover

        async def session_status(self) -> "SessionStatusSnapshot":
            return object()  # pragma: no cover

        async def memory_status(self) -> "MemoryStatusSnapshot":
            return object()  # pragma: no cover

        async def application_status(self) -> slash.ApplicationStatusSnapshot:
            permission = slash.StatusSection(
                value=slash.PermissionStatusSnapshot(
                    mode=PermissionMode.DEFAULT,
                    source=RuleSource.SESSION,
                )
            )
            return slash.ApplicationStatusSnapshot(
                workspace_root="D:/repo",
                mode=self.mode,
                permission=permission,
                token=slash.StatusSection(value=None, error="token_unavailable"),
                session=slash.StatusSection(value=None, error="session_unavailable"),
                memory=slash.StatusSection(value=None, error="memory_unavailable"),
                git=slash.StatusSection(value=None, error="git_unavailable"),
                mcp=slash.StatusSection(value=None, error="mcp_unavailable"),
            )

    controller = FakeController()

    assert isinstance(controller, slash.SlashCommandController)
    assert controller.current_mode() is slash.SlashMode.DEFAULT
    asyncio.run(controller.send_user_message("hello"))
    asyncio.run(controller.execute_skill("review", "main"))
    asyncio.run(controller.compact_context())
    status = asyncio.run(controller.application_status())
    assert status.workspace_root == "D:/repo"
    assert controller.executed_skill == ("review", "main")


def test_slash_package_exports_status_models_and_controller_protocol():
    slash = import_slash()

    expected_exports = {
        "ApplicationStatusSnapshot",
        "GitStatusSnapshot",
        "MCPServerStatus",
        "MCPStatusSnapshot",
        "ParsedSlashInput",
        "PermissionStatusSnapshot",
        "SlashCommand",
        "SlashCommandContext",
        "SlashCommandController",
        "SlashCommandHandler",
        "SlashCommandType",
        "SlashCompletionCandidate",
        "SlashDispatchKind",
        "SlashDispatchResult",
        "SlashHandlerSignal",
        "SlashInputKind",
        "SlashMode",
        "StatusSection",
    }

    assert expected_exports.issubset(set(slash.__all__))
    for name in expected_exports:
        assert hasattr(slash, name)
