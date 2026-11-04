# tests/test_broadcast_race_pg.py
"""Real-Postgres proof of the accept row lock (design §5). Skips without a DB unless
REQUIRE_PG=1 (set in CI), so a broken CI database fails instead of silently skipping."""

import asyncio
import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from conftest import FakePushSender
from sqlalchemy import delete, func, select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from plong_elven.db.models import Broadcast, BroadcastResponse, Role, User, Zone
from plong_elven.services.broadcast_service import BroadcastService
from plong_elven.services.errors import AppError
from plong_elven.services.internal.notification_service import NotificationService


@pytest.mark.asyncio
async def test_two_staff_racing_for_last_slot_only_one_wins() -> None:
    """Two concurrent accepts of the last slot: exactly one wins, the other gets 409 slots_full."""
    db_url = os.environ.get("DATABASE_URL", "")
    if not db_url:
        if os.environ.get("REQUIRE_PG") == "1":
            pytest.fail("REQUIRE_PG=1 but DATABASE_URL is not set")
        pytest.skip("DATABASE_URL not set (no Postgres)")

    engine = create_async_engine(db_url, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except (OSError, SQLAlchemyError) as e:
        await engine.dispose()
        if os.environ.get("REQUIRE_PG") == "1":
            pytest.fail(f"REQUIRE_PG=1 but no Postgres reachable: {e}")
        pytest.skip(f"no Postgres reachable: {e}")

    factory = async_sessionmaker(engine, expire_on_commit=False)
    zone_id, tag = f"race-{uuid4().hex[:8]}", uuid4().hex[:8]

    # Setup: create zone, role, users, and broadcast
    async with factory() as s:
        # Ensure role_id=1 exists (floor_staff)
        role = await s.get(Role, 1)
        if role is None:
            s.add(Role(id=1, name="floor_staff"))
            await s.flush()

        s.add(
            Zone(
                zone_id=zone_id,
                name="Race",
                map_image_url="u",
                polygon=[],
                warning_threshold=5,
                crowded_threshold=10,
            )
        )
        users = [
            User(
                role_id=1,
                email=f"{tag}-{i}@race.test",
                password_hash="x",
                full_name=f"R{i}",
                is_active=True,
            )
            for i in range(2)
        ]
        s.add_all(users)
        await s.flush()
        b = Broadcast(
            zone_id=zone_id,
            staff_needed=1,
            expires_at=datetime.now(UTC) + timedelta(minutes=1),
        )
        s.add(b)
        await s.commit()
        broadcast_id, user_ids = b.broadcast_id, [u.id for u in users]

    async def accept(user_id):
        async with factory() as s:
            user = await s.get(User, user_id)
            svc = BroadcastService(s, NotificationService(factory, FakePushSender()))
            try:
                # accept returns the updated BroadcastOut (Task 2.10)
                out = await svc.accept(broadcast_id, user)
                return "ok" if out.acceptedCount == 1 else f"count={out.acceptedCount}"
            except AppError as e:
                return e.code

    try:
        results = sorted(await asyncio.gather(*(accept(u) for u in user_ids)))
        async with factory() as s:
            rows = await s.scalar(
                select(func.count())
                .select_from(BroadcastResponse)
                .where(BroadcastResponse.broadcast_id == broadcast_id)
            )
        assert results == ["ok", "slots_full"], (
            f"Expected exactly one winner, got {results}"
        )
        assert rows == 1, f"Expected 1 response row, got {rows}"
    finally:
        # Cleanup test data
        async with factory() as s:
            await s.execute(
                delete(BroadcastResponse).where(
                    BroadcastResponse.broadcast_id == broadcast_id
                )
            )
            await s.execute(
                delete(Broadcast).where(Broadcast.broadcast_id == broadcast_id)
            )
            await s.execute(delete(User).where(User.id.in_(user_ids)))
            await s.execute(delete(Zone).where(Zone.zone_id == zone_id))
            await s.commit()
        await engine.dispose()
