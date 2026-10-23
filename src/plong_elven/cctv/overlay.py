"""Debug overlay (zone outlines, person boxes, labels) for the MJPEG stream.
Ported from plong-model/counter.py's draw()/text()/color().
"""

import cv2
import numpy as np

PALETTE = [
    (56, 56, 255),
    (255, 157, 151),
    (31, 112, 255),
    (29, 178, 255),
    (49, 210, 207),
    (10, 249, 72),
    (187, 212, 23),
    (168, 153, 44),
]  # BGR colors, one per zone (cycled if there are more zones than colors)
UNZONED = (200, 200, 200)


def color(i: int) -> tuple[int, int, int]:
    return UNZONED if i < 0 else PALETTE[i % len(PALETTE)]


def text(
    frame: np.ndarray,
    s: str,
    org: tuple[int, int],
    scale: float,
    t: int,
    bg: tuple[int, int, int],
) -> None:
    (tw, th), base = cv2.getTextSize(s, cv2.FONT_HERSHEY_SIMPLEX, scale, t)
    x, y = org
    cv2.rectangle(frame, (x - 2, y - th - 4), (x + tw + 2, y + base), bg, -1)
    cv2.putText(
        frame, s, (x, y), cv2.FONT_HERSHEY_SIMPLEX, scale, (0, 0, 0), t, cv2.LINE_AA
    )


def draw(
    frame: np.ndarray,
    boxes: np.ndarray,
    zone_idx: np.ndarray,
    polys: list[np.ndarray],
    labels: list[str],
    lines: tuple[str, ...] = (),
) -> np.ndarray:
    """Boxes colored by zone, zone outlines + labels, and extra text lines top-left."""
    h, w = frame.shape[:2]
    t = max(1, round(min(w, h) / 400))  # scale to frame size
    s = min(w, h) / 1000
    for (x1, y1, x2, y2), i in zip(boxes.astype(int), zone_idx):
        cv2.rectangle(frame, (x1, y1), (x2, y2), color(i), t)
    for i, (poly, label) in enumerate(zip(polys, labels)):
        cv2.polylines(frame, [poly.astype(np.int32)], True, color(i), t)
        cx, cy = poly.mean(axis=0).astype(int)
        text(frame, label, (cx, cy), s, t, color(i))
    for k, line in enumerate(lines):
        text(
            frame,
            line,
            (int(10 * s + 5), int((k + 1) * 45 * s + 10)),
            s,
            t,
            (255, 255, 255),
        )
    return frame
