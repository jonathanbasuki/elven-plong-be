"""Zone and crowd read DTOs (design §6.2). Field names are camelCase (P1)."""

from datetime import UTC, datetime
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel


def _as_utc(value: datetime) -> datetime:
    """Read naive datetimes as UTC and convert aware ones, so JSON ends in Z."""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


UtcDatetime = Annotated[datetime, AfterValidator(_as_utc)]


class ZoneOut(BaseModel):
    """The frontend draws the zone on its own floorplan and matches it by zoneId;
    the camera polygon is not part of this contract."""

    zoneId: str
    name: str
    threshold: int
    isActiveTask: bool


class CrowdOut(BaseModel):
    zoneId: str
    count: int
    status: Literal["safe", "crowded"]
    observedAt: UtcDatetime | None


class HistoryBucketOut(BaseModel):
    bucketStart: UtcDatetime
    avgCount: float
    peakCount: int
