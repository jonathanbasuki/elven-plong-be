"""Real-Postgres checks of the task detail (GET /api/v1/tasks/{taskId}): the
crowd-log range over zone_logs, the unflushed bucket, and zone vs delegated staff."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from conftest import pg_cleanup, pg_zone

from plong_elven.db.models import (
    Task,
    TaskRespondent,
    TaskRespondentStatusId,
    TaskStatusId,
    User,
    ZoneLog,
)
from plong_elven.schemas.tasks import LiveCrowdOut
from plong_elven.services.task_service import TaskService
from plong_elven.state.live_state import Bucket, LiveState


def at(hh: int, mm: int) -> datetime:
    return datetime(2026, 11, 6, hh, mm, tzinfo=UTC)


async def _finished_task(factory, zone_id, log_minutes, tag):
    """Zone `zone_id`; task broadcast 17:32, finished 18:12, one completed
    respondent Bunga (home elsewhere); floor staff Dedi (home here, active) and
    Eko (home here, inactive); zone_logs rows at the given (hh, mm)."""
    async with factory() as s:
        s.add(pg_zone(zone_id))
        await s.flush()
        users = {
            name: User(
                role_id=1,
                email=f"{tag}-{name}@detail.test",
                password_hash="x",
                full_name=name,
                is_active=active,
                zone_id=home,
            )
            for name, home, active in [
                ("Bunga", None, True),
                ("Dedi", zone_id, True),
                ("Eko", zone_id, False),
            ]
        }
        s.add_all(users.values())
        task = Task(
            zone_id=zone_id,
            total_staff=2,
            instruction="Guide people to exit B",
            task_status_id=TaskStatusId.FINISHED,
            created_at=at(17, 32),
            expires_at=at(17, 32) + timedelta(seconds=20),
            finished_at=at(18, 12),
        )
        s.add(task)
        await s.flush()
        s.add(
            TaskRespondent(
                task_id=task.task_id,
                staff_id=users["Bunga"].user_id,
                task_respondent_status_id=TaskRespondentStatusId.COMPLETED,
                responded_at=at(17, 32),
                completed_at=at(18, 0),
            )
        )
        s.add_all(
            ZoneLog(zone_id=zone_id, bucket_start=at(h, m), avg_count=5.0, peak_count=7)
            for h, m in log_minutes
        )
        await s.commit()
        return task.task_id, [u.user_id for u in users.values()]


def _hhmm(log) -> list[str]:
    return [b.bucketStart.strftime("%H:%M") for b in log]


@pytest.mark.asyncio
async def test_detail_completed_task_returns_bucket_range(factory) -> None:
    zone_id, tag = f"dt-{uuid4().hex[:8]}", uuid4().hex[:8]
    rows = [(17, 25), (17, 30), (17, 50), (18, 10), (18, 15)]
    task_id, user_ids = await _finished_task(factory, zone_id, rows, tag)
    try:
        async with factory() as s:
            out = await TaskService(s, None).detail(task_id, LiveState())
        assert out.phase == "completed" and out.status == "finished"
        assert out.liveCrowd is None
        assert _hhmm(out.crowdLog) == ["17:30", "17:50", "18:10"]
        assert [p.name for p in out.zoneStaff] == ["Dedi"]
        assert [p.name for p in out.delegatedStaff] == ["Bunga"]
        assert out.instruction == "Guide people to exit B"
        assert out.createdAt == at(17, 32) and out.finishedAt == at(18, 12)
        assert out.zoneName == "Race" and out.zoneStatus == "safe"
    finally:
        await pg_cleanup(factory, zone_id, user_ids)


@pytest.mark.asyncio
async def test_detail_includes_unflushed_bucket_once(factory) -> None:
    zone_id, tag = f"dt-{uuid4().hex[:8]}", uuid4().hex[:8]
    task_id, user_ids = await _finished_task(factory, zone_id, [(17, 30)], tag)
    live = LiveState()
    live.buckets[zone_id] = Bucket(start=at(18, 10), sum_count=18, n=2, peak=11)
    try:
        async with factory() as s:
            log = (await TaskService(s, None).detail(task_id, live)).crowdLog
        assert _hhmm(log) == ["17:30", "18:10"]
        assert (log[-1].avgCount, log[-1].peakCount) == (9.0, 11)

        # Once the 18:10 bucket is also in the DB, it appears once (the DB row).
        async with factory() as s:
            s.add(
                ZoneLog(
                    zone_id=zone_id,
                    bucket_start=at(18, 10),
                    avg_count=4.0,
                    peak_count=6,
                )
            )
            await s.commit()
        async with factory() as s:
            log = (await TaskService(s, None).detail(task_id, live)).crowdLog
        assert _hhmm(log) == ["17:30", "18:10"] and log[-1].peakCount == 6
    finally:
        await pg_cleanup(factory, zone_id, user_ids)


@pytest.mark.asyncio
async def test_detail_completed_without_logs_is_empty_list(factory) -> None:
    zone_id, tag = f"dt-{uuid4().hex[:8]}", uuid4().hex[:8]
    task_id, user_ids = await _finished_task(factory, zone_id, [], tag)
    try:
        async with factory() as s:
            out = await TaskService(s, None).detail(task_id, LiveState())
        assert out.crowdLog == []
    finally:
        await pg_cleanup(factory, zone_id, user_ids)


async def _open_task(factory, zone_id):
    async with factory() as s:
        s.add(pg_zone(zone_id))
        await s.flush()
        task = Task(
            zone_id=zone_id,
            total_staff=1,
            instruction="go",
            task_status_id=TaskStatusId.OPEN,
            expires_at=datetime.now(UTC) + timedelta(seconds=60),
        )
        s.add(task)
        await s.commit()
        return task.task_id


@pytest.mark.asyncio
async def test_detail_open_task_shows_live_crowd(factory) -> None:
    zone_id = f"dt-{uuid4().hex[:8]}"
    task_id = await _open_task(factory, zone_id)
    live, now = LiveState(), datetime.now(UTC)
    live.counts[zone_id], live.observed_at[zone_id] = 12, now
    live.buckets[zone_id] = Bucket(start=now, sum_count=12, n=1, peak=12)
    try:
        async with factory() as s:
            out = await TaskService(s, None).detail(task_id, live)
        assert out.phase == "ongoing"
        assert out.liveCrowd == LiveCrowdOut(count=12, observedAt=now)
        assert out.zoneStatus == "crowded"  # threshold 10
        assert out.crowdLog == []
    finally:
        await pg_cleanup(factory, zone_id)


@pytest.mark.asyncio
async def test_detail_open_task_before_any_detection(factory) -> None:
    zone_id = f"dt-{uuid4().hex[:8]}"
    task_id = await _open_task(factory, zone_id)
    try:
        async with factory() as s:
            out = await TaskService(s, None).detail(task_id, LiveState())
        assert out.liveCrowd == LiveCrowdOut(count=0, observedAt=None)
        assert out.zoneStatus == "safe"
    finally:
        await pg_cleanup(factory, zone_id)
