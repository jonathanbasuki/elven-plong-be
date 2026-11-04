"""CameraService: frame storage and zone layout operations.

This service does NOT import fastapi — routers handle HTTP concerns.
"""

import os

from plong_elven.common.zones import ANCHORS, save_config
from plong_elven.common.zones import load_config as load_zones_config
from plong_elven.services.errors import AppError
from plong_elven.state.live_state import LiveState


class CameraService:
    """Handles frame storage and zone configuration. No FastAPI imports."""

    def __init__(self, live: LiveState, zones_path: str) -> None:
        self._live = live
        self._zones_path = zones_path

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

    def get_layout(self) -> dict:
        """Load zone layout from disk, returning default if file doesn't exist."""
        if not os.path.exists(self._zones_path):
            return {"anchor": "bottom_center", "zones": []}
        return load_zones_config(self._zones_path)

    def save_layout(self, anchor: str, zones: list[dict]) -> int:
        """Save zone layout to disk. Returns the number of zones saved.

        Raises AppError(422) for invalid anchor or zone data.
        """
        if anchor not in ANCHORS:
            raise AppError(422, "validation_error", f"anchor must be one of {ANCHORS}")
        try:
            save_config(self._zones_path, zones, anchor)
        except ValueError as e:
            raise AppError(422, "validation_error", str(e)) from e
        return len(zones)
