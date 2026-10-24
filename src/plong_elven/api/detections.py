"""Ingests per-zone counts from the cctv worker and broadcasts them live over WebSocket.
No persistence yet: this is in-memory fan-out only, until DB/migrations land.
"""

from fastapi import APIRouter, Header, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from plong_elven.api.security import check_internal_key

router = APIRouter(tags=["detections"])
_clients: set[WebSocket] = set()


class Detection(BaseModel):
    camera_id: str
    counts: dict[str, int]


@router.post("/detections")
async def post_detection(
    detection: Detection, x_internal_api_key: str | None = Header(default=None)
) -> dict:
    check_internal_key(x_internal_api_key)
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
