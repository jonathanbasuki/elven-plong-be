# CCTV Integration Setup

This guide explains how to run the CCTV worker locally and view its annotated
MJPEG stream through the Plong Elven API.

The local flow is:

```text
CCTV -> CCTV worker -> Plong Elven API -> browser
```

The worker reads frames, runs the detection model, draws the annotations, and
posts counts and JPEG frames to the worker routes
`POST /api/v1/cameras/{cameraId}/detections`, `.../frames` and
`.../frames/raw`, with the `X-Internal-Api-Key` header. The detections body is
`{"counts": {"z2": 3}}`; the camera id is in the path. The API serves the
annotated frames through `GET /dev/cameras/{cameraId}/stream?ticket=...`,
which needs a 60-second admin ticket (see section 8).

The stream, the dev page, the ticket route and the zone editor API all live
under `/dev` and exist only when the API runs with `APP_ENV=dev`. In
production there is no browser stream and no zone editor API: edit
`zones.json` by hand. The worker re-reads its `--zones` file every 2 seconds,
so a hand edit takes effect without a restart. The iOS app does not use the
API stream; it plays the camera's video as HLS from MediaMTX
([SETUP_HLS.md](SETUP_HLS.md)).

This guide runs the worker with raw `uv run` commands to show every flag
explicitly. Day to day, prefer the Makefile shortcuts instead:
`make cctv ARGS="..."` (same flags, positional source first) for a webcam/file,
or `make cctv-relay` for an RTSP camera behind MediaMTX — see
[SETUP_HLS.md](SETUP_HLS.md), which also covers the important gotcha that only
one RTSP client can read the camera at a time. To draw real zone polygons
instead of using the flat `zones.example.json` template below, use the
in-browser editor at `GET /dev/cameras/{cameraId}` (`APP_ENV=dev` only, admin
login) — see the main [README](../README.md), step 11. The editor saves to the
API's `ZONES_PATH` (default `zones.json`), and the worker only sees that file
if it runs with `--zones zones.json`; the commands below use
`zones.example.json`, so editor saves do not reach them.

## Requirements

- Git access to the repository
- Python 3.12 managed by `uv`
- Docker or OrbStack for PostgreSQL (README steps 5–6), needed to log in and
  view the stream
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

The routes in this guide match the `dev/api-contract-rework` branch. To test
it:

```bash
git fetch origin
git switch --track -c dev/api-contract-rework origin/dev/api-contract-rework
```

If the branch already exists locally, use:

```bash
git switch dev/api-contract-rework
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
INTERNAL_API_KEY=<generated-value>
```

`INTERNAL_API_KEY` must not be blank: a blank key makes the API reject every
worker call with `401`, even with `APP_ENV=dev`. Generate one with
`openssl rand -hex 32` (outside `APP_ENV=dev` the API refuses to start with a
key shorter than 32 characters). The same value must be available to both the
API and the worker. Never commit `.env` or camera credentials.

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

After the worker has successfully read and posted frames, the easiest viewer
is the dev page (API running with `APP_ENV=dev`, admin login):

```text
http://127.0.0.1:8080/dev/cameras/default
```

It mints a ticket and loads the stream for you. To open the stream directly,
log in as an admin, mint a ticket for the camera, and open the URL within 60
seconds:

```bash
ADMIN_TOKEN=$(curl -s -X POST http://127.0.0.1:8080/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email": "admin@plongelven.io", "password": "password123"}' \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["accessToken"])')
TICKET=$(curl -s -X POST http://127.0.0.1:8080/dev/tickets \
  -H "Authorization: Bearer $ADMIN_TOKEN" -H "Content-Type: application/json" \
  -d '{"scope": "stream:default"}' \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["ticket"])')
echo "http://127.0.0.1:8080/dev/cameras/default/stream?ticket=$TICKET"
```

The ticket is checked only when the stream opens, so an open stream keeps
running after the 60 seconds; reloading needs a new ticket. The ticket appears
in the server access log because it is in the URL.

The stream is an MJPEG stream, so the browser may keep loading indefinitely.
That is expected. The annotated video should appear once the first frame has
arrived.

If the worker uses another camera ID, use the same ID in the scope and the
URL. For example, a worker started with `--camera-id cam1` needs a
`stream:cam1` ticket and is viewed at
`/dev/cameras/cam1/stream?ticket=...`. A ticket for one camera does not
work for another.

If the API is running on another Mac and the browser is on a different device,
replace `127.0.0.1` with the server Mac's LAN IP address, for example
`http://192.168.1.20:8080/dev/cameras/default`.

## Troubleshooting

### `no frames received yet for 'default'`

The API has not received a frame for camera ID `default`. Check that:

- The worker is running.
- The worker is not using `--dry-run`.
- The worker's camera ID matches the URL camera ID.
- `GET /dev/cameras/{cameraId}/stream` and `.../snapshot` return this `404`
  until the first frame arrives.
- The webcam, video file, or RTSP source can be opened.
- The worker is using the correct `API_BASE_URL`.

### `Could not open source`

The worker cannot read the selected webcam, video file, or RTSP stream. Check
the source path, camera permissions, IP address, credentials, and RTSP path.

### `401 invalid internal api key`

The API and worker are using different `INTERNAL_API_KEY` values, or the key
is blank (a blank key rejects every worker call). Set the same non-blank value
for both in `.env`, restart both processes, and try again.

### `413 payload_too_large` or `422 validation_error` from a worker route

A frame body is limited to 5 MB; lower `--jpeg-quality` if frames are larger.
A detections body may hold at most 50 zones, each count an integer from 0 to
10000, and each zone id and the camera id must match `^[A-Za-z0-9_-]{1,64}$`.

### `401 unauthenticated` on the stream

The `?ticket=` is missing, older than 60 seconds, or was minted for another
camera or for `crowd:live`. Mint a new `stream:<cameraId>` ticket. An access
token in the `Authorization` header does not work on the stream.

### The browser keeps loading with no image

An MJPEG endpoint remains open by design. Check the worker terminal for source
or HTTP errors. You can also inspect the response headers:

```bash
curl -v --max-time 5 "http://127.0.0.1:8080/dev/cameras/default/stream?ticket=$TICKET"
```

Once frames are available, the response should be `200 OK` with a content type
similar to:

```text
multipart/x-mixed-replace; boundary=frame
```

## Stop the local processes

Press `Ctrl+C` in the worker terminal and the API terminal. Viewing the stream
needs the database, because the admin login and the ticket check read the user
from PostgreSQL; stop it with `make db-down` when done.
