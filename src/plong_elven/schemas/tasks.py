"""Task and staff DTOs. Field names are camelCase (P1).

Datetimes use UtcDatetime, so JSON ends in Z.
"""

from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, Field, StringConstraints

from plong_elven.schemas.zones import UtcDatetime

TaskStatus = Literal["open", "closed", "finished"]
RespondentStatus = Literal["ongoing", "completed"]


class BroadcastTaskRequest(BaseModel):
    """Body of POST /api/v1/tasks/broadcast (a manager starting a task)."""

    zoneId: str
    totalStaff: int = Field(gt=0)
    instruction: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=50)
    ]


class AcceptedStaffOut(BaseModel):
    staffId: UUID
    name: str
    status: RespondentStatus
    completedAt: UtcDatetime | None


class TaskOut(BaseModel):
    taskId: UUID
    zoneId: str
    status: TaskStatus
    totalStaff: int
    instruction: str
    expiresAt: UtcDatetime
    acceptedCount: int
    completedCount: int
    acceptedStaff: list[AcceptedStaffOut]


class FinishOut(BaseModel):
    taskId: UUID
    status: Literal["finished"]
    finishedAt: UtcDatetime


class StaffOut(BaseModel):
    staffId: UUID
    name: str
    status: Literal["idle", "delegating"]
    zoneId: str | None
