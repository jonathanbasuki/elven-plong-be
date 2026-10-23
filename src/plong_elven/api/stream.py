"""Ingests the latest annotated JPEG frame per camera from the cctv worker and re-serves it
as an MJPEG stream to any number of viewers. In-memory only, single-process assumption.

# ponytail: one global dict, no pub/sub. Fine for one API process; swap for Redis if this
# ever runs behind multiple uvicorn workers.
"""

import asyncio
from collections.abc import AsyncIterator

from fastapi import APIRouter, Header, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from plong_elven.api.security import check_internal_key

router = APIRouter(tags=["stream"])
_frames: dict[str, bytes] = {}
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
