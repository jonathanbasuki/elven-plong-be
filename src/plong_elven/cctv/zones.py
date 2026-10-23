"""Zone pixel math: normalized-polygon -> pixel conversion, box-to-zone assignment.

Ported from plong-model/counter.py. Schema I/O (load/save/validate) lives in
plong_elven.common.zones, which has no cv2/numpy dependency; re-exported below so
existing call sites (z.load_config, z.ANCHORS, ...) are unaffected.
"""

import cv2
import numpy as np

from plong_elven.common.zones import ANCHORS, load_config, save_config  # noqa: F401


def to_pixels(cfg: dict, w: int, h: int) -> list[np.ndarray]:
    """Normalized (top-left origin) polygons -> float32 pixel polygons for this frame size."""
    return [(np.array(z["polygon"]) * (w, h)).astype(np.float32) for z in cfg["zones"]]


def anchor_points(boxes: np.ndarray, anchor: str) -> np.ndarray:
    x = (boxes[:, 0] + boxes[:, 2]) / 2
    if anchor == "bottom_center":
        y = boxes[:, 3]
    elif anchor == "top_center":
        y = boxes[:, 1]
    else:
        y = (boxes[:, 1] + boxes[:, 3]) / 2
    return np.stack([x, y], axis=1)


def assign(boxes: np.ndarray, polys: list[np.ndarray], anchor: str) -> np.ndarray:
    """Zone index per box, -1 if outside all zones. Points on an edge count as inside.
    Zones never overlap (user decision), so the first match is the only match."""
    out = np.full(len(boxes), -1)
    for b, (x, y) in enumerate(anchor_points(boxes, anchor)):
        for i, poly in enumerate(polys):
            if cv2.pointPolygonTest(poly, (float(x), float(y)), False) >= 0:
                out[b] = i
                break
    return out


def counts(zone_idx: np.ndarray, n_zones: int) -> np.ndarray:
    return np.bincount(zone_idx[zone_idx >= 0], minlength=n_zones)
