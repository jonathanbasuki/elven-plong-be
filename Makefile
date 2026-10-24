COMPOSE = docker compose --env-file .env -f infra/docker-compose.yml

.PHONY: bootstrap sync-api sync-cctv sync-all db-up db-down db-migrate db-revision api cctv models benchmark-cctv lint test doctor

bootstrap:
	@test -f .env || cp .env.example .env
	uv sync --group api --group cctv
	uv run pre-commit install

sync-api:  ; uv sync --group api
sync-cctv: ; uv sync --group cctv
sync-all:  ; uv sync --group api --group cctv --no-dev

db-up:       ; $(COMPOSE) up -d db
db-down:     ; $(COMPOSE) down
db-migrate:  ; uv run --env-file .env alembic upgrade head
db-revision: ; uv run --env-file .env alembic revision --autogenerate -m "$(message)"

# `uv run` does not read .env on its own; --env-file makes INTERNAL_API_KEY/ZONES_PATH/etc.
# actually reach the process (without it, the auth check always sees an unset key).
api:         ; uv run --env-file .env uvicorn plong_elven.api.main:app --host 127.0.0.1 --port 8080 --reload

cctv:        ; uv run --env-file .env python -m plong_elven.cctv $(ARGS)
benchmark-cctv: ; uv run --env-file .env python scripts/benchmark_cctv.py $(ARGS)

# Downloads YOLO weights into models/ via Ultralytics' own fetch (models/*.pt is gitignored).
models:
	@mkdir -p models
	uv run python -c "from ultralytics import YOLO; YOLO('models/yolo26n.pt'); YOLO('models/yolo26l.pt')"

lint:        ; uv run ruff check . && uv run ruff format --check .
test:        ; uv run pytest
doctor:      ; uv run python scripts/doctor.py
