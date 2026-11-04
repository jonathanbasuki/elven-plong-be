"""Zone service: read the zone catalogue (list and one zone).

Doesn't import FastAPI. Live count and status are not here; see CrowdService.
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from plong_elven.db.models import Zone
from plong_elven.schemas.zones import ZoneOut
from plong_elven.services.errors import AppError


class ZoneService:
    """Reads zones from the database, without live fields (§6.2)."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_zones(self) -> list[ZoneOut]:
        """Return all zones ordered by zone_id."""
        zones = await self._session.scalars(select(Zone).order_by(Zone.zone_id))
        return [_zone_out(zone) for zone in zones.all()]

    async def get_zone(self, zone_id: str) -> ZoneOut:
        """Return one zone.

        Raises:
            AppError(404, "not_found"): If the zone does not exist.
        """
        zone = await self._session.get(Zone, zone_id)
        if zone is None:
            raise AppError(404, "not_found", f"zone {zone_id} not found")
        return _zone_out(zone)


def _zone_out(zone: Zone) -> ZoneOut:
    return ZoneOut(
        zoneId=zone.zone_id,
        name=zone.name,
        mapImageUrl=zone.map_image_url,
        polygon=zone.polygon,
    )
