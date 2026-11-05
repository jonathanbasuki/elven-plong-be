"""Zone service: the zone catalogue, the camera's zone list, and zone edits.

Zones live only in the database (there is no zones.json any more). Doesn't
import FastAPI. Live count and status are not here; see CrowdService.
"""

import builtins
import logging
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from plong_elven.db.models import Task, Zone
from plong_elven.schemas.layout import CameraZoneOut, LayoutZone
from plong_elven.schemas.zones import ZoneOut
from plong_elven.services.errors import AppError
from plong_elven.services.task_service import close_expired_tasks

logger = logging.getLogger(__name__)


def _usable_polygon(polygon: object) -> bool:
    """At least 3 points of exactly two numbers: what the camera's pixel math
    needs. Rows edited by hand in the DB may not be."""
    return (
        isinstance(polygon, list)
        and len(polygon) >= 3
        and all(
            isinstance(p, list)
            and len(p) == 2
            and all(isinstance(v, int | float) and not isinstance(v, bool) for v in p)
            for p in polygon
        )
    )


class ZoneService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def _refresh_task_flags(self, zone_id: str | None = None) -> None:
        """Apply lazy task expiry first, so is_active_task is current."""
        if await close_expired_tasks(self._session, datetime.now(UTC), zone_id):
            await self._session.commit()

    async def _all(self) -> builtins.list[Zone]:
        return list(
            (await self._session.scalars(select(Zone).order_by(Zone.zone_id))).all()
        )

    async def list_zones(self) -> builtins.list[ZoneOut]:
        """Return all zones ordered by zone_id."""
        await self._refresh_task_flags()
        return [_zone_out(zone) for zone in await self._all()]

    async def get_zone(self, zone_id: str) -> ZoneOut:
        """Return one zone. Raises AppError(404) if it does not exist."""
        await self._refresh_task_flags(zone_id)
        zone = await self._session.get(Zone, zone_id)
        if zone is None:
            raise AppError(404, "not_found", f"zone {zone_id} not found")
        return _zone_out(zone)

    async def camera_zones(self) -> builtins.list[CameraZoneOut]:
        """Zones with their camera-frame polygons, for the camera process.

        A zone with a malformed polygon is left out (and logged): it would crash
        the camera's pixel math, stopping counts for every zone.
        """
        zones = []
        for z in await self._all():
            if _usable_polygon(z.polygon):
                zones.append(
                    CameraZoneOut(id=z.zone_id, name=z.name, polygon=z.polygon)
                )
            else:
                logger.warning(
                    "zone %s has a malformed polygon; not sent to cameras", z.zone_id
                )
        return zones

    async def get_layout(self) -> builtins.list[LayoutZone]:
        """Zones as the dev editor shows them."""
        return [
            LayoutZone(
                id=z.zone_id, name=z.name, polygon=z.polygon, threshold=z.threshold
            )
            for z in await self._all()
        ]

    async def save_layout(self, zones: builtins.list[LayoutZone]) -> int:
        """Replace the zone set: update existing zones, add new ones, delete the
        rest. Returns the number of zones saved.

        A zone that has any task cannot be deleted (tasks are history), so the
        whole save is rejected with 409 zone_in_use. Deleting a zone also deletes
        its zone_logs and clears it from users' home zone / last location.
        """
        ids = [z.id for z in zones]
        if len(set(ids)) != len(ids):
            raise AppError(422, "validation_error", "zone ids must be unique")

        existing = {z.zone_id: z for z in await self._all()}
        removed = [zid for zid in existing if zid not in set(ids)]
        for zone_id in removed:
            has_tasks = await self._session.scalar(
                select(func.count()).select_from(Task).where(Task.zone_id == zone_id)
            )
            if has_tasks:
                raise AppError(
                    409,
                    "zone_in_use",
                    f"zone {zone_id} has tasks and cannot be deleted",
                )

        for zone_id in removed:
            await self._session.delete(existing[zone_id])
        for z in zones:
            row = existing.get(z.id)
            if row is None:
                self._session.add(
                    Zone(
                        zone_id=z.id,
                        name=z.name,
                        polygon=z.polygon,
                        threshold=z.threshold,
                    )
                )
            else:
                row.name, row.polygon, row.threshold = z.name, z.polygon, z.threshold
        await self._session.commit()
        return len(zones)


def _zone_out(zone: Zone) -> ZoneOut:
    return ZoneOut(
        zoneId=zone.zone_id,
        name=zone.name,
        threshold=zone.threshold,
        isActiveTask=zone.is_active_task,
    )
