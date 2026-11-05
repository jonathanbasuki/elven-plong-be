"""The camera client talks to /api/v1/cameras/{cameraId}/... (Task 2.6)."""

import json

import httpx
import pytest

from plong_elven.cctv.client import fetch_zones, post_counts, post_frame


def _client(seen: list[httpx.Request], status: int = 200) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(status, json={"status": "ok"})

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_post_counts_uses_internal_route() -> None:
    seen: list[httpx.Request] = []
    assert post_counts(_client(seen), "http://api", "k", "cam1", {"z1": 2}) is True
    (req,) = seen
    assert req.method == "POST"
    assert req.url.path == "/api/v1/cameras/cam1/detections"
    assert json.loads(req.content) == {"counts": {"z1": 2}}
    assert req.headers["X-Internal-Api-Key"] == "k"


@pytest.mark.parametrize(
    "raw,path",
    [
        (False, "/api/v1/cameras/cam1/frames"),
        (True, "/api/v1/cameras/cam1/frames/raw"),
    ],
)
def test_post_frame_uses_internal_route(raw, path) -> None:
    seen: list[httpx.Request] = []
    assert post_frame(_client(seen), "http://api", "k", "cam1", b"jpg", raw=raw)
    (req,) = seen
    assert req.url.path == path
    assert req.content == b"jpg"
    assert req.headers["X-Internal-Api-Key"] == "k"
    assert req.headers["Content-Type"] == "image/jpeg"


def test_http_error_returns_false() -> None:
    seen: list[httpx.Request] = []
    assert post_counts(_client(seen, 401), "http://api", "k", "cam1", {}) is False
    assert post_frame(_client(seen, 413), "http://api", "k", "cam1", b"j") is False


def test_fetch_zones_reads_the_camera_route() -> None:
    seen: list[httpx.Request] = []
    zones = [{"id": "z1", "name": "A", "polygon": [[0, 0], [1, 0], [1, 1]]}]

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"zones": zones})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    assert fetch_zones(client, "http://api", "k", "cam1") == zones
    (req,) = seen
    assert (req.method, req.url.path) == ("GET", "/api/v1/cameras/cam1/zones")
    assert req.headers["X-Internal-Api-Key"] == "k"


def test_fetch_zones_failure_returns_none() -> None:
    assert fetch_zones(_client([], 503), "http://api", "k", "cam1") is None
