"""Tests for ZoneService: zone reads, the camera zone list, and layout edits."""

import pytest
from test_tasks import MemorySession

from plong_elven.db.models import TaskStatusId, Zone
from plong_elven.schemas.layout import CameraZoneOut, LayoutZone
from plong_elven.schemas.zones import ZoneOut
from plong_elven.services.errors import AppError
from plong_elven.services.zone_service import ZoneService

TRI = [[0.0, 0.0], [1.0, 0.0], [1.0, 1.0]]


def _session_with_two_zones() -> MemorySession:
    s = MemorySession()
    s.zones["z2"] = Zone(zone_id="z2", name="B", polygon=TRI, threshold=6)
    s.zones["z2"].is_active_task = False
    return s


@pytest.mark.asyncio
async def test_list_zones_returns_zone_payloads() -> None:
    result = await ZoneService(_session_with_two_zones()).list_zones()
    assert result == [
        ZoneOut(zoneId="z1", name="Main Lobby", threshold=10, isActiveTask=False),
        ZoneOut(zoneId="z2", name="B", threshold=6, isActiveTask=False),
    ]


@pytest.mark.asyncio
async def test_zone_reads_apply_lazy_task_expiry() -> None:
    """An expired task with no respondents frees the zone before it is read."""
    s = MemorySession()
    s.add_task(expires_in=-1)
    assert s.zones["z1"].is_active_task is True

    assert (await ZoneService(s).get_zone("z1")).isActiveTask is False


@pytest.mark.asyncio
async def test_get_zone_not_found_raises_app_error() -> None:
    with pytest.raises(AppError) as exc:
        await ZoneService(MemorySession()).get_zone("z9")
    assert exc.value.status_code == 404
    assert exc.value.code == "not_found"


@pytest.mark.asyncio
async def test_camera_zones_carry_polygons() -> None:
    result = await ZoneService(MemorySession()).camera_zones()
    assert result == [CameraZoneOut(id="z1", name="Main Lobby", polygon=TRI)]


@pytest.mark.asyncio
async def test_save_layout_updates_adds_and_deletes() -> None:
    s = _session_with_two_zones()
    new = [
        LayoutZone(id="z1", name="Lobby", polygon=TRI, threshold=12),
        LayoutZone(id="z3", name="New", polygon=TRI, threshold=4),
    ]

    assert await ZoneService(s).save_layout(new) == 2

    assert sorted(s.zones) == ["z1", "z3"]  # z2 deleted
    assert (s.zones["z1"].name, s.zones["z1"].threshold) == ("Lobby", 12)
    assert s.zones["z3"].threshold == 4
    assert s.commits == 1


@pytest.mark.asyncio
async def test_save_layout_refuses_to_delete_a_zone_with_tasks() -> None:
    s = _session_with_two_zones()
    s.add_task(zone_id="z2", status=TaskStatusId.FINISHED)

    with pytest.raises(AppError) as exc:
        await ZoneService(s).save_layout(
            [LayoutZone(id="z1", name="A", polygon=TRI, threshold=10)]
        )
    assert exc.value.code == "zone_in_use"
    assert "z2" in s.zones and s.commits == 0


@pytest.mark.asyncio
async def test_save_layout_rejects_duplicate_ids() -> None:
    z = LayoutZone(id="z1", name="A", polygon=TRI, threshold=10)
    with pytest.raises(AppError) as exc:
        await ZoneService(MemorySession()).save_layout([z, z])
    assert exc.value.status_code == 422
