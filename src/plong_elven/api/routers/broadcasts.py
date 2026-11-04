"""Broadcast routes, prefix /api/v1/broadcasts (design §6.1, §6.3).

Roles do not inherit and admin has no dispatch powers: create/finish need
ops_manager, accept/complete need floor_staff, the list is open to any role.
"""

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, Query

from plong_elven.api.deps import (
    get_broadcast_service,
    get_current_user,
    require_role,
)
from plong_elven.db.models import User
from plong_elven.schemas.broadcasts import (
    BroadcastOut,
    CreateBroadcastRequest,
    FinishOut,
)
from plong_elven.services.broadcast_service import BroadcastService

router = APIRouter(prefix="/api/v1/broadcasts", tags=["broadcasts"])


@router.get("")
async def list_broadcasts(
    zoneId: str | None = Query(None),
    status: Literal["open", "closed", "finished"] | None = Query(None),
    limit: int = Query(20, ge=1, le=50),
    service: BroadcastService = Depends(get_broadcast_service),  # noqa: B008
    _user: User = Depends(get_current_user),  # noqa: B008
) -> list[BroadcastOut]:
    """Newest first. The old "current" call is ?zoneId=..&status=open."""
    return await service.list(zoneId, status, limit)


@router.post("", status_code=201)
async def create_broadcast(
    payload: CreateBroadcastRequest,
    background_tasks: BackgroundTasks,
    service: BroadcastService = Depends(get_broadcast_service),  # noqa: B008
    _user: User = Depends(require_role("ops_manager")),  # noqa: B008
) -> BroadcastOut:
    return await service.create(
        payload.zoneId, payload.staffNeeded, background_tasks.add_task
    )


@router.post("/{broadcastId}/accept")
async def accept_broadcast(
    broadcastId: UUID,
    service: BroadcastService = Depends(get_broadcast_service),  # noqa: B008
    user: User = Depends(require_role("floor_staff")),  # noqa: B008
) -> BroadcastOut:
    return await service.accept(broadcastId, user)


@router.post("/{broadcastId}/complete")
async def complete_broadcast(
    broadcastId: UUID,
    service: BroadcastService = Depends(get_broadcast_service),  # noqa: B008
    user: User = Depends(require_role("floor_staff")),  # noqa: B008
) -> BroadcastOut:
    """403 not_your_response when this staff member did not accept."""
    return await service.complete(broadcastId, user)


@router.post("/{broadcastId}/finish")
async def finish_broadcast(
    broadcastId: UUID,
    service: BroadcastService = Depends(get_broadcast_service),  # noqa: B008
    _user: User = Depends(require_role("ops_manager")),  # noqa: B008
) -> FinishOut:
    return await service.finish(broadcastId)
