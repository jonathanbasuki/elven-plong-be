"""Real-Postgres checks: the accept row lock, the broadcast zone lock, and zone
deletion (FK cascade/restrict). Skips without a DB unless REQUIRE_PG=1 (set in
CI), so a broken CI database fails instead of silently skipping."""

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from conftest import FakePushSender
from conftest import pg_cleanup as _cleanup
from conftest import pg_zone as _zone
from sqlalchemy import delete, func, select

from plong_elven.app.db.models import Task, TaskRespondent, User, Zone, ZoneLog
from plong_elven.app.schemas.layout import LayoutZone
from plong_elven.app.services.errors import AppError
from plong_elven.app.services.internal.notification_service import NotificationService
from plong_elven.app.services.task_service import TaskService
from plong_elven.app.services.zone_service import ZoneService


@pytest.mark.asyncio
async def test_two_staff_racing_for_last_slot_only_one_wins(factory) -> None:
    """Two concurrent accepts of the last slot: one wins, the other gets 409 slots_full."""
    zone_id, tag = f"race-{uuid4().hex[:8]}", uuid4().hex[:8]
    async with factory() as s:
        s.add(_zone(zone_id))
        users = [
            User(
                role_id=1,  # floor_staff, seeded by migration
                email=f"{tag}-{i}@race.test",
                password_hash="x",
                full_name=f"R{i}",
                is_active=True,
            )
            for i in range(2)
        ]
        s.add_all(users)
        await s.flush()
        t = Task(
            zone_id=zone_id,
            total_staff=1,
            instruction="race",
            expires_at=datetime.now(UTC) + timedelta(minutes=1),
        )
        s.add(t)
        await s.commit()
        task_id, user_ids = t.task_id, [u.user_id for u in users]

    async def accept(user_id):
        async with factory() as s:
            user = await s.get(User, user_id)
            svc = TaskService(s, NotificationService(factory, FakePushSender()))
            try:
                out = await svc.accept(task_id, user)
                return "ok" if out.acceptedCount == 1 else f"count={out.acceptedCount}"
            except AppError as e:
                return e.code

    try:
        results = sorted(await asyncio.gather(*(accept(u) for u in user_ids)))
        async with factory() as s:
            rows = await s.scalar(
                select(func.count())
                .select_from(TaskRespondent)
                .where(TaskRespondent.task_id == task_id)
            )
        assert results == ["ok", "slots_full"], f"Expected one winner, got {results}"
        assert rows == 1, f"Expected 1 respondent row, got {rows}"
    finally:
        await _cleanup(factory, zone_id, user_ids)


@pytest.mark.asyncio
async def test_two_managers_broadcasting_same_zone_only_one_wins(factory) -> None:
    """The zone row lock serializes the is_active_task check."""
    zone_id = f"race-{uuid4().hex[:8]}"
    async with factory() as s:
        s.add(_zone(zone_id))
        await s.commit()

    async def broadcast():
        async with factory() as s:
            try:
                await TaskService(s, None).broadcast(
                    zone_id, 1, "go", lambda *a, **k: None
                )
                return "ok"
            except AppError as e:
                return e.code

    try:
        results = sorted(await asyncio.gather(broadcast(), broadcast()))
        assert results == ["broadcast_open", "ok"], results
        async with factory() as s:
            zone = await s.get(Zone, zone_id)
            assert zone.is_active_task is True
    finally:
        await _cleanup(factory, zone_id)


@pytest.mark.asyncio
async def test_layout_delete_cascades_logs_and_refuses_zones_with_tasks(
    factory,
) -> None:
    keep, gone, busy = (f"lay-{uuid4().hex[:8]}" for _ in range(3))
    # Zones already in the DB are passed back unchanged, so the test never edits
    # or deletes them.
    async with factory() as s:
        before = await ZoneService(s).get_layout()
    try:
        async with factory() as s:
            s.add_all([_zone(keep), _zone(gone), _zone(busy)])
            await s.flush()
            s.add(
                ZoneLog(
                    zone_id=gone,
                    bucket_start=datetime.now(UTC),
                    avg_count=1,
                    peak_count=1,
                )
            )
            s.add(
                Task(
                    zone_id=busy,
                    total_staff=1,
                    instruction="x",
                    expires_at=datetime.now(UTC),
                )
            )
            await s.commit()

        def layout(*ids):
            return before + [
                LayoutZone(
                    id=z, name="Z", polygon=[[0, 0], [1, 0], [1, 1]], threshold=7
                )
                for z in ids
            ]

        async with factory() as s:
            with pytest.raises(AppError) as exc:
                await ZoneService(s).save_layout(layout(keep))  # drops gone + busy
            assert exc.value.code == "zone_in_use"

        async with factory() as s:
            await ZoneService(s).save_layout(layout(keep, busy))  # drops only gone
        async with factory() as s:
            assert await s.get(Zone, gone) is None
            logs = await s.scalar(
                select(func.count()).select_from(ZoneLog).where(ZoneLog.zone_id == gone)
            )
            assert logs == 0
            assert (await s.get(Zone, keep)).threshold == 7
    finally:
        for zone_id in (keep, gone, busy):
            async with factory() as s:
                await s.execute(delete(ZoneLog).where(ZoneLog.zone_id == zone_id))
                await s.commit()
            await _cleanup(factory, zone_id)


@pytest.mark.asyncio
async def test_task_push_skips_staff_whose_home_zone_is_the_task_zone(factory) -> None:
    """SQL NULL != 'z' is not true: staff with no home zone must still be pushed."""
    here, there, tag = (f"push-{uuid4().hex[:8]}" for _ in range(3))
    async with factory() as s:
        s.add_all([_zone(here), _zone(there)])
        await s.flush()
        users = [
            User(
                role_id=1,  # floor_staff
                email=f"{tag}-{name}@push.test",
                password_hash="x",
                full_name=name,
                is_active=True,
                push_token=f"{tag}-{name}",
                zone_id=home,
            )
            for name, home in [("here", here), ("there", there), ("none", None)]
        ]
        s.add_all(users)
        await s.commit()
        user_ids = [u.user_id for u in users]

    sender = FakePushSender()
    try:
        await NotificationService(factory, sender).notify_role(
            "floor_staff", "New task", "x", exclude_zone_id=here
        )
        ((tokens, _, _),) = sender.calls
        mine = sorted(t for t in tokens if t.startswith(tag))  # ignore other DB users
        assert mine == [f"{tag}-none", f"{tag}-there"]
    finally:
        async with factory() as s:
            await s.execute(delete(User).where(User.user_id.in_(user_ids)))
            await s.commit()
        await _cleanup(factory, here)
        await _cleanup(factory, there)
