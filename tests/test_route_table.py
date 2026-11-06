# tests/test_route_table.py
from plong_elven.api.main import app

ROUTES = {
    ("POST", "/api/v1/auth/login"),
    ("POST", "/api/v1/auth/logout"),
    ("POST", "/api/v1/auth/refresh"),
    ("GET", "/api/v1/profile"),
    ("PUT", "/api/v1/profile/push-token"),
    ("GET", "/api/v1/tasks"),
    ("GET", "/api/v1/tasks/{taskId}/footage"),
    ("POST", "/api/v1/tasks/broadcast"),
    ("POST", "/api/v1/tasks/{taskId}/accept"),
    ("POST", "/api/v1/tasks/{taskId}/complete"),
    ("POST", "/api/v1/tasks/{taskId}/finish"),
    ("POST", "/api/v1/tasks/{taskId}/cancel"),
    ("GET", "/api/v1/staff/{staffId}"),
    ("POST", "/dev/tickets"),
    ("GET", "/api/v1/zones"),
    ("GET", "/api/v1/zones/{zoneId}"),
    ("GET", "/api/v1/zones/{zoneId}/crowd"),
    ("GET", "/api/v1/zones/{zoneId}/crowd/history"),
    ("GET", "/api/v1/cameras/{cameraId}/zones"),
    ("POST", "/api/v1/cameras/{cameraId}/detections"),
    ("POST", "/api/v1/cameras/{cameraId}/frames"),
    ("POST", "/api/v1/cameras/{cameraId}/frames/raw"),
    ("GET", "/dev/cameras/{cameraId}/layout"),
    ("PUT", "/dev/cameras/{cameraId}/layout"),
    ("GET", "/dev/cameras/{cameraId}/snapshot"),
    ("GET", "/dev/cameras/{cameraId}/stream"),
    ("GET", "/dev/cameras/{cameraId}"),
    ("POST", "/dev/cameras/{cameraId}/detections"),
    ("GET", "/healthz"),
    ("GET", "/readyz"),
}


def test_http_route_table_is_exactly_this() -> None:
    paths = app.openapi()["paths"]
    actual = {(m.upper(), p) for p, ops in paths.items() for m in ops}
    assert actual == ROUTES
