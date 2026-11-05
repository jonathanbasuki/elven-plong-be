"""CameraService: the latest annotated and raw JPEG frame per camera.

Zones are in ZoneService (database). This service does NOT import fastapi.
"""

from plong_elven.services.errors import AppError
from plong_elven.state.live_state import LiveState


class CameraService:
    """Handles frame storage. No FastAPI imports."""

    def __init__(self, live: LiveState) -> None:
        self._live = live

    def put_frame(self, camera_id: str, jpeg: bytes, raw: bool) -> None:
        """Store an annotated or raw frame for a camera."""
        if raw:
            self._live.raw_frames[camera_id] = jpeg
        else:
            self._live.frames[camera_id] = jpeg

    def latest_frame(self, camera_id: str, raw: bool) -> bytes:
        """Return the latest frame for a camera, raising AppError(404) if none."""
        store = self._live.raw_frames if raw else self._live.frames
        if camera_id not in store:
            raise AppError(
                404, "not_found", f"no frames received yet for {camera_id!r}"
            )
        return store[camera_id]
