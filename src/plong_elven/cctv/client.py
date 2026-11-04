"""Pushes per-zone counts to the API's ingestion endpoint. Fire-and-forget: a
down/slow API must not stall the detection loop, so request errors are logged, not raised.
"""

import logging

import httpx

logger = logging.getLogger(__name__)


def post_counts(
    client: httpx.Client,
    base_url: str,
    api_key: str,
    camera_id: str,
    counts: dict[str, int],
) -> bool:
    try:
        response = client.post(
            f"{base_url}/api/v1/cameras/{camera_id}/detections",
            json={"counts": counts},
            headers={"X-Internal-Api-Key": api_key},
            timeout=2.0,
        )
        response.raise_for_status()
        return True
    except httpx.HTTPError as e:
        logger.warning("failed to post counts to %s: %s", base_url, _error_text(e))
        return False


def post_frame(
    client: httpx.Client,
    base_url: str,
    api_key: str,
    camera_id: str,
    jpeg: bytes,
    raw: bool = False,
) -> bool:
    """raw=True posts the plain camera frame (no overlay) used for the zone editor's
    'capture frame' step; raw=False posts the annotated frame used for the live stream."""
    path = f"/api/v1/cameras/{camera_id}/frames" + ("/raw" if raw else "")
    try:
        response = client.post(
            f"{base_url}{path}",
            content=jpeg,
            headers={"X-Internal-Api-Key": api_key, "Content-Type": "image/jpeg"},
            timeout=2.0,
        )
        response.raise_for_status()
        return True
    except httpx.HTTPError as e:
        logger.warning("failed to post frame to %s: %s", base_url, _error_text(e))
        return False


def _error_text(error: httpx.HTTPError) -> str:
    if isinstance(error, httpx.HTTPStatusError):
        return f"HTTP {error.response.status_code}"
    return str(error)
