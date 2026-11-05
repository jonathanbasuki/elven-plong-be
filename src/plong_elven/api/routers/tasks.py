"""Task routes, prefix /api/v1/tasks.

A manager starts a task by broadcasting it (POST /tasks/broadcast). Roles do not
inherit and admin has no dispatch powers: broadcast/finish need ops_manager,
accept/complete need floor_staff, the list is open to any role.
"""

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, Query

from plong_elven.api.deps import get_current_user, get_task_service, require_role
from plong_elven.db.models import User
from plong_elven.schemas.tasks import BroadcastTaskRequest, FinishOut, TaskOut
from plong_elven.services.task_service import TaskService

router = APIRouter(prefix="/api/v1/tasks", tags=["tasks"])


@router.get("")
async def list_tasks(
    zoneId: str | None = Query(None),
    status: Literal["open", "closed", "finished"] | None = Query(None),
    limit: int = Query(20, ge=1, le=50),
    service: TaskService = Depends(get_task_service),  # noqa: B008
    _user: User = Depends(get_current_user),  # noqa: B008
) -> list[TaskOut]:
    """Newest first. A zone's current task is ?zoneId=..&status=open."""
    return await service.list(zoneId, status, limit)


@router.post("/broadcast", status_code=201)
async def broadcast_task(
    payload: BroadcastTaskRequest,
    background_tasks: BackgroundTasks,
    service: TaskService = Depends(get_task_service),  # noqa: B008
    _user: User = Depends(require_role("ops_manager")),  # noqa: B008
) -> TaskOut:
    """Start a task in a zone and push it to floor staff from other zones."""
    return await service.broadcast(
        payload.zoneId,
        payload.totalStaff,
        payload.instruction,
        background_tasks.add_task,
    )


@router.post("/{taskId}/accept")
async def accept_task(
    taskId: UUID,
    service: TaskService = Depends(get_task_service),  # noqa: B008
    user: User = Depends(require_role("floor_staff")),  # noqa: B008
) -> TaskOut:
    return await service.accept(taskId, user)


@router.post("/{taskId}/complete")
async def complete_task(
    taskId: UUID,
    service: TaskService = Depends(get_task_service),  # noqa: B008
    user: User = Depends(require_role("floor_staff")),  # noqa: B008
) -> TaskOut:
    """403 not_your_response when this staff member did not accept."""
    return await service.complete(taskId, user)


@router.post("/{taskId}/finish")
async def finish_task(
    taskId: UUID,
    service: TaskService = Depends(get_task_service),  # noqa: B008
    _user: User = Depends(require_role("ops_manager")),  # noqa: B008
) -> FinishOut:
    return await service.finish(taskId)
