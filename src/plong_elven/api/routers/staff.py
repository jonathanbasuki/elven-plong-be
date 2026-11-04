"""Staff lookup: GET /api/v1/staff/{staffId}, any role (design §6.1)."""

from uuid import UUID

from fastapi import APIRouter, Depends

from plong_elven.api.deps import get_broadcast_service, get_current_user
from plong_elven.db.models import User
from plong_elven.schemas.broadcasts import StaffOut
from plong_elven.services.broadcast_service import BroadcastService

router = APIRouter(prefix="/api/v1/staff", tags=["staff"])


@router.get("/{staffId}")
async def get_staff(
    staffId: UUID,
    service: BroadcastService = Depends(get_broadcast_service),  # noqa: B008
    _user: User = Depends(get_current_user),  # noqa: B008
) -> StaffOut:
    return await service.get_staff(staffId)
