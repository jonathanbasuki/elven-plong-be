"""Zone reads: GET /api/v1/zones and GET /api/v1/zones/{zoneId}, any role (§6.1).

Live count and status are not here; they are on /zones/{zoneId}/crowd (crowd.py).
"""

from fastapi import APIRouter, Depends

from plong_elven.app.api.deps import get_current_user, get_zone_service
from plong_elven.app.db.models import User
from plong_elven.app.schemas.zones import ZoneOut
from plong_elven.app.services.zone_service import ZoneService

router = APIRouter(prefix="/api/v1/zones", tags=["zones"])


@router.get("")
async def list_zones(
    svc: ZoneService = Depends(get_zone_service),  # noqa: B008
    _user: User = Depends(get_current_user),  # noqa: B008
) -> list[ZoneOut]:
    """Lets the client discover real zone ids instead of hardcoding one; this is a
    single-zone POC, so the app uses whichever zone comes back first."""
    return await svc.list_zones()


@router.get("/{zoneId}")
async def get_zone(
    zoneId: str,
    svc: ZoneService = Depends(get_zone_service),  # noqa: B008
    _user: User = Depends(get_current_user),  # noqa: B008
) -> ZoneOut:
    return await svc.get_zone(zoneId)
