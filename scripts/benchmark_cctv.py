import argparse
import csv
import json
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import psutil


def parse_profile(value: str) -> tuple[str, float, int]:
    try:
        name, interval, quality = value.split(":")
        return name, float(interval), int(quality)
    except ValueError as error:
        raise argparse.ArgumentTypeError(
            "profile must use NAME:FRAME_INTERVAL:JPEG_QUALITY"
        ) from error


def find_worker_process(launcher: psutil.Process) -> psutil.Process:
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        candidates = [launcher, *launcher.children(recursive=True)]
        candidates.extend(
            process for process in psutil.process_iter() if process.pid != launcher.pid
        )
        for process in candidates:
            if process.pid == launcher.pid:
                continue
            try:
                argv = process.cmdline()
            except psutil.Error:
                continue
            if "-m" in argv:
                module_index = argv.index("-m")
                if (
                    module_index + 1 < len(argv)
                    and argv[module_index + 1] == "plong_elven.cctv"
                ):
                    return process
        time.sleep(0.2)
    raise RuntimeError(
        "could not find the plong_elven.cctv process; "
        "check the worker log and process command line"
    )


def run_profile(
    source: str,
    zones: str,
    camera_id: str,
    model: str | None,
    device: str | None,
    duration: float,
    profile: tuple[str, float, int],
    output_dir: Path,
) -> None:
    name, frame_interval, jpeg_quality = profile
    log_path = output_dir / f"{name}.worker.log"
    samples_path = output_dir / f"{name}.csv"
    command = [
        "uv",
        "run",
        "--env-file",
        ".env",
        "python",
        "-m",
        "plong_elven.cctv",
        source,
        "--camera-id",
        camera_id,
        "--zones",
        zones,
        "--frame-interval",
        str(frame_interval),
        "--jpeg-quality",
        str(jpeg_quality),
    ]
    if model:
        command.extend(("--model", model))
    if device:
        command.extend(("--device", device))

    print(f"[{name}] starting worker for {duration:.0f}s")
    with log_path.open("w") as log_file:
        process = subprocess.Popen(
            command,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            text=True,
        )
        launcher = psutil.Process(process.pid)
        time.sleep(1)
        worker = find_worker_process(launcher)
        print(f"[{name}] measuring PID {worker.pid}: {' '.join(worker.cmdline())}")
        worker.cpu_percent(None)
        started_at = time.monotonic()
        rows: list[dict[str, float | str]] = []

        try:
            while time.monotonic() - started_at < duration:
                time.sleep(1)
                if process.poll() is not None:
                    raise RuntimeError(
                        f"worker exited early with code {process.returncode}; "
                        f"see {log_path}"
                    )
                if not worker.is_running():
                    raise RuntimeError(
                        f"worker exited early with code {process.returncode}; "
                        f"see {log_path}"
                    )
                with worker.oneshot():
                    rows.append(
                        {
                            "elapsed_seconds": round(time.monotonic() - started_at, 1),
                            "cpu_percent": round(worker.cpu_percent(None), 2),
                            "rss_mb": round(worker.memory_info().rss / 1024**2, 2),
                        }
                    )
        finally:
            for child in launcher.children(recursive=True):
                child.terminate()
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()

    with samples_path.open("w", newline="") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)

    summary = {
        "profile": name,
        "frame_interval": frame_interval,
        "jpeg_quality": jpeg_quality,
        "duration_seconds": duration,
        "average_cpu_percent": round(
            sum(float(row["cpu_percent"]) for row in rows) / len(rows), 2
        ),
        "peak_cpu_percent": max(float(row["cpu_percent"]) for row in rows),
        "average_rss_mb": round(
            sum(float(row["rss_mb"]) for row in rows) / len(rows), 2
        ),
        "peak_rss_mb": max(float(row["rss_mb"]) for row in rows),
        "log": str(log_path),
        "samples": str(samples_path),
    }
    (output_dir / f"{name}.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(
        f"[{name}] average CPU {summary['average_cpu_percent']}%, "
        f"peak RSS {summary['peak_rss_mb']} MB"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Benchmark CCTV worker profiles")
    parser.add_argument("source", help="camera index, video path, or RTSP URL")
    parser.add_argument("--zones", default="zones.example.json")
    parser.add_argument("--camera-id", default="default")
    parser.add_argument("--model")
    parser.add_argument("--device")
    parser.add_argument("--duration", type=float, default=60)
    parser.add_argument(
        "--profile",
        action="append",
        type=parse_profile,
        default=[],
        help="NAME:FRAME_INTERVAL:JPEG_QUALITY; may be repeated",
    )
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()

    profiles = args.profile or [
        ("baseline", 0.2, 80),
        ("balanced", 0.5, 70),
        ("low_bandwidth", 1.0, 60),
    ]
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    output_dir = args.output_dir or Path("data/benchmarks") / timestamp
    output_dir.mkdir(parents=True, exist_ok=True)
    print(f"Results will be written to {output_dir}")

    try:
        for profile in profiles:
            run_profile(
                args.source,
                args.zones,
                args.camera_id,
                args.model,
                args.device,
                args.duration,
                profile,
                output_dir,
            )
    except KeyboardInterrupt:
        print("Benchmark interrupted", file=sys.stderr)
        raise SystemExit(130) from None


if __name__ == "__main__":
    main()
