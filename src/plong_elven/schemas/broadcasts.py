"""Broadcast and staff DTOs (design §6.1-6.2). Field names are camelCase (P1).

Datetimes use UtcDatetime, so JSON ends in Z (Review Focus 5).
"""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from plong_elven.schemas.zones import UtcDatetime

BroadcastStatus = Literal["open", "closed", "finished"]


class CreateBroadcastRequest(BaseModel):
    zoneId: str
    staffNeeded: int = Field(gt=0)


class AcceptedStaffOut(BaseModel):
    staffId: UUID
    name: str
    completedAt: UtcDatetime | None


class BroadcastOut(BaseModel):
    broadcastId: UUID
    zoneId: str
    status: BroadcastStatus
    staffNeeded: int
    expiresAt: UtcDatetime
    acceptedCount: int
    completedCount: int
    acceptedStaff: list[AcceptedStaffOut]


class FinishOut(BaseModel):
    broadcastId: UUID
    status: Literal["finished"]
    finishedAt: UtcDatetime


class StaffOut(BaseModel):
    staffId: UUID
    name: str
    status: Literal["idle", "delegating"]
