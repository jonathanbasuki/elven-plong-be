# Configuration

Start from `.env.example` (`make bootstrap` copies it to `.env`).

## How settings are loaded

- The API reads `Settings` in `src/plong_elven/common/settings.py` (pydantic-settings).
  It reads real environment variables first, then `.env` in the current directory.
  Values are cached (`get_settings()` is `lru_cache`).
- `ZONES_PATH` is read directly with `os.environ` (`api/deps.py`, `scripts/seed_dispatch.py`).
- The worker reads only `os.environ`, so run it with `uv run --env-file .env`
  (the Makefile does).
- Alembic (`alembic/env.py`) reads `DATABASE_URL` from `os.environ` and raises if it is unset.

## Fail-closed startup check

`check_security_settings()` runs in the app lifespan. If `APP_ENV` is anything
other than `dev` (the default is `prod`), the API refuses to start unless both
`INTERNAL_API_KEY` and `JWT_SECRET` are at least 32 characters.

Independently of `APP_ENV`:
- a blank `INTERNAL_API_KEY` rejects every worker call with 401;
- a `JWT_SECRET` shorter than 32 characters makes every token operation return
  503 `auth_not_configured` (login, refresh, bearer checks, tickets).

## API variables

| Variable | Default | Required | Notes |
|---|---|---|---|
| `DATABASE_URL` | none | yes | `postgresql+asyncpg://...`; also used by Alembic |
| `APP_ENV` | `prod` | no | `dev` skips the startup check and mounts the `/dev` router |
| `JWT_SECRET` | `""` | yes outside `dev` (>= 32 chars); in `dev` auth returns 503 until set to >= 32 chars | HS256 signing key for access tokens and tickets |
| `ACCESS_TOKEN_MINUTES` | `15` | no | access token lifetime; also `expiresIn` (seconds) |
| `REFRESH_TOKEN_DAYS` | `30` | no | refresh token lifetime |
| `INTERNAL_API_KEY` | `""` | yes outside `dev` (>= 32 chars); blank rejects all worker calls | shared with the worker |
| `ZONES_PATH` | `zones.json` | no | layout file used by `/dev/cameras/{id}/layout` and the seed script |
| `APNS_KEY_PATH` | `""` | for push | path to the `.p8` key; push is skipped (logged) if this, `APNS_TEAM_ID` or `APNS_KEY_ID` is blank |
| `APNS_KEY_ID` | `""` | for push | `kid` header of the APNs JWT |
| `APNS_TEAM_ID` | `""` | for push | `iss` claim |
| `APNS_TOPIC` | `""` | for push | app bundle id (`apns-topic`) |
| `APNS_USE_SANDBOX` | `true` | no | `true` -> `api.sandbox.push.apple.com`, else `api.push.apple.com` |

APNs setup: see [PUSH_NOTIFICATIONS.md](PUSH_NOTIFICATIONS.md).

## Infra and script variables

| Variable | Read by | Default | Notes |
|---|---|---|---|
| `POSTGRES_PASSWORD` | `infra/docker-compose.yml` | none | required (`${POSTGRES_PASSWORD:?...}`); DB and user are `plong_elven`, port `127.0.0.1:5432` |
| `RTSP_URL` | worker (default source), `scripts/generate_mediamtx_config.py` | worker: `0` | the script requires an `rtsp://` URL with host and port and writes `infra/mediamtx.yml` (mode 600) |
| `REQUIRE_PG` | `tests/test_broadcast_race_pg.py` | unset | `1` makes the Postgres race test fail instead of skip when no DB is reachable (set in CI) |

## CCTV worker

`python -m plong_elven.cctv [source] [flags]` (`src/plong_elven/cctv/loop.py`).

| Flag | Env default | Default | Meaning |
|---|---|---|---|
| `source` (positional) | `RTSP_URL` | `0` | camera index, video file or RTSP URL |
| `--zones` | - | `zones.json` | zones file; reloaded when its mtime changes (checked every 2 s) |
| `--model` | `YOLO_WEIGHTS` | `models/yolo26n.pt` | YOLO weights (`.env.example` sets `models/yolo26l.pt`) |
| `--imgsz` | - | `640` | inference size |
| `--conf` | - | `0.25` | detection confidence |
| `--anchor` | - | from zones file | `bottom_center`, `center` or `top_center` |
| `--device` | `YOLO_DEVICE` | none | e.g. `mps`, `cpu` |
| `--camera-id` | `CAMERA_ID` | `default` | path id posted to the API |
| `--api-base` | `API_BASE_URL` | `http://127.0.0.1:8080` | API base URL |
| `--api-key` | `INTERNAL_API_KEY` | `""` | sent as `X-Internal-Api-Key` |
| `--post-interval` | - | `1.0` | seconds between count posts |
| `--dry-run` | `DRY_RUN=1` | off | print counts, post nothing |
| `--frame-interval` | - | `0.2` | seconds between frame posts |
| `--jpeg-quality` | - | `80` | JPEG quality of posted frames |
| `--no-stream` | - | off | do not post frames |

The worker also sets `OPENCV_FFMPEG_CAPTURE_OPTIONS=rtsp_transport;tcp|stimeout;5000000`
if not already set. Camera setup: [SETUP_CCTV.md](SETUP_CCTV.md), HLS: [SETUP_HLS.md](SETUP_HLS.md).

## `zones.json` format

```json
{
  "version": 1,
  "anchor": "bottom_center",
  "zones": [
    {"id": "z1", "name": "Zone 1", "polygon": [[0, 0], [1, 0], [1, 1], [0, 1]]}
  ]
}
```

- `anchor`: `bottom_center`, `center` or `top_center` (which point of a person box decides its zone).
- `polygon`: at least 3 `[x, y]` points, normalized 0..1, origin top-left.
- `id`: becomes the key in posted `counts`; must match `^[A-Za-z0-9_-]{1,64}$`
  for the API to accept it, and must exist in the DB `zones` table for history and alerts.
- Validated by `common/zones.validate()`; `save_config()` always writes `version: 1`.
- A missing file means no zones (`anchor` `bottom_center`).
