"""Camera admin routes: zone layout, raw snapshot and the live MJPEG stream.

Layout and snapshot need a bearer access token with role admin. The stream is
viewed from a browser <img>, which cannot send headers, so it takes only a
short-lived `?ticket=` scoped to `stream:<cameraId>` (§6.4). The internal key is
never accepted here.

# ponytail: frames live in in-memory dicts and every viewer re-reads the latest
# one. Fine for one API process; swap for Redis if this ever runs behind
# multiple uvicorn workers.
"""

import asyncio
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, Path, Query, Response
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from plong_elven.api.deps import get_camera_service, get_session_factory, require_role
from plong_elven.db.models import User
from plong_elven.schemas.common import CAMERA_ID_PATTERN
from plong_elven.schemas.layout import ZonesIn
from plong_elven.services.auth_service import AuthService
from plong_elven.services.camera_service import CameraService
from plong_elven.services.errors import AppError
from plong_elven.state.live_state import LiveState, get_live_state

router = APIRouter(prefix="/api/v1/cameras/{cameraId}", tags=["cameras"])
BOUNDARY = b"frame"
STREAM_INTERVAL = 0.2  # seconds between re-sent frames to each viewer


async def _stream_ticket_user(
    cameraId: str = Path(pattern=CAMERA_ID_PATTERN),
    ticket: str | None = Query(default=None),
    factory: async_sessionmaker[AsyncSession] = Depends(get_session_factory),  # noqa: B008
) -> User:
    """Authenticate the stream by a `stream:<cameraId>` ticket.

    The session is opened and closed here, so an open stream does not hold a
    pool connection for its whole life (same reason as P5).
    """
    if not ticket:
        raise AppError(401, "unauthenticated", "Missing ticket")
    async with factory() as session:
        return await AuthService(session).verify_ticket(ticket, f"stream:{cameraId}")


@router.get("/layout")
async def get_layout(
    cameraId: str = Path(pattern=CAMERA_ID_PATTERN),
    _: User = Depends(require_role("admin")),  # noqa: B008
    svc: CameraService = Depends(get_camera_service),  # noqa: B008
) -> dict:
    """The shared zone layout (one file for all cameras today)."""
    return svc.get_layout()


@router.put("/layout")
async def put_layout(
    body: ZonesIn,
    cameraId: str = Path(pattern=CAMERA_ID_PATTERN),
    _: User = Depends(require_role("admin")),  # noqa: B008
    svc: CameraService = Depends(get_camera_service),  # noqa: B008
) -> dict:
    count = svc.save_layout(body.anchor, [z.model_dump() for z in body.zones])
    return {"status": "ok", "zones": count}


@router.get("/snapshot")
async def get_snapshot(
    cameraId: str = Path(pattern=CAMERA_ID_PATTERN),
    _: User = Depends(require_role("admin")),  # noqa: B008
    svc: CameraService = Depends(get_camera_service),  # noqa: B008
) -> Response:
    """The latest unannotated frame, for the zone editor's 'capture frame' step."""
    return Response(
        content=svc.latest_frame(cameraId, raw=True), media_type="image/jpeg"
    )


@router.get("/stream")
async def get_stream(
    cameraId: str = Path(pattern=CAMERA_ID_PATTERN),
    _: User = Depends(_stream_ticket_user),  # noqa: B008
    live: LiveState = Depends(get_live_state),  # noqa: B008
    svc: CameraService = Depends(get_camera_service),  # noqa: B008
) -> StreamingResponse:
    """Annotated frames as multipart/x-mixed-replace for a browser <img>."""
    svc.latest_frame(cameraId, raw=False)  # 404 before the first frame

    async def frames() -> AsyncIterator[bytes]:
        while True:
            jpeg = live.frames[cameraId]
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
