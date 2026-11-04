# Plong Elven - Backend

Backend for a crowd-detection and staff-dispatch system. A `cctv` worker reads
a camera feed, runs YOLO person-detection per zone, and posts counts to this
FastAPI service. The service persists users/auth, zones, crowd-dispatch
broadcasts, and 5-minute crowd history in PostgreSQL; pushes real APNs
notifications to staff and managers; and serves a live HLS camera relay
(via MediaMTX) plus a small in-browser zone-editor/dev-tools page.

```text
Camera --RTSP--> MediaMTX --RTSP--> cctv worker --YOLO--> POST /internal/v1/cameras/{cameraId}/detections --> Postgres
                    |                                                                     |
                    +--HLS--> iPhone app                                                  +--> APNs push (staff/managers)
```

## Repository structure

```text
Plong-Elven/
├── .github/workflows/       # GitHub Actions for linting and tests
├── docs/                    # Technical documentation and API contracts
├── infra/                   # Local infrastructure: PostgreSQL Compose, generated MediaMTX config
├── models/                  # Model weights; model files are not committed
├── scripts/                 # Manual utilities: doctor, create_user, seed_dispatch, mediamtx config gen
├── secrets/                 # APNs .p8 auth key; gitignored
├── src/plong_elven/         # Main Python application source code
│   ├── api/                 # FastAPI app and one router per domain (api/routers/)
│   ├── schemas/             # Pydantic request/response DTOs (camelCase)
│   ├── services/            # Business logic: auth, broadcasts, crowd, cameras, alerts, APNs
│   ├── state/               # In-memory live counts and frames
│   ├── cctv/                # Camera -> YOLO -> zone-count detection worker
│   ├── common/               # Shared settings and zone-status classification
│   └── __init__.py          # Package entry point
├── tests/                   # Automated tests
├── zones.example.json       # Template zone-polygon config, committed
├── zones.json               # Your real, per-deployment zones; gitignored
├── .env.example             # Local configuration template without real secrets
├── .gitignore               # Files excluded from Git
├── .pre-commit-config.yaml  # Pre-commit linting and secret scanning hooks
├── Makefile                 # Development command shortcuts
├── pyproject.toml           # Project metadata and dependency groups
└── uv.lock                  # Reproducible dependency lockfile
```

## What's implemented

| Area | Endpoints | Docs |
|---|---|---|
| Health/readiness | `GET /healthz`, `GET /readyz` | — |
| Auth (JWT + refresh tokens, roles) | `POST /api/v1/auth/{login,refresh,logout}`, `GET /api/v1/me`, `PUT /api/v1/me/push-token` | [docs/SETUP_AUTH.md](docs/SETUP_AUTH.md), [docs/AUTH_SCHEMA.md](docs/AUTH_SCHEMA.md) |
| Zones, live crowd, 5-min history | `GET /api/v1/zones[/{zoneId}]`, `GET /api/v1/zones/{zoneId}/crowd[/history]`, `WS /api/v1/crowd/live` | [docs/DISPATCH_API.md](docs/DISPATCH_API.md) |
| Crowd dispatch (broadcasts, staff) | `/api/v1/broadcasts*`, `GET /api/v1/staff/{staffId}` | [docs/DISPATCH_API.md](docs/DISPATCH_API.md) |
| CCTV ingestion (worker) | `POST /internal/v1/cameras/{cameraId}/{detections,frames,frames/raw}` | [docs/SETUP_CCTV.md](docs/SETUP_CCTV.md) |
| Camera admin: layout, snapshot, live annotated stream | `GET`/`PUT /api/v1/cameras/{cameraId}/layout`, `GET /api/v1/cameras/{cameraId}/{snapshot,stream}`, `POST /api/v1/tickets` | [docs/SETUP_CCTV.md](docs/SETUP_CCTV.md) |
| In-browser live view / zone editor / dev tools (`APP_ENV=dev` only) | `GET /dev/cameras/{cameraId}`, `POST /dev/cameras/{cameraId}/detections` | step 11 below |
| Push notifications (APNs) | fired internally on broadcast creation and crowd-status change | [docs/PUSH_NOTIFICATIONS.md](docs/PUSH_NOTIFICATIONS.md) |
| Live HLS camera relay (MediaMTX) | external process, not an API route | [docs/SETUP_HLS.md](docs/SETUP_HLS.md) |

Every endpoint except `/healthz`, `/readyz`, `POST /api/v1/auth/login`,
`/refresh`, `/logout` and the dev page itself needs credentials: a bearer
access token for `/api/v1/*`, `X-Internal-Api-Key` for the worker routes under
`/internal/v1/*`, or a short-lived `?ticket=` for the browser stream and
WebSocket. Roles are `floor_staff`, `ops_manager` and `admin`; they do not
inherit, and `admin` has no dispatch powers. Every error is
`{"error": {"code": "...", "message": "..."}}` and all JSON is camelCase.
Moving from the old routes: [docs/API_V1_MIGRATION.md](docs/API_V1_MIGRATION.md).

## Requirements

- macOS on Apple Silicon or another platform supported by Python 3.12
- `uv`
- Docker with Compose support (for PostgreSQL and MediaMTX)
- An Apple Developer account with an APNs Auth Key — only if you want real
  push notifications; everything else works without it

## Project Setup

Follow these steps after receiving repository access. Steps 1–7 get the API
and database running; steps 8+ are optional add-ons (live camera, push).

### 1. Install prerequisites

```bash
brew install git uv
brew install --cask orbstack
```

Make sure Docker or OrbStack is running before starting the database.

### 2. Clone the repository

```bash
git clone git@github.com:jonathanbasuki/elven-plong-be.git
cd elven-plong-be
```

### 3. Create local configuration

```bash
cp .env.example .env
```

`APP_ENV` defaults to `prod` when unset. Outside `APP_ENV=dev` the API refuses
to start unless `INTERNAL_API_KEY` and `JWT_SECRET` are each at least 32
characters. `.env.example` sets `APP_ENV=dev` for local work. In dev the
startup check is skipped, but a `JWT_SECRET` shorter than 32 characters still
makes auth calls fail with `503 auth_not_configured`, and a blank
`INTERNAL_API_KEY` rejects every worker call. Set a strong `JWT_SECRET`:

```bash
openssl rand -hex 32
```

```env
JWT_SECRET=<generated-value>
POSTGRES_PASSWORD=<pick-a-local-password>
DATABASE_URL=postgresql+asyncpg://plong_elven:<same-password>@127.0.0.1:5432/plong_elven
```

Leave `INTERNAL_API_KEY`, `RTSP_URL`, and the `APNS_*` values empty for now.
They are only needed for the CCTV worker, HLS relay, and push notifications
respectively (steps 8–10); the worker needs a non-blank `INTERNAL_API_KEY`
(generate one the same way). Never commit `.env`.

### 4. Install dependencies and Git hooks

```bash
make bootstrap
```

Creates the virtual environment through `uv`, installs the `api` and `cctv`
dependency groups plus dev tools, and installs the pre-commit hooks. The
`cctv` group includes OpenCV/Ultralytics and can take longer.

### 5. Start PostgreSQL and run migrations

```bash
make db-up
make db-migrate
```

Verify:

```bash
docker compose --env-file .env -f infra/docker-compose.yml ps
uv run --env-file .env alembic current   # should print the latest revision, suffixed "(head)"
```

`make db-migrate` runs `alembic upgrade head`, which also adds the `admin` role
(revision `a1b2c3d4e5f6`). Run it again after pulling a new build, before
starting the API.

### 6. Seed local users and a zone

```bash
uv run --env-file .env python scripts/seed_dispatch.py
```

Creates one zone (`z2`, from `zones.example.json`/`zones.json`) and five users,
all sharing the password `password123`: `admin@plongelven.io` (`admin`),
`manager@plongelven.io` (`ops_manager`), and `bunga@`, `citra@` and
`dedi@plongelven.io` (`floor_staff`). See [docs/SETUP_AUTH.md](docs/SETUP_AUTH.md) to create users
individually instead.

### 7. Run the API

```bash
make api
```

```bash
curl -i http://127.0.0.1:8080/healthz
# {"status":"ok"}
curl -i http://127.0.0.1:8080/readyz
# {"status":"ready","database":"connected"}
```

Interactive API docs: `http://127.0.0.1:8080/docs`. At this point auth and
dispatch are fully usable — see [docs/SETUP_AUTH.md](docs/SETUP_AUTH.md) and
[docs/DISPATCH_API.md](docs/DISPATCH_API.md) for the full request/response
walkthrough. Stop the API with `Ctrl+C`.

### 8. (Optional) Run a live camera through the cctv worker

Download YOLO weights once:

```bash
make models
```

Copy the zone template once (gitignored — it's your real deployment's zone
layout, not code):

```bash
cp zones.example.json zones.json
```

Point a webcam, video file, or RTSP camera at the worker:

```bash
make cctv ARGS="0 --dry-run"   # webcam, prints counts instead of posting
```

Drop `--dry-run` to actually post to
`POST /internal/v1/cameras/{cameraId}/detections` once the API is running
(set the same non-blank `INTERNAL_API_KEY` for both). Full walkthrough, including RTSP setup and the annotated MJPEG
stream: [docs/SETUP_CCTV.md](docs/SETUP_CCTV.md).

### 9. (Optional) Live HLS preview for the iOS app

Needs a real RTSP camera and MediaMTX (Docker). Full walkthrough:
[docs/SETUP_HLS.md](docs/SETUP_HLS.md). Short version:

```bash
make mediamtx      # terminal 1 — relays the camera to RTSP + HLS
make cctv-relay     # terminal 2 — worker reads MediaMTX's relay, not the camera directly
```

### 10. (Optional) Real push notifications

Needs an Apple Developer APNs Auth Key (`.p8`). Full walkthrough, including
where to put the key and which `.env` values to set:
[docs/PUSH_NOTIFICATIONS.md](docs/PUSH_NOTIFICATIONS.md).

### 11. Draw zones from the browser

With the API running with `APP_ENV=dev` (the page does not exist otherwise),
and the worker running for **Capture frame** to have a frame, open:

```text
http://127.0.0.1:8080/dev/cameras/default
```

The page opens without login, then asks you to log in with an admin account
(`admin@plongelven.io` from step 6). Every call it makes uses that login: the
access token is kept in memory only, and the stream and live counts use
60-second tickets from `POST /api/v1/tickets`. Three tabs:

- **Live Feed** — the MJPEG stream plus live per-zone counts.
- **Edit Zones** — capture a frame, click to draw a zone polygon, save to
  `zones.json` via `PUT /api/v1/cameras/{cameraId}/layout`. The worker reloads
  it automatically within 2 seconds, no restart needed.
- **Dev Tools** — manually set a count for a zone via
  `POST /dev/cameras/{cameraId}/detections`, to drive
  the app's Safe/Warning/Crowded states and test push alerts without needing
  real people in frame. Stop the cctv worker first, or it'll overwrite your
  manual value within ~1 second.

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
