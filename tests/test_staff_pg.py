"""Real-Postgres check of the staff list (GET /api/v1/staff): it joins users to
roles, which the in-memory test session cannot evaluate."""

from uuid import uuid4

import pytest
from conftest import pg_cleanup, pg_zone

from plong_elven.db.models import User
from plong_elven.services.task_service import TaskService


@pytest.mark.asyncio
async def test_list_staff_floor_staff_only_by_home_zone(factory) -> None:
    zone_a, zone_b, tag = (f"st-{uuid4().hex[:8]}" for _ in range(3))
    people = [  # (name, role_id, home zone, active); role 1 floor_staff, 2 ops_manager
        ("Ani", 1, zone_a, True),
        ("Budi", 1, zone_b, True),
        ("Cici", 1, zone_a, False),
        ("Mgr", 2, zone_a, True),
        ("Dodi", 1, None, True),
    ]
    async with factory() as s:
        s.add_all([pg_zone(zone_a), pg_zone(zone_b)])
        await s.flush()
        users = [
            User(
                role_id=role,
                email=f"{tag}-{name}@staff.test",
                password_hash="x",
                full_name=name,
                is_active=active,
                zone_id=home,
            )
            for name, role, home, active in people
        ]
        s.add_all(users)
        await s.commit()
        ids = {u.user_id for u in users}

    def mine(rows):
        return [r.name for r in rows if r.staffId in ids]  # ignore other DB users

    try:
        async with factory() as s:
            svc = TaskService(s, None)
            in_a = await svc.list_staff(zone_a)
            assert mine(in_a) == ["Ani"]
            assert in_a[0].zoneId == zone_a and in_a[0].status == "idle"
            assert mine(await svc.list_staff(None)) == ["Ani", "Budi", "Dodi"]
            assert await svc.list_staff("no-such-zone") == []
    finally:
        await pg_cleanup(factory, zone_a, ids)
        await pg_cleanup(factory, zone_b)
