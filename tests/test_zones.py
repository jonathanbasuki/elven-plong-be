"""Ported from plong-model/test_counter.py. Skipped unless the cctv group is installed."""

import json
import os
import tempfile

import pytest

np = pytest.importorskip("numpy")
pytest.importorskip("cv2")

from plong_elven.cctv import zones as z


def test_assign_handles_concave_zone_and_edges() -> None:
    # Concave "U" zone on a 100x100 frame: the notch (40..60 x, 0..60 y) is outside.
    u = {
        "zones": [
            {
                "polygon": [
                    [0, 0],
                    [0.4, 0],
                    [0.4, 0.6],
                    [0.6, 0.6],
                    [0.6, 0],
                    [1, 0],
                    [1, 1],
                    [0, 1],
                ]
            }
        ]
    }
    polys = z.to_pixels(u, 100, 100)
    box = lambda cx, bottom: [cx - 5, bottom - 20, cx + 5, bottom]
    boxes = np.array(
        [box(20, 50), box(50, 30), box(50, 80), box(50, 60)], dtype=np.float32
    )

    assert z.assign(boxes, polys, "bottom_center").tolist() == [0, -1, 0, 0]
    assert z.assign(np.array([box(50, 30)], np.float32), polys, "center").tolist() == [
        -1
    ]
    assert z.anchor_points(
        np.array([[0, 0, 10, 40]], np.float32), "center"
    ).tolist() == [[5, 20]]


def test_counts_scale_to_frame_size() -> None:
    cfg = z.load_config(
        os.path.join(os.path.dirname(__file__), "..", "zones.example.json")
    )
    p = z.to_pixels(cfg, 1920, 1080)
    assert p[1][1].tolist() == [1920, 0]
    box = lambda cx, bottom: [cx - 5, bottom - 20, cx + 5, bottom]
    zi = z.assign(
        np.array([box(100, 500), box(1500, 500), box(1600, 900)], np.float32),
        p,
        "bottom_center",
    )
    assert z.counts(zi, 2).tolist() == [1, 2]


def test_config_round_trips_and_rejects_bad_anchor() -> None:
    cfg = z.load_config(
        os.path.join(os.path.dirname(__file__), "..", "zones.example.json")
    )
    with tempfile.TemporaryDirectory() as d:
        f = os.path.join(d, "z.json")
        z.save_config(f, cfg["zones"], "center")
        assert z.load_config(f)["anchor"] == "center"

        with open(f, "w") as fh:
            json.dump({"anchor": "feet", "zones": []}, fh)
        with pytest.raises(ValueError):
            z.load_config(f)
