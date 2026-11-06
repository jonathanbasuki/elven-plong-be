"""Task and staff DTOs. Field names are camelCase (P1).

Datetimes use UtcDatetime, so JSON ends in Z.
"""

from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, Field, StringConstraints

from plong_elven.schemas.zones import HistoryBucketOut, UtcDatetime

TaskStatus = Literal["open", "ongoing", "closed", "finished", "complete"]
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


class LiveCrowdOut(BaseModel):
    count: int
    observedAt: UtcDatetime | None


class TaskDetailOut(BaseModel):
    """GET /api/v1/tasks/{taskId}. While `phase` is "ongoing" (status open)
    `liveCrowd` is set and `crowdLog` is []; once "completed" `liveCrowd` is
    null and `crowdLog` holds the 5-minute buckets, oldest first."""

    taskId: UUID
    zoneId: str
    zoneName: str
    zoneStatus: Literal["safe", "crowded"]
    instruction: str
    status: TaskStatus
    phase: Literal["ongoing", "completed"]
    createdAt: UtcDatetime
    expiresAt: UtcDatetime
    finishedAt: UtcDatetime | None
    totalStaff: int
    acceptedCount: int
    completedCount: int
    liveCrowd: LiveCrowdOut | None
    crowdLog: list[HistoryBucketOut]
    zoneStaff: list[StaffOut]
    delegatedStaff: list[AcceptedStaffOut]


class TaskFootageOut(BaseModel):
    taskId: UUID
    status: Literal["processing", "ready", "failed"]
    url: str | None = None
    error: str | None = None
