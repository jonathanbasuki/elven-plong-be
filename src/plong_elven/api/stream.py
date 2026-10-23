"""Ingests JPEG frames from the cctv worker and re-serves them to any number of viewers.
Two separate stores: annotated frames (zone outlines/boxes/labels burned in, for the live
MJPEG stream) and raw frames (plain camera view, for the zone editor's 'capture frame'
step — drawing new zones on top of old boxes/labels would be confusing). In-memory only,
single-process assumption.

# ponytail: two global dicts, no pub/sub. Fine for one API process; swap for Redis if this
# ever runs behind multiple uvicorn workers.
"""

import asyncio
from collections.abc import AsyncIterator

from fastapi import APIRouter, Header, HTTPException, Query, Request, Response
from fastapi.responses import StreamingResponse

from plong_elven.api.security import check_internal_key

router = APIRouter(tags=["stream"])
_frames: dict[str, bytes] = {}  # annotated, for the live stream
_raw_frames: dict[str, bytes] = {}  # unannotated, for the zone editor's capture step
BOUNDARY = b"frame"
STREAM_INTERVAL = 0.2  # seconds between re-sent frames to each viewer


@router.post("/frames/{camera_id}")
async def post_frame(
    camera_id: str,
    request: Request,
    x_internal_api_key: str | None = Header(default=None),
) -> dict:
    check_internal_key(x_internal_api_key)
    _frames[camera_id] = await request.body()
    return {"status": "ok"}


@router.post("/frames/{camera_id}/raw")
async def post_raw_frame(
    camera_id: str,
    request: Request,
    x_internal_api_key: str | None = Header(default=None),
) -> dict:
    check_internal_key(x_internal_api_key)
    _raw_frames[camera_id] = await request.body()
    return {"status": "ok"}


@router.get("/frames/{camera_id}/latest")
async def get_latest_frame(
    camera_id: str, key: str | None = Query(default=None)
) -> Response:
    """The latest unannotated frame, for the zone editor's 'capture frame' step (vs. the
    live, annotated multipart stream below)."""
    check_internal_key(key)
    if camera_id not in _raw_frames:
        raise HTTPException(
            status_code=404, detail=f"no frames received yet for {camera_id!r}"
        )
    return Response(content=_raw_frames[camera_id], media_type="image/jpeg")


@router.get("/stream/{camera_id}")
async def get_stream(
    camera_id: str, key: str | None = Query(default=None)
) -> StreamingResponse:
    # A browser <img> tag can't send custom headers, so the viewing key is a query param
    # here while every other endpoint uses the X-Internal-Api-Key header.
    check_internal_key(key)
    if camera_id not in _frames:
        raise HTTPException(
            status_code=404, detail=f"no frames received yet for {camera_id!r}"
        )

    async def frames() -> AsyncIterator[bytes]:
        while True:
            jpeg = _frames[camera_id]
            yield (
                b"--" + BOUNDARY + b"\r\n"
                b"Content-Type: image/jpeg\r\n"
                b"Content-Length: "
                + str(len(jpeg)).encode()
                + b"\r\n\r\n"
                + jpeg
                + b"\r\n"
            )
            await asyncio.sleep(STREAM_INTERVAL)

    return StreamingResponse(
        frames(), media_type=f"multipart/x-mixed-replace; boundary={BOUNDARY.decode()}"
    )
