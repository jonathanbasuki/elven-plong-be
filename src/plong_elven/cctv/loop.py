"""Headless people counter: reads a camera/video/RTSP source, detects people, assigns them
to zones, and POSTs per-zone counts (and, unless --no-stream, annotated JPEG frames for
GET /stream/{camera_id}) to the API. No local display window; for an on-screen preview
with the same overlay, use plong-model's run.py instead.

  uv run python -m plong_elven.cctv 1 --zones zones.json
  uv run python -m plong_elven.cctv rtsp://127.0.0.1:8554/cam1_sub --camera-id cam1
"""

import argparse
import os
import time

import cv2
import httpx

from . import zones as z
from .client import post_counts, post_frame
from .detector import Detector
from .overlay import draw


def open_source(src: str) -> cv2.VideoCapture:
    cap = cv2.VideoCapture(int(src) if src.isdigit() else src)
    if not cap.isOpened():
        raise SystemExit(f"could not open source {src!r}")
    return cap


def run(args: argparse.Namespace) -> None:
    cfg = (
        z.load_config(args.zones)
        if os.path.exists(args.zones)
        else {"anchor": "bottom_center", "zones": []}
    )
    anchor = args.anchor or cfg["anchor"]
    det = Detector(args.model, args.imgsz, args.device)
    cap = open_source(args.source)
    client = httpx.Client()
    polys, size, last_post, last_frame = None, None, 0.0, 0.0

    while True:
        ok, frame = cap.read()
        if not ok:
            break
        h, w = frame.shape[:2]
        if (w, h) != size:  # read real frame size, never hardcode it
            size, polys = (w, h), z.to_pixels(cfg, w, h)
        boxes, _, _ms = det(frame, args.conf)
        zi = z.assign(boxes, polys, anchor)
        n = z.counts(zi, len(polys))

        now = time.monotonic()
        if now - last_post >= args.post_interval:
            counts = {zone["id"]: int(k) for zone, k in zip(cfg["zones"], n)}
            if args.dry_run:
                print(counts)
            else:
                post_counts(client, args.api_base, args.api_key, args.camera_id, counts)
            last_post = now

        if not args.no_stream and now - last_frame >= args.frame_interval:
            labels = [f"{zone['name']}: {k}" for zone, k in zip(cfg["zones"], n)]
            annotated = draw(
                frame.copy(), boxes, zi, polys, labels, [f"total: {len(boxes)}"]
            )
            ok_enc, jpeg = cv2.imencode(
                ".jpg", annotated, [cv2.IMWRITE_JPEG_QUALITY, args.jpeg_quality]
            )
            if ok_enc and not args.dry_run:
                post_frame(
                    client, args.api_base, args.api_key, args.camera_id, jpeg.tobytes()
                )
            last_frame = now

    cap.release()
    client.close()


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("source", nargs="?", default=os.environ.get("RTSP_URL", "0"))
    p.add_argument("--zones", default="zones.json")
    p.add_argument(
        "--model", default=os.environ.get("YOLO_WEIGHTS", "models/yolo26n.pt")
    )
    p.add_argument("--imgsz", type=int, default=640)
    p.add_argument("--conf", type=float, default=0.25)
    p.add_argument(
        "--anchor", choices=z.ANCHORS, help="override the anchor in the zones file"
    )
    p.add_argument(
        "--device", default=os.environ.get("YOLO_DEVICE"), help="e.g. mps or cpu"
    )
    p.add_argument("--camera-id", default=os.environ.get("CAMERA_ID") or "default")
    p.add_argument(
        "--api-base", default=os.environ.get("API_BASE_URL", "http://127.0.0.1:8080")
    )
    p.add_argument("--api-key", default=os.environ.get("INTERNAL_API_KEY", ""))
    p.add_argument(
        "--post-interval",
        type=float,
        default=1.0,
        help="seconds between POSTs to the API",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        default=os.environ.get("DRY_RUN") == "1",
        help="print counts instead of posting them",
    )
    p.add_argument(
        "--frame-interval",
        type=float,
        default=0.2,
        help="seconds between frames posted for GET /stream/{camera_id}",
    )
    p.add_argument("--jpeg-quality", type=int, default=80)
    p.add_argument(
        "--no-stream",
        action="store_true",
        help="don't post frames for the MJPEG stream",
    )
    run(p.parse_args())


if __name__ == "__main__":
    main()
