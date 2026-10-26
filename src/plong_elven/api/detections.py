"""Ingests per-zone counts from the cctv worker and broadcasts them live over WebSocket.
No persistence yet: this is in-memory fan-out only, until DB/migrations land.
"""

from datetime import UTC, datetime

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    Header,
    WebSocket,
    WebSocketDisconnect,
)
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from plong_elven.api.apns import send_push
from plong_elven.api.security import check_internal_key
from plong_elven.common.zone_status import classify
from plong_elven.db.models import Role, User, Zone
from plong_elven.db.session import get_db

router = APIRouter(tags=["detections"])
_clients: set[WebSocket] = set()
latest_counts: dict[str, int] = {}
latest_count_observed_at: dict[str, datetime] = {}
_last_status: dict[str, str] = {}

_ALERT_MESSAGES = {
    "warning": (
        "Crowd warning",
        "{zone} is getting busy ({count} people detected).",
    ),
    "crowded": (
        "Zone crowded — act now",
        "{zone} is crowded ({count} people). Dispatch staff immediately.",
    ),
}


class Detection(BaseModel):
    camera_id: str
    counts: dict[str, int]


async def _notify_managers_on_status_change(
    session: AsyncSession, counts: dict[str, int]
) -> None:
    """Pushes ops_manager devices only when a zone newly crosses into warning or
    crowded — not on every detection post, which would spam a push every second
    while the zone stays busy."""
    for zone_id, count in counts.items():
        zone = await session.get(Zone, zone_id)
        if zone is None:
            continue
        status = classify(count, zone.warning_threshold, zone.crowded_threshold)
        previous = _last_status.get(zone_id)
        _last_status[zone_id] = status
        if status == previous or status not in _ALERT_MESSAGES:
            continue

        manager_tokens = await session.scalars(
            select(User.push_token)
            .join(Role, Role.id == User.role_id)
            .where(Role.name == "ops_manager", User.push_token.is_not(None))
        )
        title, body_template = _ALERT_MESSAGES[status]
        await send_push(
            list(manager_tokens.all()),
            title,
            body_template.format(zone=zone.name, count=count),
        )


@router.post("/detections")
async def post_detection(
    detection: Detection,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_db),  # noqa: B008
    x_internal_api_key: str | None = Header(default=None),
) -> dict:
    check_internal_key(x_internal_api_key)
    latest_counts.update(detection.counts)
    observed_at = datetime.now(UTC)
    for zone_id in detection.counts:
        latest_count_observed_at[zone_id] = observed_at
    background_tasks.add_task(
        _notify_managers_on_status_change, session, detection.counts
    )
    dead = set()
    for ws in _clients:
        try:
            await ws.send_json(detection.model_dump())
        except (WebSocketDisconnect, RuntimeError):
            dead.add(ws)
    _clients.difference_update(dead)
    return {"status": "ok"}


@router.websocket("/ws/counts")
async def ws_counts(websocket: WebSocket) -> None:
    await websocket.accept()
    _clients.add(websocket)
    try:
        while True:
            await (
                websocket.receive_text()
            )  # keep the connection open; payload is ignored
    except WebSocketDisconnect:
        pass
    finally:
        _clients.discard(websocket)
