"""Real-Postgres checks of the task footage job: which recording segments it
uses, which it deletes, and that it leaves no temp files. ffmpeg and the
Supabase upload are replaced by fakes."""

import os
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from conftest import pg_cleanup, pg_zone

from plong_elven.db.models import Task, TaskFootage, TaskStatusId
from plong_elven.services.footage_service import process_task_footage


def at(hh: int, mm: int) -> datetime:
    return datetime(2026, 11, 7, hh, mm, tzinfo=UTC)


def _segment(root: Path, hh: int, mm: int) -> Path:
    p = root / f"{hh:02d}-{mm:02d}.ts"
    p.write_bytes(b"ts")
    os.utime(p, (at(hh, mm).timestamp(), at(hh, mm).timestamp()))
    return p


@pytest.fixture
def recordings(tmp_path, monkeypatch) -> Path:
    monkeypatch.setenv("MEDIAMTX_RECORDINGS_DIR", str(tmp_path))
    root = tmp_path / "cam1"  # MEDIAMTX_RECORD_PATH default
    root.mkdir()
    return root


async def _tasks(factory, zone_id, done_status=TaskStatusId.COMPLETE):
    """Task A: ongoing since 10:07. Task B: 10:05-10:10, footage processing."""
    async with factory() as s:
        s.add(pg_zone(zone_id))
        await s.flush()
        a = Task(
            zone_id=zone_id,
            total_staff=1,
            instruction="a",
            task_status_id=TaskStatusId.ONGOING,
            created_at=at(10, 7),
            expires_at=at(10, 7),
        )
        b = Task(
            zone_id=zone_id,
            total_staff=1,
            instruction="b",
            task_status_id=done_status,
            created_at=at(10, 5),
            expires_at=at(10, 5),
            finished_at=at(10, 10),
        )
        s.add_all([a, b])
        await s.flush()
        s.add(TaskFootage(task_id=b.task_id))
        await s.commit()
        return b.task_id


@pytest.mark.asyncio
async def test_footage_keeps_segments_of_an_ongoing_task(factory, recordings) -> None:
    zone_id = f"ft-{uuid4().hex[:8]}"
    only_b = _segment(recordings, 10, 6)  # inside B only -> deleted
    shared = _segment(recordings, 10, 8)  # inside B and ongoing A -> kept
    later = _segment(recordings, 10, 30)  # outside B -> untouched
    task_id = await _tasks(factory, zone_id)
    encoded, uploaded = [], []

    async def encode(segments, output: Path) -> None:
        encoded.append(sorted(p.name for p in segments))
        output.write_bytes(b"mp4")

    async def upload(path: str, data: bytes) -> None:
        uploaded.append((path, data))

    try:
        await process_task_footage(task_id, factory, encode=encode, upload=upload)

        assert encoded == [["10-06.ts", "10-08.ts"]]
        assert uploaded == [(f"tasks/{task_id}.mp4", b"mp4")]
        assert not only_b.exists()
        assert shared.exists() and later.exists()
        async with factory() as s:
            footage = await s.get(TaskFootage, task_id)
            assert footage.status == "ready"
            assert footage.public_url.endswith(f"/tasks/{task_id}.mp4")
        leftovers = list(Path("/tmp").glob(f"{task_id}.*"))
        assert leftovers == []
    finally:
        await pg_cleanup(factory, zone_id)


@pytest.mark.asyncio
async def test_footage_without_segments_fails_and_cleans_up(
    factory, recordings
) -> None:
    zone_id = f"ft-{uuid4().hex[:8]}"
    task_id = await _tasks(factory, zone_id)

    async def must_not_run(*_args) -> None:
        raise AssertionError("encode/upload must not run without segments")

    try:
        await process_task_footage(
            task_id, factory, encode=must_not_run, upload=must_not_run
        )
        async with factory() as s:
            footage = await s.get(TaskFootage, task_id)
            assert footage.status == "failed"
            assert "no MediaMTX segments" in footage.error
        assert list(Path("/tmp").glob(f"{task_id}.*")) == []
    finally:
        await pg_cleanup(factory, zone_id)


@pytest.mark.asyncio
async def test_startup_fails_footage_cut_off_by_a_restart(factory) -> None:
    from plong_elven.services.footage_service import fail_interrupted_footage

    zone_id = f"ft-{uuid4().hex[:8]}"
    task_id = await _tasks(factory, zone_id)  # footage row is "processing"
    try:
        assert await fail_interrupted_footage(factory) >= 1
        async with factory() as s:
            footage = await s.get(TaskFootage, task_id)
            assert footage.status == "failed"
            assert "restart" in footage.error
    finally:
        await pg_cleanup(factory, zone_id)
