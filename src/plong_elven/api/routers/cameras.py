"""Camera routes: the camera process reads its zones and posts counts and JPEG
frames here.

Every route needs the X-Internal-Api-Key header (a blank configured key rejects
every call, P2) and a cameraId matching CAMERA_ID_PATTERN (§6.7). Frame bodies
are capped at 5 MB (§6.4); count bounds live in DetectionIn. A bearer token is
never accepted here. The admin camera tools (layout, snapshot, stream) live in
the dev-only router, `dev.py`.
"""

from fastapi import APIRouter, BackgroundTasks, Depends, Path, Request
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from plong_elven.api.deps import (
    get_alert_service,
    get_camera_service,
    get_session_factory,
    get_zone_service,
    require_internal_key,
)
from plong_elven.api.routers.crowd import ingest_counts
from plong_elven.schemas.common import CAMERA_ID_PATTERN
from plong_elven.schemas.detections import DetectionIn
from plong_elven.schemas.layout import CameraZonesOut
from plong_elven.services.camera_service import CameraService
from plong_elven.services.errors import AppError
from plong_elven.services.internal.alert_service import AlertService
from plong_elven.services.zone_service import ZoneService
from plong_elven.state.live_state import LiveState, get_live_state

MAX_FRAME_BYTES = 5 * 1024 * 1024

router = APIRouter(
    prefix="/api/v1/cameras/{cameraId}",
    tags=["cameras"],
    dependencies=[Depends(require_internal_key)],
)


async def _read_capped_body(
    request: Request, max_bytes: int = MAX_FRAME_BYTES
) -> bytes:
    """Read the request body, refusing anything over max_bytes with a 413.

    The content-length header is checked first; the running total is checked
    again while streaming, so a missing or lying header cannot get past the cap.
    """

    def too_large() -> AppError:
        return AppError(
            413, "payload_too_large", f"frame body must be at most {max_bytes} bytes"
        )

    declared = request.headers.get("content-length")
    if declared is not None and declared.isdigit() and int(declared) > max_bytes:
        raise too_large()

    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > max_bytes:
            raise too_large()
        chunks.append(chunk)
    return b"".join(chunks)


@router.get("/zones")
async def get_camera_zones(
    cameraId: str = Path(pattern=CAMERA_ID_PATTERN),
    svc: ZoneService = Depends(get_zone_service),  # noqa: B008
) -> CameraZonesOut:
    """Zone polygons (camera frame, normalized 0..1). The camera polls this to
    pick up zone edits. One shared zone set for every camera today."""
    return CameraZonesOut(zones=await svc.camera_zones())


@router.post("/detections")
async def post_detections(
    detection: DetectionIn,
    background_tasks: BackgroundTasks,
    cameraId: str = Path(pattern=CAMERA_ID_PATTERN),
    live: LiveState = Depends(get_live_state),  # noqa: B008
    factory: async_sessionmaker[AsyncSession] = Depends(get_session_factory),  # noqa: B008
    alerts: AlertService = Depends(get_alert_service),  # noqa: B008
) -> dict:
    await ingest_counts(
        cameraId, detection.counts, background_tasks, live, factory, alerts
    )
    return {"status": "ok"}


@router.post("/frames")
async def post_frame(
    request: Request,
    cameraId: str = Path(pattern=CAMERA_ID_PATTERN),
    svc: CameraService = Depends(get_camera_service),  # noqa: B008
) -> dict:
    """Annotated frame (zone outlines, boxes, labels) for the live stream."""
    svc.put_frame(cameraId, await _read_capped_body(request), raw=False)
    return {"status": "ok"}


@router.post("/frames/raw")
async def post_raw_frame(
    request: Request,
    cameraId: str = Path(pattern=CAMERA_ID_PATTERN),
    svc: CameraService = Depends(get_camera_service),  # noqa: B008
) -> dict:
    """Plain camera frame for the zone editor's 'capture frame' step."""
    svc.put_frame(cameraId, await _read_capped_body(request), raw=True)
    return {"status": "ok"}
