COMPOSE = docker compose --env-file .env -f infra/docker-compose.yml

.PHONY: bootstrap sync-api sync-worker sync-all db-up db-down api lint test doctor

bootstrap:                      
	@test -f .env || cp .env.example .env
	uv sync --group api --group worker
	uv run pre-commit install

sync-api:    ; uv sync --group api
sync-worker: ; uv sync --group worker
sync-all:    ; uv sync --group api --group worker --no-dev

db-up:       ; $(COMPOSE) up -d db
db-down:     ; $(COMPOSE) down

api:         ; uv run uvicorn plong_elven.api.main:app --host 0.0.0.0 --port 8080 --reload

lint:        ; uv run ruff check . && uv run ruff format --check .
test:        ; uv run pytest
doctor:      ; uv run python scripts/doctor.py
