# Plong Elven - Backend

Backend scaffold for the crowd-detection system. The current baseline includes
the Python environment, a process health endpoint, a smoke test, and a local
PostgreSQL service. Detection ingestion, authentication, migrations, and app
notifications are not implemented yet.

## Repository structure

```text
Plong-Elven/
├── .github/workflows/       # GitHub Actions for linting and tests
├── docs/                    # Technical documentation and API contracts
├── infra/                   # Local infrastructure, currently PostgreSQL Compose
├── models/                  # Model weights; model files are not committed
├── scripts/                 # Manual utilities such as doctor.py
├── src/plong_elven/         # Main Python application source code
│   ├── api/                 # FastAPI app and HTTP endpoints
│   └── __init__.py          # Package entry point
├── tests/                   # Automated tests
├── .env.example             # Local configuration template without real secrets
├── .gitignore               # Files excluded from Git
├── .pre-commit-config.yaml  # Pre-commit linting and secret scanning hooks
├── Makefile                 # Development command shortcuts
├── pyproject.toml           # Project metadata and dependency groups
└── uv.lock                  # Reproducible dependency lockfile
```

The `worker/` package, database models, migrations, notification service, and
ingestion routes will be added as implementation begins. Currently, the only
available API endpoint is `GET /healthz`.

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

This creates the virtual environment through `uv`, installs the API, worker,
and development dependency groups, and installs the pre-commit hooks.

The worker dependency group includes computer-vision packages and can take
longer to install. It is required for the future CCTV/model integration.

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

## Development roles

The `api` dependency group is for server/API work. The `worker` group is for
CCTV and model integration. The worker group is intentionally separate because
it installs large computer-vision dependencies.

```bash
uv sync --group api --group dev
uv sync --group worker --group dev
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
