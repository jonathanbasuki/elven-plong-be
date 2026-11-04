# tests/test_route_table.py
from plong_elven.api.main import app

STAGE1_ROUTES = {
    ("POST", "/api/v1/auth/login"),
    ("POST", "/api/v1/auth/logout"),
    ("POST", "/api/v1/auth/refresh"),
    ("GET", "/api/v1/me"),
    ("PUT", "/api/v1/me/push-token"),
    ("GET", "/api/v1/broadcasts"),
    ("POST", "/api/v1/broadcasts"),
    ("POST", "/api/v1/broadcasts/{broadcastId}/accept"),
    ("POST", "/api/v1/broadcasts/{broadcastId}/complete"),
    ("POST", "/api/v1/broadcasts/{broadcastId}/finish"),
    ("GET", "/api/v1/staff/{staffId}"),
    ("POST", "/api/v1/tickets"),
    ("GET", "/api/v1/zones"),
    ("GET", "/api/v1/zones/{zoneId}"),
    ("GET", "/api/v1/zones/{zoneId}/crowd"),
    ("GET", "/api/v1/zones/{zoneId}/crowd/history"),
    ("POST", "/internal/v1/cameras/{cameraId}/detections"),
    ("POST", "/internal/v1/cameras/{cameraId}/frames"),
    ("POST", "/internal/v1/cameras/{cameraId}/frames/raw"),
    ("GET", "/api/v1/cameras/{cameraId}/layout"),
    ("PUT", "/api/v1/cameras/{cameraId}/layout"),
    ("GET", "/api/v1/cameras/{cameraId}/snapshot"),
    ("GET", "/api/v1/cameras/{cameraId}/stream"),
    ("GET", "/dev/cameras/{cameraId}"),
    ("POST", "/dev/cameras/{cameraId}/detections"),
    ("GET", "/healthz"),
    ("GET", "/readyz"),
}


def test_http_route_table_is_unchanged() -> None:
    paths = app.openapi()["paths"]
    actual = {(m.upper(), p) for p, ops in paths.items() for m in ops}
    assert actual == STAGE1_ROUTES
