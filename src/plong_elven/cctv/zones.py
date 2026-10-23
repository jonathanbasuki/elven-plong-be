"""Zone config: load/save, normalized-polygon -> pixel conversion, box-to-zone assignment.

Ported from plong-model/counter.py. JSON schema is unchanged (version, anchor, zones[{id,
name, polygon}], polygon points normalized 0..1) so existing zones.json files work as-is,
and the flat {id, name, polygon} shape maps directly onto a future zones DB table.
"""

import json

import cv2
import numpy as np

ANCHORS = ("bottom_center", "center", "top_center")


def load_config(path: str) -> dict:
    with open(path) as f:
        cfg = json.load(f)
    if cfg.get("anchor") not in ANCHORS:
        raise ValueError(f"anchor must be one of {ANCHORS}, got {cfg.get('anchor')!r}")
    for z in cfg["zones"]:
        if len(z["polygon"]) < 3 or not all(
            0 <= v <= 1 for p in z["polygon"] for v in p
        ):
            raise ValueError(f"zone {z['id']}: need >= 3 points, all within 0..1")
    return cfg


def save_config(path: str, zones: list[dict], anchor: str = "bottom_center") -> None:
    with open(path, "w") as f:
        json.dump({"version": 1, "anchor": anchor, "zones": zones}, f, indent=2)


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
