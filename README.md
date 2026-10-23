# Plong Elven - Backend

Backend for the crowd-detection system. The current baseline includes the
Python environment, a process health endpoint, the `cctv` detection worker,
an in-memory detection-ingestion + WebSocket broadcast endpoint, and a local
PostgreSQL service. Persistence (migrations, a zones table, aggregated
stats) and app notifications are not implemented yet.

## Repository structure

```text
Plong-Elven/
├── .github/workflows/       # GitHub Actions for linting and tests
├── docs/                    # Technical documentation and API contracts
├── infra/                   # Local infrastructure, currently PostgreSQL Compose
├── models/                  # Model weights; model files are not committed
├── scripts/                 # Manual utilities such as doctor.py
├── src/plong_elven/         # Main Python application source code
│   ├── api/                 # FastAPI app and HTTP/WebSocket endpoints
│   ├── cctv/                # Camera -> YOLO -> zone-count detection worker
│   └── __init__.py          # Package entry point
├── tests/                   # Automated tests
├── zones.example.json       # Example zone-polygon config for the cctv worker
├── .env.example             # Local configuration template without real secrets
├── .gitignore               # Files excluded from Git
├── .pre-commit-config.yaml  # Pre-commit linting and secret scanning hooks
├── Makefile                 # Development command shortcuts
├── pyproject.toml           # Project metadata and dependency groups
└── uv.lock                  # Reproducible dependency lockfile
```

Database models, migrations, and the notification service will be added as
implementation continues. The API currently exposes `GET /healthz`,
`POST /detections` + `WS /ws/counts` (count ingestion and live broadcast),
and `POST /frames/{camera_id}` + `GET /stream/{camera_id}` (annotated-frame
ingestion and an MJPEG live view) — no persistence on either path yet.

## Requirements

- macOS on Apple Silicon or another platform supported by Python 3.12
- `uv`
- Docker with Compose support (only needed for PostgreSQL)

## Project Setup

Follow these steps after receiving repository access.

### 1. Install prerequisites

Install the following tools:

- Git
- `uv`
- Docker with Compose support, such as Docker Desktop or OrbStack

On macOS with Homebrew:

```bash
brew install git uv
brew install --cask orbstack
```

Make sure Docker or OrbStack is running before starting the database.

### 2. Clone the repository

Using SSH:

```bash
git clone git@github.com:jonathanbasuki/elven-plong-be.git
```

Or using HTTPS:

```bash
git clone https://github.com/jonathanbasuki/elven-plong-be.git
```

Then enter the project directory:

```bash
cd elven-plong-be
```

### 3. Create local configuration

Copy the example configuration. This creates a local `.env` file that is
ignored by Git:

```bash
cp .env.example .env
```

For the current health-check setup, the default values are sufficient. Do not
commit `.env`, camera credentials, APNs keys, or other secrets.

### 4. Install dependencies and Git hooks

```bash
make bootstrap
```

This creates the virtual environment through `uv`, installs the API, cctv,
and development dependency groups, and installs the pre-commit hooks.

The `cctv` dependency group includes computer-vision packages (OpenCV,
Ultralytics) and can take longer to install.

### 5. Validate the local setup

```bash
make doctor
make lint
make test
uv lock --check
uv run pre-commit run --all-files
```

Expected results:

- `make doctor` prints the Python version and machine architecture.
- `make lint` reports that all Ruff checks passed.
- `make test` reports at least one passing test.
- `uv lock --check` completes without changing `uv.lock`.
- Pre-commit completes without reporting a secret or formatting error.

### 6. Run the API

Start the API in the first terminal:

```bash
make api
```

Keep that terminal running. In a second terminal, check the health endpoint:

```bash
curl -i http://127.0.0.1:8080/healthz
# {"status":"ok"}
```

Open the interactive API documentation at:

```text
http://127.0.0.1:8080/docs
```

The API is working when the health request returns HTTP `200` and the JSON
body is `{"status":"ok"}`. Stop the API with `Ctrl+C`.

### 7. Start the local database when needed

Make sure Docker or OrbStack is running, then execute:

```bash
make db-up
docker compose --env-file .env -f infra/docker-compose.yml ps
```

The database container should be running or healthy. Validate the Compose
configuration with:

```bash
docker compose --env-file .env -f infra/docker-compose.yml config --quiet
```

Database migrations and application database access have not been added yet.
Stop the local database when it is no longer needed:

```bash
make db-down
```

### 8. Run the cctv worker

Download the YOLO weights once (goes to `models/`, which is gitignored):

```bash
make models
```

With the API running (step 6), start the worker in a separate terminal:

```bash
make cctv ARGS="--zones zones.example.json --dry-run"
```

This reads `RTSP_URL` straight from `.env` as the camera source (`make cctv`
uses `uv run --env-file .env`, since plain `uv run` does not load `.env` on
its own). Pass a camera index or video file instead by giving `ARGS` a
positional source, e.g. `ARGS="0 --zones zones.example.json"`.

`--dry-run` prints counts instead of posting them — drop it once the API is
running and you want counts to actually reach `POST /detections`. Draw real
zone polygons for your camera using the `draw_zones.py` tool from the
`plong-model` project, which shares the same `zones.json` schema.

The worker also posts an annotated JPEG every `--frame-interval` seconds
(default 0.2s) to `POST /frames/{camera_id}`, so anyone connected to the API
can watch the feed, with zone outlines/boxes drawn in, at:

```text
http://127.0.0.1:8080/stream/cam1
```

(open directly in a browser, or embed with `<img src="...">`). Pass
`--no-stream` to the worker to skip posting frames, and add `?key=...` to the
stream URL if `INTERNAL_API_KEY` is set (a browser `<img>` tag can't send a
custom header, so the key is a query param there instead, unlike every other
endpoint).

## Development roles

The `api` dependency group is for server/API work. The `cctv` group is for
the camera/model worker. It is intentionally separate because it installs
large computer-vision dependencies.

```bash
uv sync --group api --group dev
uv sync --group cctv --group dev
```

Use a short-lived branch for your work:

```bash
git checkout -b feat/your-change
```

Before opening a pull request:

```bash
make lint
make test
git status
git push -u origin feat/your-change
```

Keep model weights, recordings, camera URLs, and APNs keys outside Git. Every
push and pull request runs the API lint and test checks in GitHub Actions.
