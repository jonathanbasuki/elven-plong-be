"""Staff routes: GET /api/v1/staff[?zoneId=] and GET /api/v1/staff/{staffId}, any role."""

from uuid import UUID

from fastapi import APIRouter, Depends, Query

from plong_elven.api.deps import get_current_user, get_task_service
from plong_elven.db.models import User
from plong_elven.schemas.tasks import StaffOut
from plong_elven.services.task_service import TaskService

router = APIRouter(prefix="/api/v1/staff", tags=["staff"])


@router.get("")
async def list_staff(
    zoneId: str | None = Query(None),
    service: TaskService = Depends(get_task_service),  # noqa: B008
    _user: User = Depends(get_current_user),  # noqa: B008
) -> list[StaffOut]:
    """Active floor staff, ordered by name. zoneId keeps only that home zone."""
    return await service.list_staff(zoneId)


@router.get("/{staffId}")
async def get_staff(
    staffId: UUID,
    service: TaskService = Depends(get_task_service),  # noqa: B008
    _user: User = Depends(get_current_user),  # noqa: B008
) -> StaffOut:
    return await service.get_staff(staffId)
