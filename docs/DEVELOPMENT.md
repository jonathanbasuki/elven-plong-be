# Development

Step-by-step machine setup is in [SETUP_AUTH.md](SETUP_AUTH.md) (database, users),
[SETUP_CCTV.md](SETUP_CCTV.md) (camera process) and [SETUP_HLS.md](SETUP_HLS.md) (MediaMTX).
This page is the quick reference. Variables are in [CONFIGURATION.md](CONFIGURATION.md).

## Setup

Requires Python >= 3.12, [uv](https://docs.astral.sh/uv/) and Docker.

```bash
make bootstrap      # copy .env.example -> .env if missing, uv sync api+cctv, install pre-commit
# edit .env: POSTGRES_PASSWORD, DATABASE_URL, JWT_SECRET (>= 32 chars), INTERNAL_API_KEY
make db-up
make db-migrate
uv run --env-file .env python scripts/seed_dispatch.py
make api            # http://127.0.0.1:8080, also bound to 0.0.0.0
```

## Make targets

| Target | Runs |
|---|---|
| `bootstrap` | copy `.env`, `uv sync --group api --group cctv`, `pre-commit install` |
| `sync-api` / `sync-cctv` | `uv sync --group api` / `--group cctv` |
| `sync-all` | `uv sync --group api --group cctv --no-dev` |
| `db-up` / `db-down` | `docker compose --env-file .env -f infra/docker-compose.yml up -d db` / `down` |
| `db-migrate` | `uv run --env-file .env alembic upgrade head` |
| `db-revision message="..."` | `alembic revision --autogenerate -m "$(message)"` |
| `api` | `uvicorn plong_elven.app.api.main:app --host 0.0.0.0 --port 8080 --reload` |
| `cctv ARGS="..."` | `python -m plong_elven.cctv $(ARGS)` |
| `cctv-relay` | camera process on `rtsp://127.0.0.1:8554/cam1`, `--camera-id default`, frame interval 0.2, JPEG 80 |
| `benchmark-cctv ARGS="..."` | `scripts/benchmark_cctv.py` |
| `models` | download `models/yolo26n.pt` and `models/yolo26l.pt` via Ultralytics |
| `mediamtx-config` / `mediamtx` | write `infra/mediamtx.yml` from `RTSP_URL` / run MediaMTX in Docker (ports 8554, 8888) |
| `lint` | `ruff check .` and `ruff format --check .` |
| `test` | `uv run pytest` |
| `doctor` | `scripts/doctor.py` (Python version, arch, MPS availability) |

All run targets except `test`, `lint` and `models` use `uv run --env-file .env`,
because `uv run` does not load `.env` by itself and the camera process reads only `os.environ`.

## Running

- API: `make api`. With `APP_ENV=dev` the dev page is at `/dev/cameras/<cameraId>`
  (log in with an admin account).
- Camera: `make cctv ARGS="rtsp://... --camera-id cam1"` or `make cctv-relay`.
  `INTERNAL_API_KEY` must match the API's. Use `--dry-run` to print counts only.

## Migrations

Alembic, `alembic/versions/`, in order:

1. `ad950fffbf32` create auth tables (`roles` with `floor_staff`=1, `ops_manager`=2; `users`; `refresh_tokens`)
2. `7b5b2f7c1d21` create dispatch tables (`zones`, `broadcasts`, ...)
3. `f9fe68ec2955` merge staff into users
4. `f67711b4a0bb` add `zone_count_history`
5. `a1b2c3d4e5f6` add `admin` role with id 3 (downgrade fails if admin users exist, FK RESTRICT)
6. `3f1a9c2e7b40` rename to the task schema: `broadcasts` → `task`, `broadcast_responses` → `task_respondents`, `zones` → `zone`, `zone_count_history` → `zone_logs`, `users.id` → `user_id`, `roles.id` → `role_id` (renames only)
7. `5c8d2e1f9a63` status text columns → lookup tables `task_status`, `task_respondent_status`, `user_status` (fixed ids, backfilled)
8. `8e4b7a0d2c15` zone: one `threshold` (from `crowded_threshold`), `is_active_task`, drop `map_image_url`; users: `zone_id`, `last_location`, drop `email_verified_at`/`updated_at`/`last_login_at`; task: `instruction`
9. `c1d2e3f4a5b6` task status `complete` (4); `task_footage` table (one row per completed task's video)
10. `d2e3f4a5b6c7` task status `ongoing` (5)

Migrations 6–8 are hand-written (autogenerate would turn a rename into drop + create and lose rows). `alembic check` must report no differences after `upgrade head`.

New migration: `make db-revision message="describe change"`, then review the file.

## Users and seed data

- `uv run --env-file .env python scripts/seed_dispatch.py`: inserts zone `z1`
  (full-frame camera polygon, threshold 10) and five users
  (`admin@`, `manager@`, `bunga@`, `citra@`, `dedi@plongelven.io`), all with password
  `password123`. Skips rows that exist. Local dev only.
- `uv run --env-file .env python scripts/create_user.py --email a@b.c --full-name "Name" --role admin`:
  `--role` is `floor_staff`, `ops_manager` or `admin`; prompts for the password twice.

## Tests

```bash
uv run pytest
```

- Use plain `uv run pytest`, not `--env-file .env`. `tests/conftest.py` sets
  `APP_ENV=dev`, `INTERNAL_API_KEY` and `JWT_SECRET` before importing the app, and the
  tests expect those values. The rework plan records that loading the local `.env`
  (its `INTERNAL_API_KEY`) broke tests. `Settings` still reads `DATABASE_URL` from `.env`
  on its own, so `.env` must exist.
- `tests/test_task_race_pg.py` races two accepts for the last slot, and two broadcasts into one zone, against real
  Postgres. It reads `DATABASE_URL` from the environment and skips when it is unset or
  unreachable, unless `REQUIRE_PG=1`. To run it locally:
  `DATABASE_URL=postgresql+asyncpg://... uv run pytest tests/test_task_race_pg.py`
  (migrated database required).
- `tests/test_route_auth.py` walks every route and checks that anonymous calls and calls
  with the wrong credential are rejected. A new public route must be added to its allow-list.

## Lint

```bash
make lint   # ruff check . && ruff format --check .
```

Pre-commit runs ruff, ruff-format, gitleaks, detect-private-key and a 1024 KB large-file check.

## CI (`.github/workflows/ci.yml`)

One job, `checks`, on every push and pull request, Ubuntu with a `postgres:16-alpine`
service. Env: `DATABASE_URL` to that service, `JWT_SECRET`, `REQUIRE_PG=1`, `APP_ENV=dev`.

Steps: `uv python install 3.12`, `uv sync --locked --group api --group dev`,
`ruff check .`, `ruff format --check .`, `alembic upgrade head`,
`alembic downgrade -1 && alembic upgrade head`, `pytest`.

## Conventions

- Routers handle HTTP only; rules live in services, which raise `AppError` and do not
  import `fastapi` (checked for `task_service.py` by a test). The same holds for
  `core/state/`, `core/adapters/`, `core/helpers/` and `core/common/`.
- New dependencies are wired in `app/api/deps.py` so tests can override them.
- A service that has its own route is named after it (`zones.py` -> `zone_service.py`).
  A service with no route goes in `app/services/internal/`. Code that talks to an outside
  system goes in `core/adapters/`. Plain functions go in `core/helpers/`.
- New alert type: write a class that implements `AlertRule.evaluate()` and add it to
  `default_alert_rules()`; do not change `AlertService`.
- New push transport: implement `PushSender.send()` (log failures, never raise) and
  return it from `deps.get_push_sender()`.
- Request/response fields are camelCase; datetimes use `UtcDatetime`.
- Background work opens its own session from `SessionFactory`, never the request session.
