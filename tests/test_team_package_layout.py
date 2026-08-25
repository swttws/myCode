"""Structural checks for the canonical Agent Team package layout."""

from __future__ import annotations

from dataclasses import fields
from pathlib import Path


TEAM_SRC = Path(__file__).resolve().parent.parent / "src" / "mycode" / "team"


def test_public_team_facade_uses_canonical_contracts() -> None:
    from mycode.team import TeamError, TeamRequest, TeamTask
    from mycode.team.domain.models import TeamError as DomainTeamError, TeamTask as DomainTeamTask
    from mycode.team.infrastructure.requests import TeamRequest as InfrastructureTeamRequest

    assert TeamError is DomainTeamError
    assert TeamTask is DomainTeamTask
    assert TeamRequest is InfrastructureTeamRequest


def test_canonical_layer_exports_are_importable() -> None:
    from mycode.team.application.integration import IntegrationService
    from mycode.team.application.service import TeamService
    from mycode.team.application.tasks import TaskBoard
    from mycode.team.execution.backends import BackendRouter
    from mycode.team.execution.consumer import RoleEventConsumer
    from mycode.team.execution.notifier import TeamEventNotifier
    from mycode.team.execution.runtime import TeamMemberRuntime
    from mycode.team.execution.supervisor import LeadSupervisor
    from mycode.team.infrastructure.events import TeamEventStore
    from mycode.team.infrastructure.storage import TeamStore
    from mycode.team.tooling.lead_tools import register_lead_team_tools
    from mycode.team.tooling.member_tools import register_member_team_tools
    from mycode.team.tooling.tool import TeamTool

    assert all(
        callable(item)
        for item in (
            IntegrationService,
            TeamService,
            TaskBoard,
            BackendRouter,
            RoleEventConsumer,
            TeamEventNotifier,
            TeamMemberRuntime,
            LeadSupervisor,
            TeamEventStore,
            TeamStore,
            register_lead_team_tools,
            register_member_team_tools,
            TeamTool,
        )
    )


def test_root_implementation_and_legacy_mailbox_modules_are_removed() -> None:
    removed_modules = {
        "backends.py",
        "config.py",
        "consumer.py",
        "context.py",
        "events.py",
        "integration.py",
        "lead_tools.py",
        "locking.py",
        "mailbox.py",
        "member_tools.py",
        "models.py",
        "notifier.py",
        "policy.py",
        "requests.py",
        "runtime.py",
        "service.py",
        "state.py",
        "storage.py",
        "supervisor.py",
        "tasks.py",
        "tool.py",
        "tool_helpers.py",
        "tool_names.py",
        "worker.py",
    }
    assert all(not (TEAM_SRC / filename).exists() for filename in removed_modules)
    assert not (TEAM_SRC / "tools").exists()


def test_canonical_layers_do_not_import_removed_root_modules() -> None:
    root_modules = {
        "backends",
        "config",
        "context",
        "consumer",
        "events",
        "integration",
        "lead_tools",
        "locking",
        "mailbox",
        "member_tools",
        "models",
        "notifier",
        "policy",
        "requests",
        "runtime",
        "service",
        "state",
        "storage",
        "supervisor",
        "tasks",
        "tool",
        "tool_helpers",
        "tool_names",
        "worker",
    }
    violations: list[str] = []
    for package in ("application", "domain", "execution", "infrastructure", "tooling"):
        for path in (TEAM_SRC / package).rglob("*.py"):
            for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if line.startswith("from mycode.team.") or line.startswith("import mycode.team."):
                    imported = line.split("mycode.team.", 1)[1].split()[0].split(".", 1)[0]
                    if imported in root_modules:
                        violations.append(f"{path.relative_to(TEAM_SRC)}:{line_number}: {line}")

    assert not violations, "Canonical layers import removed root modules:\n" + "\n".join(violations)


def test_team_has_no_empty_python_files() -> None:
    empty = [path.relative_to(TEAM_SRC) for path in TEAM_SRC.rglob("*.py") if not path.read_text(encoding="utf-8").strip()]
    assert not empty, f"Empty Team Python files remain: {empty}"


def test_message_routing_has_an_application_boundary() -> None:
    from mycode.team.application.messaging import event_recipients, validate_message_sender

    assert callable(event_recipients)
    assert callable(validate_message_sender)


def test_event_driven_launch_contract_has_no_legacy_mailbox_metadata() -> None:
    from mycode.team.domain.models import MemberLaunchSpec, MemberRecord
    from mycode.team.infrastructure.config import TeamConfig
    from mycode.team.infrastructure.storage import TeamStore

    assert not any(field.name.startswith("mailbox_") for field in fields(TeamConfig))
