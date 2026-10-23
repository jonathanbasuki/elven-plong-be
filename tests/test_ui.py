from fastapi.testclient import TestClient

from plong_elven.api.main import app


def test_ui_page_renders_with_camera_id_substituted() -> None:
    response = TestClient(app).get("/ui/cam1")

    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "cam1" in response.text
    assert "__CAMERA_ID__" not in response.text
