"""Task routes, prefix /api/v1/tasks.

A manager starts a task by broadcasting it (POST /tasks/broadcast). Roles do not
inherit and admin has no dispatch powers: broadcast/finish need ops_manager,
accept/complete need floor_staff, the list is open to any role.
"""

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from plong_elven.api.deps import get_current_user, get_task_service, require_role
from plong_elven.db.models import TaskFootage, User
from plong_elven.db.session import get_db
from plong_elven.schemas.tasks import (
    BroadcastTaskRequest,
    FinishOut,
    TaskDetailOut,
    TaskFootageOut,
    TaskOut,
)
from plong_elven.services.task_service import TaskService
from plong_elven.state.live_state import LiveState, get_live_state

router = APIRouter(prefix="/api/v1/tasks", tags=["tasks"])


@router.get("")
async def list_tasks(
    zoneId: str | None = Query(None),
    status: list[Literal["open", "closed", "finished"]] | None = Query(None),  # noqa: B008
    limit: int = Query(20, ge=1, le=50),
    service: TaskService = Depends(get_task_service),  # noqa: B008
    _user: User = Depends(get_current_user),  # noqa: B008
) -> list[TaskOut]:
    """Newest first. Repeat `status` to match several (?status=open&status=closed).
    A zone's current task is ?zoneId=..&status=open."""
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


@router.get("/{taskId}")
async def get_task(
    taskId: UUID,
    service: TaskService = Depends(get_task_service),  # noqa: B008
    live: LiveState = Depends(get_live_state),  # noqa: B008
    _user: User = Depends(get_current_user),  # noqa: B008
) -> TaskDetailOut:
    """One task: zone, live crowd while open or the 5-minute crowd log once
    completed, the zone's own staff and the delegated staff."""
    return await service.detail(taskId, live)


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


@router.post("/{taskId}/cancel")
async def cancel_task(
    taskId: UUID,
    service: TaskService = Depends(get_task_service),  # noqa: B008
    _user: User = Depends(require_role("ops_manager")),  # noqa: B008
) -> TaskOut:
    """Cancel an open task before the staff-gathering window ends."""
    return await service.cancel(taskId)


@router.get("/{taskId}/footage")
async def get_footage(
    taskId: UUID,
    session: AsyncSession = Depends(get_db),  # noqa: B008
    _user: User = Depends(get_current_user),  # noqa: B008
) -> TaskFootageOut:
    footage = await session.get(TaskFootage, taskId)
    if footage is None:
        from plong_elven.services.errors import AppError

        raise AppError(404, "not_found", f"footage for task {taskId} not found")
    return TaskFootageOut(
        taskId=taskId,
        status=footage.status,
        url=footage.public_url,
        error=footage.error,
    )
