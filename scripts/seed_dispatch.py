"""Seeds one zone (from zones.json) plus a manager and some staff users, for local
dispatch testing. Safe to re-run: skips rows that already exist.

  uv run --env-file .env python scripts/seed_dispatch.py
"""

import asyncio
import os

from argon2 import PasswordHasher
from sqlalchemy import select

from plong_elven.common.zones import load_config
from plong_elven.db.models import Role, User, Zone
from plong_elven.db.session import SessionFactory

ZONES_PATH = os.environ.get("ZONES_PATH", "zones.json")
PASSWORD = "password123"  # same for every seeded user, local dev only

USERS = [
    ("manager@plongelven.io", "Manager Aldi", "ops_manager"),
    ("bunga@plongelven.io", "Bunga", "floor_staff"),
    ("citra@plongelven.io", "Citra", "floor_staff"),
    ("dedi@plongelven.io", "Dedi", "floor_staff"),
]


async def main() -> None:
    hasher = PasswordHasher()
    cfg = load_config(ZONES_PATH)
    zone_cfg = cfg["zones"][0]

    async with SessionFactory() as session:
        roles = {r.name: r for r in (await session.scalars(select(Role))).all()}

        if await session.get(Zone, zone_cfg["id"]) is None:
            session.add(
                Zone(
                    zone_id=zone_cfg["id"],
                    name=zone_cfg["name"],
                    map_image_url="https://example.com/media/maps/placeholder.png",
                    polygon=zone_cfg["polygon"],
                    warning_threshold=5,
                    crowded_threshold=10,
                )
            )
            print(f"seeded zone {zone_cfg['id']!r}")
        else:
            print(f"zone {zone_cfg['id']!r} already exists, skipping")

        for email, full_name, role_name in USERS:
            existing = await session.scalar(select(User).where(User.email == email))
            if existing is not None:
                print(f"user {email!r} already exists, skipping")
                continue
            session.add(
                User(
                    role_id=roles[role_name].id,
                    email=email,
                    password_hash=hasher.hash(PASSWORD),
                    full_name=full_name,
                    is_active=True,
                )
            )
            print(f"seeded {role_name} {email!r}")

        await session.commit()

    print(f"\nAll seeded users share the password: {PASSWORD!r}")


if __name__ == "__main__":
    asyncio.run(main())
