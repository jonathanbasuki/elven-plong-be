"""Tests for ZoneService: the zone catalogue (list and one zone)."""

import pytest

from plong_elven.db.models import Zone
from plong_elven.schemas.zones import ZoneOut
from plong_elven.services.errors import AppError
from plong_elven.services.zone_service import ZoneService

ZONE = Zone(
    zone_id="z1",
    name="Main Lobby",
    map_image_url="u",
    polygon=[],
    warning_threshold=5,
    crowded_threshold=10,
)


class _ZoneSession:
    def __init__(self, zone: Zone | None = ZONE):
        self._zone = zone

    async def get(self, model, key):
        if self._zone is not None and key == self._zone.zone_id:
            return self._zone
        return None


@pytest.mark.asyncio
async def test_list_zones_returns_zone_payloads() -> None:
    class _MultiZoneSession:
        async def scalars(self, stmt):
            zones = [
                Zone(
                    zone_id="z1",
                    name="A",
                    map_image_url="u1",
                    polygon=[],
                    warning_threshold=5,
                    crowded_threshold=10,
                ),
                Zone(
                    zone_id="z2",
                    name="B",
                    map_image_url="u2",
                    polygon=[],
                    warning_threshold=3,
                    crowded_threshold=6,
                ),
            ]
            return type("R", (), {"all": lambda self: zones})()

    svc = ZoneService(_MultiZoneSession())
    result = await svc.list_zones()
    assert [z.zoneId for z in result] == ["z1", "z2"]
    assert result[0] == ZoneOut(zoneId="z1", name="A", mapImageUrl="u1", polygon=[])


@pytest.mark.asyncio
async def test_get_zone_returns_zone_without_live_fields() -> None:
    svc = ZoneService(_ZoneSession())
    result = await svc.get_zone("z1")
    assert result == ZoneOut(
        zoneId="z1", name="Main Lobby", mapImageUrl="u", polygon=[]
    )


@pytest.mark.asyncio
async def test_get_zone_not_found_raises_app_error() -> None:
    svc = ZoneService(_ZoneSession(None))
    with pytest.raises(AppError) as exc:
        await svc.get_zone("z9")
    assert exc.value.status_code == 404
    assert exc.value.code == "not_found"
