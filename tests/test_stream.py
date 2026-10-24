from fastapi.testclient import TestClient

from plong_elven.api.main import app

JPEG = b"\xff\xd8fake-jpeg-bytes\xff\xd9"


def test_stream_404_before_any_frame_posted() -> None:
    response = TestClient(app).get("/stream/never-posted")

    assert response.status_code == 404


def test_latest_frame_returns_the_posted_raw_jpeg() -> None:
    client = TestClient(app)
    client.post(
        "/frames/cam2/raw", content=JPEG, headers={"Content-Type": "image/jpeg"}
    )

    response = client.get("/frames/cam2/latest")

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/jpeg"
    assert response.content == JPEG


def test_latest_frame_is_independent_of_annotated_frame() -> None:
    client = TestClient(app)
    annotated = b"\xff\xd8annotated-with-overlay\xff\xd9"
    raw = b"\xff\xd8plain-camera-view\xff\xd9"
    client.post(
        "/frames/cam3", content=annotated, headers={"Content-Type": "image/jpeg"}
    )
    client.post("/frames/cam3/raw", content=raw, headers={"Content-Type": "image/jpeg"})

    response = client.get("/frames/cam3/latest")

    assert response.content == raw
    assert response.content != annotated


def test_latest_frame_404_before_any_frame_posted() -> None:
    response = TestClient(app).get("/frames/never-posted/latest")

    assert response.status_code == 404


def test_stream_serves_posted_frame() -> None:
    client = TestClient(app)
    post = client.post(
        "/frames/cam1", content=JPEG, headers={"Content-Type": "image/jpeg"}
    )
    assert post.status_code == 200

    with client.stream("GET", "/stream/cam1") as response:
        assert response.status_code == 200
        assert "multipart/x-mixed-replace" in response.headers["content-type"]
        chunk = next(response.iter_bytes())
        assert JPEG in chunk


def test_stream_rejects_wrong_key(monkeypatch) -> None:
    monkeypatch.setenv("INTERNAL_API_KEY", "secret")
    client = TestClient(app)
    client.post(
        "/frames/cam1",
        content=JPEG,
        headers={"Content-Type": "image/jpeg", "X-Internal-Api-Key": "secret"},
    )

    response = client.get("/stream/cam1", params={"key": "wrong"})

    assert response.status_code == 401
