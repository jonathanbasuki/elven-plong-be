import json
import os
import tempfile

from fastapi.testclient import TestClient

from plong_elven.api.main import app


def test_get_zones_defaults_when_file_missing(monkeypatch) -> None:
    monkeypatch.setenv("ZONES_PATH", "/tmp/does-not-exist-plong-elven.json")

    response = TestClient(app).get("/zones/cam1")

    assert response.status_code == 200
    assert response.json() == {"anchor": "bottom_center", "zones": []}


def test_put_then_get_zones_round_trips(monkeypatch) -> None:
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "zones.json")
        monkeypatch.setenv("ZONES_PATH", path)
        client = TestClient(app)
        body = {
            "anchor": "center",
            "zones": [
                {"id": "z1", "name": "Left", "polygon": [[0, 0], [0.5, 0], [0.5, 1]]}
            ],
        }

        put = client.put("/zones/cam1", json=body)
        assert put.status_code == 200
        assert put.json() == {"status": "ok", "zones": 1}

        get = client.get("/zones/cam1")
        assert get.json()["anchor"] == "center"
        assert get.json()["zones"][0]["id"] == "z1"
        with open(path) as f:
            assert json.load(f)["zones"][0]["name"] == "Left"


def test_put_zones_rejects_bad_polygon(monkeypatch) -> None:
    with tempfile.TemporaryDirectory() as d:
        monkeypatch.setenv("ZONES_PATH", os.path.join(d, "zones.json"))
        body = {
            "anchor": "bottom_center",
            "zones": [{"id": "z1", "name": "Bad", "polygon": [[0, 0], [1, 1]]}],
        }

        response = TestClient(app).put("/zones/cam1", json=body)

        assert response.status_code == 422


def test_put_zones_requires_internal_api_key(monkeypatch) -> None:
    monkeypatch.setenv("INTERNAL_API_KEY", "secret")
    with tempfile.TemporaryDirectory() as d:
        monkeypatch.setenv("ZONES_PATH", os.path.join(d, "zones.json"))
        body = {"anchor": "bottom_center", "zones": []}

        response = TestClient(app).put("/zones/cam1", json=body)

        assert response.status_code == 401
