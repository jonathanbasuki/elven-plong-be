"""Crowd: GET /api/v1/zones/{zoneId}/crowd[/history], WS /api/v1/crowd/live, ingest.

Counts reach this module from the camera route (api/routers/cameras.py). They
update in-memory live state, fan out to every connected WebSocket, and schedule
history and alert work in a background task with its own session.
"""

from datetime import UTC, datetime

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    Query,
    WebSocket,
    WebSocketDisconnect,
)
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from plong_elven.api.deps import (
    get_crowd_service,
    get_current_user,
    get_session_factory,
)
from plong_elven.db.models import User
from plong_elven.schemas.zones import CrowdOut, HistoryBucketOut
from plong_elven.services.auth_service import AuthService
from plong_elven.services.crowd_service import (
    CrowdService,
    process_counts_in_new_session,
)
from plong_elven.services.errors import AppError
from plong_elven.services.internal.alert_service import AlertService
from plong_elven.state.live_state import LiveState

router = APIRouter(tags=["crowd"])
_clients: set[WebSocket] = set()

CROWD_LIVE_SCOPE = "crowd:live"


@router.get("/api/v1/zones/{zoneId}/crowd")
async def get_zone_crowd(
    zoneId: str,
    svc: CrowdService = Depends(get_crowd_service),  # noqa: B008
    _user: User = Depends(get_current_user),  # noqa: B008
) -> CrowdOut:
    """Live count and status; observedAt is null before the first detection."""
    return await svc.crowd(zoneId)


@router.get("/api/v1/zones/{zoneId}/crowd/history")
async def get_zone_crowd_history(
    zoneId: str,
    limit: int = Query(12, gt=0, le=288),
    svc: CrowdService = Depends(get_crowd_service),  # noqa: B008
    _user: User = Depends(get_current_user),  # noqa: B008
) -> list[HistoryBucketOut]:
    """Most recent 5-minute avg/peak buckets first. Default limit (12) is the last
    hour; max (288) is the last 24 hours."""
    return await svc.history(zoneId, limit)


async def publish_counts(camera_id: str, counts: dict[str, int]) -> None:
    """Send {"cameraId", "counts"} to every connected client; drop dead sockets."""
    message = {"cameraId": camera_id, "counts": counts}
    dead = set()
    for ws in list(_clients):
        try:
            await ws.send_json(message)
        except (WebSocketDisconnect, RuntimeError):
            dead.add(ws)
    _clients.difference_update(dead)


async def ingest_counts(
    camera_id: str,
    counts: dict[str, int],
    background_tasks: BackgroundTasks,
    live: LiveState,
    factory: async_sessionmaker[AsyncSession],
    alerts: AlertService,
) -> None:
    """Record live counts, publish them, then schedule history and alerts.

    The background task opens its own session from the factory; the request
    session is never handed to it.
    """
    CrowdService(None, live).record_live(counts, datetime.now(UTC))  # type: ignore[arg-type]
    await publish_counts(camera_id, counts)
    background_tasks.add_task(
        process_counts_in_new_session, factory, live, alerts, counts
    )


async def _authenticate(
    websocket: WebSocket, factory: async_sessionmaker[AsyncSession]
) -> None:
    """Bearer access token (iOS) or ?ticket= with scope crowd:live (browser).

    P5: the session is closed before the socket is accepted, so an open socket
    does not hold a pool connection. Raises AppError(401) on any failure.
    """
    authorization = websocket.headers.get("authorization", "")
    scheme, _, token = authorization.partition(" ")
    async with factory() as session:
        auth = AuthService(session)
        if scheme.lower() == "bearer" and token:
            await auth.user_from_access_token(token)
            return
        ticket = websocket.query_params.get("ticket")
        if not ticket:
            raise AppError(401, "unauthenticated", "Not authenticated")
        await auth.verify_ticket(ticket, CROWD_LIVE_SCOPE)


@router.websocket("/api/v1/crowd/live")
async def crowd_live(
    websocket: WebSocket,
    factory: async_sessionmaker[AsyncSession] = Depends(get_session_factory),  # noqa: B008
) -> None:
    try:
        await _authenticate(websocket, factory)
    except AppError:
        await websocket.close(code=1008)
        return

    await websocket.accept()
    _clients.add(websocket)
    try:
        while True:
            await websocket.receive_text()  # keep open; payload is ignored
    except WebSocketDisconnect:
        pass
    finally:
        _clients.discard(websocket)
