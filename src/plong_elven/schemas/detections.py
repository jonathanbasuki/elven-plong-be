"""Detection schemas for the worker route POST /api/v1/cameras/{cameraId}/detections."""

from typing import Annotated

from pydantic import BaseModel, Field, StringConstraints

from plong_elven.schemas.common import CAMERA_ID_PATTERN

# P6: zone keys become in-memory dict keys, so they use the cameraId pattern.
ZoneKey = Annotated[str, StringConstraints(pattern=CAMERA_ID_PATTERN)]

# §6.4: at most 50 zones per post, each count an int in 0..10000.
ZoneCount = Annotated[int, Field(ge=0, le=10000)]


class DetectionIn(BaseModel):
    counts: dict[ZoneKey, ZoneCount] = Field(max_length=50)
