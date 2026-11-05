"""Zone layout schemas: the camera's zone list and the dev zone editor."""

from typing import Annotated

from pydantic import BaseModel, Field, StringConstraints

from plong_elven.schemas.common import CAMERA_ID_PATTERN

# A normalized [x, y] point in the camera frame, both coordinates within 0..1.
Point = Annotated[
    list[Annotated[float, Field(ge=0, le=1)]], Field(min_length=2, max_length=2)
]

# Zone ids become the keys the camera posts in `counts`, so they use the same
# pattern as camera ids (P6).
ZoneId = Annotated[str, StringConstraints(pattern=CAMERA_ID_PATTERN)]


class LayoutZone(BaseModel):
    """One zone as the dev editor reads and writes it."""

    id: ZoneId
    name: Annotated[str, StringConstraints(min_length=1, max_length=100)]
    polygon: list[Point] = Field(min_length=3, max_length=100)
    threshold: int = Field(gt=0, le=10000)


class ZonesIn(BaseModel):
    """The full zone set. Zones missing from it are deleted."""

    zones: list[LayoutZone] = Field(max_length=50)


class CameraZoneOut(BaseModel):
    """What the camera process needs to assign people to zones."""

    id: str
    name: str
    polygon: list[list[float]]


class CameraZonesOut(BaseModel):
    zones: list[CameraZoneOut]
