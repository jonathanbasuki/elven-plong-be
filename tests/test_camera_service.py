import pytest

from plong_elven.services.camera_service import CameraService
from plong_elven.services.errors import AppError
from plong_elven.state.live_state import LiveState


def test_raw_and_annotated_frames_are_separate(tmp_path) -> None:
    svc = CameraService(LiveState(), str(tmp_path / "z.json"))
    svc.put_frame("cam1", b"annotated", raw=False)
    svc.put_frame("cam1", b"raw", raw=True)
    assert svc.latest_frame("cam1", raw=True) == b"raw"
    assert svc.latest_frame("cam1", raw=False) == b"annotated"


def test_missing_frame_is_not_found(tmp_path) -> None:
    with pytest.raises(AppError) as exc:
        CameraService(LiveState(), str(tmp_path / "z.json")).latest_frame(
            "nope", raw=True
        )
    assert exc.value.status_code == 404


def test_bad_anchor_is_validation_error(tmp_path) -> None:
    with pytest.raises(AppError) as exc:
        CameraService(LiveState(), str(tmp_path / "z.json")).save_layout("feet", [])
    assert exc.value.status_code == 422
