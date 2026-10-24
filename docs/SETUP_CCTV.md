# CCTV Integration Setup

This guide explains how to run the CCTV worker locally and view its annotated
MJPEG stream through the Plong Elven API.

The local flow is:

```text
CCTV -> CCTV worker -> Plong Elven API -> browser
```

The worker reads frames, runs the detection model, draws the annotations, and
posts the resulting JPEG frames to the API. The API serves those frames through
`GET /stream/{camera_id}`.

## Requirements

- Git access to the repository
- Python 3.12 managed by `uv`
- Docker or OrbStack is optional for this guide
- A webcam, video file, or RTSP-capable CCTV camera
- macOS on Apple Silicon is recommended for MPS acceleration

Install the tools on macOS with Homebrew if needed:

```bash
brew install git uv ffmpeg
```

## 1. Clone the repository

Clone the repository and enter its directory:

```bash
git clone https://github.com/jonathanbasuki/elven-plong-be.git

cd elven-plong-be
```

To test the CCTV integration branch:

```bash
git fetch origin
git switch --track -c dev/cctv-integration origin/dev/cctv-integration
```

If the branch already exists locally, use:

```bash
git switch dev/cctv-integration
```

## 2. Create the local environment file

Create `.env` from the committed template:

```bash
cp .env.example .env
```

For a local first run, these values are sufficient:

```env
API_BASE_URL=http://127.0.0.1:8080
CAMERA_ID=default
RTSP_URL=
YOLO_DEVICE=mps
DRY_RUN=0
INTERNAL_API_KEY=
```

`INTERNAL_API_KEY` can remain empty for local testing. If it is set, the same
value must be available to both the API and the worker. Never commit `.env` or
camera credentials.

## 3. Install dependencies

Run the project bootstrap command:

```bash
make bootstrap
```

This creates the virtual environment, installs the API, CCTV, and development
dependencies, and installs the pre-commit hooks.

Alternatively, install only the CCTV and development groups:

```bash
uv sync --group cctv --group dev
```

## 4. Download the detection model

Download the model weights once:

```bash
make models
```

The weights are stored in `models/` and are intentionally ignored by Git.
This download can take some time and requires an internet connection.

## 5. Validate the installation

Run the local checks:

```bash
make doctor
make lint
make test
```

The worker should report the machine architecture and whether MPS is
available. If MPS is unavailable, run the worker with `--device cpu`.

## 6. Start the API

Open a terminal in the project root and start the API:

```bash
make api
```

Verify the API from a second terminal:

```bash
curl http://127.0.0.1:8080/healthz
```

Expected response:

```json
{"status":"ok"}
```

Keep the API terminal running.

## 7. Start the CCTV worker

### Option A: Use a webcam

In a second terminal, run:

```bash
uv run --env-file .env python -m plong_elven.cctv \
  0 \
  --camera-id default \
  --zones zones.example.json
```

The positional source `0` refers to the first local webcam.

### Option B: Use a video file

```bash
uv run --env-file .env python -m plong_elven.cctv \
  /path/to/video.mp4 \
  --camera-id default \
  --zones zones.example.json
```

### Option C: Use an RTSP camera

Set the camera source in `.env`:

```env
CAMERA_ID=default
RTSP_URL=rtsp://<username>:<password>@<camera-ip>:554/<stream-path>
```

Then run:

```bash
uv run --env-file .env python -m plong_elven.cctv \
  --camera-id default \
  --zones zones.example.json
```

The RTSP URL must be reachable from the machine running the worker. Test it
independently when needed:

```bash
ffprobe -rtsp_transport tcp \
  -i "rtsp://<username>:<password>@<camera-ip>:554/<stream-path>" \
  -t 5 -f null -
```

Do not use `--dry-run` when testing the browser stream. In dry-run mode the
worker prints detection counts but does not post frames to the API.

## 8. View the CCTV stream

After the worker has successfully read and posted frames, open this URL:

```text
http://127.0.0.1:8080/stream/default
```

The stream is an MJPEG stream, so the browser may keep loading indefinitely.
That is expected. The annotated video should appear once the first frame has
arrived.

If the worker uses another camera ID, use the same ID in the URL:

```text
http://127.0.0.1:8080/stream/<camera-id>
```

For example, a worker started with `--camera-id cam1` must be viewed at:

```text
http://127.0.0.1:8080/stream/cam1
```

If the API is running on another Mac and the browser is on a different device,
replace `127.0.0.1` with the server Mac's LAN IP address, for example:

```text
http://192.168.1.20:8080/stream/default
```

## Troubleshooting

### `no frames received yet for 'default'`

The API has not received a frame for camera ID `default`. Check that:

- The worker is running.
- The worker is not using `--dry-run`.
- The worker's camera ID matches the URL camera ID.
- The webcam, video file, or RTSP source can be opened.
- The worker is using the correct `API_BASE_URL`.

### `Could not open source`

The worker cannot read the selected webcam, video file, or RTSP stream. Check
the source path, camera permissions, IP address, credentials, and RTSP path.

### `401 invalid internal api key`

The API and worker are using different `INTERNAL_API_KEY` values. For local
testing, leave the value empty in `.env`, restart both processes, and try again.

### The browser keeps loading with no image

An MJPEG endpoint remains open by design. Check the worker terminal for source
or HTTP errors. You can also inspect the response headers:

```bash
curl -v --max-time 5 http://127.0.0.1:8080/stream/default
```

Once frames are available, the response should be `200 OK` with a content type
similar to:

```text
multipart/x-mixed-replace; boundary=frame
```

## Stop the local processes

Press `Ctrl+C` in the worker terminal and the API terminal. The database is not
required for this CCTV stream test.
