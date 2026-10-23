"""Backs the browser zone editor: GET/PUT the shared zones config file.

# ponytail: one file for all cameras (camera_id is unused in the path today, kept for
# forward-compat with the UI's per-camera routes). Split into per-camera files if/when
# more than one camera needs different zone layouts.
"""

import os

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

from plong_elven.api.security import check_internal_key
from plong_elven.common.zones import ANCHORS, save_config
from plong_elven.common.zones import load_config as load_zones_config

router = APIRouter(tags=["zones"])


def _zones_path() -> str:
    # Matches the cctv worker's own --zones default, so the editor and the detection
    # loop read/write the same file without extra configuration.
    return os.environ.get("ZONES_PATH", "zones.json")


class Zone(BaseModel):
    id: str
    name: str
    polygon: list[list[float]]


class ZonesIn(BaseModel):
    anchor: str
    zones: list[Zone]


@router.get("/zones/{camera_id}")
async def get_zones(camera_id: str) -> dict:
    path = _zones_path()
    if not os.path.exists(path):
        return {"anchor": "bottom_center", "zones": []}
    return load_zones_config(path)


@router.put("/zones/{camera_id}")
async def put_zones(
    camera_id: str, body: ZonesIn, x_internal_api_key: str | None = Header(default=None)
) -> dict:
    check_internal_key(x_internal_api_key)
    if body.anchor not in ANCHORS:
        raise HTTPException(status_code=422, detail=f"anchor must be one of {ANCHORS}")
    try:
        save_config(_zones_path(), [z.model_dump() for z in body.zones], body.anchor)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    return {"status": "ok", "zones": len(body.zones)}
