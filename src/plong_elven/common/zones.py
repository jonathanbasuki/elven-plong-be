"""Zone config schema: anchor + zones[{id, name, polygon}], polygon points normalized
0..1. Shared by the api (zone editor endpoints) and cctv (pixel math) packages. No
cv2/numpy dependency on purpose, so importing it doesn't pull in heavy CV packages.
"""

import json

ANCHORS = ("bottom_center", "center", "top_center")


def validate(cfg: dict) -> None:
    if cfg.get("anchor") not in ANCHORS:
        raise ValueError(f"anchor must be one of {ANCHORS}, got {cfg.get('anchor')!r}")
    for z in cfg["zones"]:
        if len(z["polygon"]) < 3 or not all(
            0 <= v <= 1 for p in z["polygon"] for v in p
        ):
            raise ValueError(f"zone {z['id']}: need >= 3 points, all within 0..1")


def load_config(path: str) -> dict:
    with open(path) as f:
        cfg = json.load(f)
    validate(cfg)
    return cfg


def save_config(path: str, zones: list[dict], anchor: str = "bottom_center") -> None:
    cfg = {"version": 1, "anchor": anchor, "zones": zones}
    validate(cfg)
    with open(path, "w") as f:
        json.dump(cfg, f, indent=2)
