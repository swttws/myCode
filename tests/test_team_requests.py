from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from mycode.team.domain.models import TeamRecord, TeamState
from mycode.team.infrastructure.requests import (
    TeamRequest,
    TeamRequestKind,
    TeamRequestState,
    TeamRequestStore,
)
from mycode.team.infrastructure.storage import TeamStore


def make_store(tmp_path: Path) -> TeamRequestStore:
    store = TeamStore(home=tmp_path / "home")
    store.create(
        TeamRecord(
            team_name="team-a",
            repository_root=tmp_path,
            repository_id="repo-123",
            target_branch="main",
            state=TeamState.ACTIVE,
        )
    )
    return TeamRequestStore(store)


def make_request() -> TeamRequest:
    now = datetime(2026, 1, 2, tzinfo=timezone.utc)
    return TeamRequest(
        request_id="request-1",
        team_name="team-a",
        batch_id="batch-1",
        task_id="task-1",
        member_name="dev",
        kind=TeamRequestKind.CLARIFICATION,
        question="Which API should be used?",
        options=("v1", "v2"),
        context_summary="The task mentions both APIs.",
        state=TeamRequestState.PENDING,
        created_at=now,
    )


def test_request_store_round_trips_pending_request(tmp_path: Path) -> None:
    requests = make_store(tmp_path)
    request = make_request()

    assert requests.create(request) == request
    assert requests.get("team-a", "request-1") == request
    assert requests.list("team-a") == (request,)
    assert requests.list("team-a", state=TeamRequestState.PENDING) == (request,)


def test_request_store_resolves_once_and_is_idempotent(tmp_path: Path) -> None:
    requests = make_store(tmp_path)
    request = make_request()
    requests.create(request)

    resolved = requests.resolve(
        "team-a",
        "request-1",
        state=TeamRequestState.RESOLVED,
        resolution="Use v2.",
        resolved_by="lead",
    )
    repeated = requests.resolve(
        "team-a",
        "request-1",
        state=TeamRequestState.RESOLVED,
        resolution="Use v2.",
        resolved_by="lead",
    )

    assert resolved.state is TeamRequestState.RESOLVED
    assert resolved.resolution == "Use v2."
    assert repeated == resolved


def test_request_store_rejects_conflicting_resolution(tmp_path: Path) -> None:
    requests = make_store(tmp_path)
    requests.create(make_request())
    requests.resolve(
        "team-a",
        "request-1",
        state=TeamRequestState.REJECTED,
        resolution="Not enough information.",
        resolved_by="user",
    )

    with pytest.raises(ValueError, match="already resolved"):
        requests.resolve(
            "team-a",
            "request-1",
            state=TeamRequestState.RESOLVED,
            resolution="Use v2.",
            resolved_by="lead",
        )
