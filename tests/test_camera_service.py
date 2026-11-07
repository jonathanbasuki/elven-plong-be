import pytest

from plong_elven.app.services.camera_service import CameraService
from plong_elven.app.services.errors import AppError
from plong_elven.core.state.live_state import LiveState


def test_raw_and_annotated_frames_are_separate() -> None:
    svc = CameraService(LiveState())
    svc.put_frame("cam1", b"annotated", raw=False)
    svc.put_frame("cam1", b"raw", raw=True)
    assert svc.latest_frame("cam1", raw=True) == b"raw"
    assert svc.latest_frame("cam1", raw=False) == b"annotated"


def test_missing_frame_is_not_found() -> None:
    with pytest.raises(AppError) as exc:
        CameraService(LiveState()).latest_frame("nope", raw=True)
    assert exc.value.status_code == 404
