"""Zone layout schemas (for the zone editor)."""

from typing import Annotated

from pydantic import BaseModel, Field, StringConstraints

from plong_elven.schemas.common import CAMERA_ID_PATTERN

# A normalized [x, y] point, both coordinates within 0..1.
Point = Annotated[
    list[Annotated[float, Field(ge=0, le=1)]], Field(min_length=2, max_length=2)
]


class LayoutZone(BaseModel):
    """Renamed from zones.Zone to avoid clash with db.models.Zone."""

    # Zone ids become the keys the worker posts in `counts`, so they use the
    # same pattern (P6).
    id: Annotated[str, StringConstraints(pattern=CAMERA_ID_PATTERN)]
    name: Annotated[str, StringConstraints(min_length=1, max_length=100)]
    polygon: list[Point] = Field(min_length=3, max_length=100)


class ZonesIn(BaseModel):
    anchor: str
    zones: list[LayoutZone] = Field(max_length=50)
