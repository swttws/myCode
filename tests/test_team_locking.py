import asyncio
import logging
import json
import os
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from mycode.team import TeamError
from mycode.team.infrastructure.config import TeamConfig
from mycode.team.infrastructure import locking
from mycode.team.infrastructure.locking import FileLease


def fast_lock_config(**overrides) -> TeamConfig:
    values = {
        "lock_retry_interval_seconds": 0.01,
        "lock_timeout_seconds": 0.04,
        "lock_stale_after_seconds": 1.0,
    }
    values.update(overrides)
    return TeamConfig(**values)


def read_lock(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_lock(path: Path, *, owner: str, token: str, process_id: int, created_at: datetime) -> None:
    path.write_text(
        json.dumps(
            {
                "owner": owner,
                "token": token,
                "created_at": created_at.isoformat(),
                "process_id": process_id,
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )


def test_file_lease_acquire_writes_lock_content_and_release_removes_file(tmp_path: Path):
    lock_path = tmp_path / "lead.lock"

    lease = asyncio.run(FileLease.acquire(lock_path, config=fast_lock_config(), owner="lead"))

    data = read_lock(lock_path)
    assert data["owner"] == "lead"
    assert data["token"] == lease.token
    assert data["process_id"] == os.getpid()
    assert isinstance(data["created_at"], str)

    asyncio.run(lease.release())

    assert not lock_path.exists()


def test_file_lease_times_out_when_current_owner_is_active(tmp_path: Path):
    lock_path = tmp_path / "lead.lock"
    lease = asyncio.run(FileLease.acquire(lock_path, config=fast_lock_config(), owner="lead"))

    with pytest.raises(TeamError, match="lock"):
        asyncio.run(FileLease.acquire(lock_path, config=fast_lock_config(), owner="other"))

    assert read_lock(lock_path)["owner"] == "lead"
    asyncio.run(lease.release())


def test_file_lease_concurrent_claims_only_one_owner(tmp_path: Path):
    lock_path = tmp_path / "lead.lock"

    async def attempt(owner: str):
        try:
            return await FileLease.acquire(lock_path, config=fast_lock_config(), owner=owner)
        except TeamError:
            return None

    async def scenario():
        return await asyncio.gather(attempt("one"), attempt("two"))

    first, second = asyncio.run(scenario())
    leases = [lease for lease in (first, second) if lease is not None]

    assert len(leases) == 1
    assert read_lock(lock_path)["owner"] in {"one", "two"}
    asyncio.run(leases[0].release())


def test_file_lease_reclaims_stale_lock_only_when_owner_is_not_alive(tmp_path: Path):
    lock_path = tmp_path / "lead.lock"
    stale_time = datetime.now(timezone.utc) - timedelta(seconds=120)
    write_lock(
        lock_path,
        owner="old",
        token="old-token",
        process_id=99999999,
        created_at=stale_time,
    )

    lease = asyncio.run(FileLease.acquire(lock_path, config=fast_lock_config(), owner="new"))

    data = read_lock(lock_path)
    assert data["owner"] == "new"
    assert data["token"] == lease.token
    asyncio.run(lease.release())


def test_file_lease_reclaims_dead_owner_lock_without_waiting_for_staleness(tmp_path: Path):
    lock_path = tmp_path / "lead.lock"
    write_lock(
        lock_path,
        owner="old",
        token="old-token",
        process_id=99999999,
        created_at=datetime.now(timezone.utc),
    )

    lease = asyncio.run(FileLease.acquire(lock_path, config=fast_lock_config(), owner="new"))

    data = read_lock(lock_path)
    assert data["owner"] == "new"
    assert data["token"] == lease.token
    asyncio.run(lease.release())


def test_file_lease_logs_recovery_and_release(tmp_path: Path, caplog):
    lock_path = tmp_path / "lead.lock"
    write_lock(
        lock_path,
        owner="old",
        token="old-token",
        process_id=99999999,
        created_at=datetime.now(timezone.utc),
    )

    with caplog.at_level(logging.INFO, logger="mycode.team.locking"):
        lease = asyncio.run(FileLease.acquire(lock_path, config=fast_lock_config(), owner="new"))
        asyncio.run(lease.release())

    messages = [record.message for record in caplog.records if record.name == "mycode.team.locking"]
    assert "team.lock.acquire.started" in messages
    assert "team.lock.reclaimed.dead_owner" in messages
    assert "team.lock.acquired" in messages
    assert "team.lock.released" in messages


def test_file_lease_keeps_stale_lock_when_owner_process_is_alive(tmp_path: Path):
    lock_path = tmp_path / "lead.lock"
    stale_time = datetime.now(timezone.utc) - timedelta(seconds=120)
    write_lock(
        lock_path,
        owner="old",
        token="old-token",
        process_id=os.getpid(),
        created_at=stale_time,
    )

    with pytest.raises(TeamError, match="lock"):
        asyncio.run(FileLease.acquire(lock_path, config=fast_lock_config(), owner="new"))

    assert read_lock(lock_path)["token"] == "old-token"


def test_file_lease_release_refuses_non_matching_token(tmp_path: Path):
    lock_path = tmp_path / "lead.lock"
    lease = asyncio.run(FileLease.acquire(lock_path, config=fast_lock_config(), owner="lead"))
    write_lock(
        lock_path,
        owner="other",
        token="other-token",
        process_id=os.getpid(),
        created_at=datetime.now(timezone.utc),
    )

    with pytest.raises(TeamError, match="owned"):
        asyncio.run(lease.release())

    assert read_lock(lock_path)["token"] == "other-token"


def test_file_lease_acquire_cancellation_cleans_up_lock_written_by_worker(
    tmp_path: Path, monkeypatch
):
    lock_path = tmp_path / "lead.lock"
    started = threading.Event()
    finish = threading.Event()
    completed = threading.Event()
    original_write_lock = locking._write_lock

    def delayed_write_lock(lease: FileLease) -> None:
        started.set()
        assert finish.wait(1)
        original_write_lock(lease)
        completed.set()

    monkeypatch.setattr(locking, "_write_lock", delayed_write_lock)

    async def scenario() -> None:
        task = asyncio.create_task(
            FileLease.acquire(lock_path, config=fast_lock_config(), owner="lead")
        )
        assert await asyncio.to_thread(started.wait, 1)
        task.cancel()
        finish.set()
        assert await asyncio.to_thread(completed.wait, 1)
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(scenario())

    assert not lock_path.exists()
