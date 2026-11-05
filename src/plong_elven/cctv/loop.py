"""The camera process (headless people counter): reads a camera/video/RTSP source,
detects people, assigns them to the zones it reads from the API, and POSTs per-zone
counts (and, unless --no-stream, annotated and raw JPEG frames for the dev page) to
the API. No local display window; for an on-screen preview with the same overlay,
use plong-model's run.py instead.

  uv run python -m plong_elven.cctv 1
  uv run python -m plong_elven.cctv rtsp://127.0.0.1:8554/cam1_sub --camera-id cam1
"""

import argparse
import os
import time

import cv2

from . import zones as z
from .client import fetch_zones, post_counts, post_frame
from .detector import Detector
from .overlay import draw
from .uploader import LatestUploader
from .zones_poller import ZonesPoller

# Without this, OpenCV's RTSP (FFmpeg) backend has no read timeout: on a stall (e.g. a
# flaky Wi-Fi link), cap.read() blocks forever instead of failing so the loop can recover.
# TCP transport also tolerates packet loss better than the UDP default on a lossy link.
os.environ.setdefault(
    "OPENCV_FFMPEG_CAPTURE_OPTIONS", "rtsp_transport;tcp|stimeout;5000000"
)
RECONNECT_DELAY = 2.0  # seconds to wait before reopening a dropped source
ZONES_POLL_INTERVAL = 5.0  # seconds between reading the zone list from the API


def open_source(src: str) -> cv2.VideoCapture:
    cap = cv2.VideoCapture(int(src) if src.isdigit() else src)
    cap.set(
        cv2.CAP_PROP_BUFFERSIZE, 1
    )  # always the newest frame, never a growing backlog
    if not cap.isOpened():
        raise SystemExit(f"could not open source {src!r}")
    return cap


def reconnect(src: str) -> cv2.VideoCapture:
    """Keep retrying rather than exiting: a dropped camera/RTSP link should recover on
    its own once the network comes back, not take the whole camera process down with it."""
    while True:
        try:
            return open_source(src)
        except SystemExit:
            print(f"reconnect to {src!r} failed, retrying in {RECONNECT_DELAY:.0f}s")
            time.sleep(RECONNECT_DELAY)


def run(args: argparse.Namespace) -> None:
    poller = ZonesPoller(
        lambda client: fetch_zones(client, args.api_base, args.api_key, args.camera_id),
        ZONES_POLL_INTERVAL,
    )
    poller.start()
    det = Detector(args.model, args.imgsz, args.device)
    cap = open_source(args.source)
    counts_uploader = LatestUploader(
        lambda client, item: post_counts(
            client, args.api_base, args.api_key, item[0], item[1]
        )
    )
    annotated_uploader = LatestUploader(
        lambda client, item: post_frame(
            client, args.api_base, args.api_key, item[0], item[1]
        )
    )
    raw_uploader = LatestUploader(
        lambda client, item: post_frame(
            client, args.api_base, args.api_key, item[0], item[1], raw=True
        )
    )
    counts_uploader.start()
    annotated_uploader.start()
    raw_uploader.start()
    zones, zones_version = [], -1
    polys, size, last_post, last_frame = None, None, 0.0, 0.0

    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                print(f"lost {args.source!r}, reconnecting in {RECONNECT_DELAY:.0f}s")
                cap.release()
                time.sleep(RECONNECT_DELAY)
                cap = reconnect(args.source)
                size = None  # re-derive polygons if resolution changes on reconnect
                continue

            now = time.monotonic()
            if poller.version != zones_version:  # zones edited in the database
                zones, zones_version = poller.zones, poller.version
                size = None  # re-derive polygons against the new zones

            h, w = frame.shape[:2]
            if (w, h) != size:  # read real frame size, never hardcode it
                size, polys = (w, h), z.to_pixels(zones, w, h)
            boxes, _, _ms = det(frame, args.conf)
            zi = z.assign(boxes, polys, args.anchor)
            n = z.counts(zi, len(polys))

            if now - last_post >= args.post_interval:
                counts = {zone["id"]: int(k) for zone, k in zip(zones, n)}
                if args.dry_run:
                    print(counts)
                else:
                    counts_uploader.submit((args.camera_id, counts))
                last_post = now

            if not args.no_stream and now - last_frame >= args.frame_interval:
                labels = [f"{zone['name']}: {k}" for zone, k in zip(zones, n)]
                annotated = draw(
                    frame.copy(), boxes, zi, polys, labels, [f"total: {len(boxes)}"]
                )
                ok_enc, jpeg = cv2.imencode(
                    ".jpg", annotated, [cv2.IMWRITE_JPEG_QUALITY, args.jpeg_quality]
                )
                ok_raw, raw_jpeg = cv2.imencode(
                    ".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, args.jpeg_quality]
                )
                if not args.dry_run:
                    if ok_enc:
                        annotated_uploader.submit((args.camera_id, jpeg.tobytes()))
                    if ok_raw:
                        raw_uploader.submit((args.camera_id, raw_jpeg.tobytes()))
                last_frame = now
    finally:
        cap.release()
        poller.close()
        counts_uploader.close()
        annotated_uploader.close()
        raw_uploader.close()


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("source", nargs="?", default=os.environ.get("RTSP_URL", "0"))
    p.add_argument(
        "--model", default=os.environ.get("YOLO_WEIGHTS", "models/yolo26n.pt")
    )
    p.add_argument("--imgsz", type=int, default=640)
    p.add_argument("--conf", type=float, default=0.25)
    p.add_argument(
        "--anchor",
        choices=z.ANCHORS,
        default=os.environ.get("CAMERA_ANCHOR") or "bottom_center",
        help="which point of a person's box decides their zone",
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
        help="seconds between frames posted for the dev page's stream",
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
