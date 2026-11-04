# Authentication Setup

This guide covers only the initial authentication setup and verification. It
assumes the repository has already been set up locally.

The current authentication flow supports:

```text
POST /api/v1/auth/login
POST /api/v1/auth/refresh
POST /api/v1/auth/logout
GET  /api/v1/profile
PUT  /api/v1/profile/push-token
POST /dev/tickets        (admin only; see API_V1_MIGRATION.md §6)
```

All request and response fields are camelCase (`accessToken`, `refreshToken`,
`fullName`, ...). A body that sends a required field in snake_case (for
example `refresh_token`) gets `422 validation_error`. The old profile
and push-token routes are removed; see
[API_V1_MIGRATION.md](API_V1_MIGRATION.md).

The current roles are:

```text
floor_staff   (id 1)
ops_manager   (id 2)
admin         (id 3)
```

Roles do not inherit. Authentication per route group:

- `POST /api/v1/auth/{login,refresh,logout}`: no access token.
- `/api/v1/cameras/{cameraId}/...` (CCTV worker routes): the
  `X-Internal-Api-Key` header, not a user token.
- `WS /api/v1/crowd/live`: a bearer access token, or `?ticket=` with scope
  `crowd:live`.
- Every other `/api/v1/*` route: a bearer access token for any role.
- `POST /api/v1/broadcasts` and `.../finish` need `ops_manager`; `.../accept`
  and `.../complete` need `floor_staff`. `admin` has no dispatch powers.
- `/dev/*` exists only with `APP_ENV=dev`. The page `GET /dev/cameras/{cameraId}`
  needs no login; `GET /dev/cameras/{cameraId}/stream` needs a
  `stream:<cameraId>` ticket; `POST /dev/tickets` and the other `/dev/cameras`
  routes need an `admin` access token.

A request with no or an invalid token gets `401 unauthenticated`; a token with
the wrong role gets `403 wrong_role`. Every error has the shape
`{"error": {"code": "...", "message": "..."}}`.

## 1. Configure local authentication

Create the local environment file if it does not exist:

```bash
cp .env.example .env
```

Generate a strong JWT secret:

```bash
openssl rand -hex 32
```

Put the generated value in `.env`:

```env
JWT_SECRET=<generated-value>
ACCESS_TOKEN_MINUTES=15
REFRESH_TOKEN_DAYS=30
```

`APP_ENV` defaults to `prod` in the code; `.env.example` sets `APP_ENV=dev`.
For any value other than `dev`, the API refuses to start when `JWT_SECRET` or
`INTERNAL_API_KEY` is shorter than 32 characters. With `APP_ENV=dev` it starts,
but every call that signs or checks a token fails with `503 auth_not_configured`
until `JWT_SECRET` has at least 32 characters. A blank `INTERNAL_API_KEY`
rejects every worker call with `401`, even in dev.

Do not commit `.env`, passwords, JWT secrets, refresh tokens, or other local
credentials.

## 2. Install authentication dependencies

If the project dependencies have not been synchronized after the auth changes:

```bash
uv sync --group api --group dev
```

The API authentication implementation uses:

- SQLAlchemy async sessions
- PostgreSQL
- Argon2id password hashing
- JWT access tokens
- Hashed opaque refresh tokens

## 3. Start PostgreSQL

Make sure Docker or OrbStack is running, then execute:

```bash
make db-up
```

Check the container:

```bash
docker compose --env-file .env -f infra/docker-compose.yml ps
```

The database should be running or healthy.

## 4. Apply the database migration

Run the authentication migration:

```bash
make db-migrate
```

This runs `alembic upgrade head`. The auth tables are:

```text
roles
users
refresh_tokens
```

The same run also creates `zones`, `broadcasts`, `broadcast_responses`,
`zone_count_history` and `alembic_version`.

The migrations seed three roles. `admin` comes from revision `a1b2c3d4e5f6`,
so run `make db-migrate` again after pulling it:

```text
floor_staff
ops_manager
admin
```

Verify the migration state:

```bash
uv run --env-file .env alembic current
uv run --env-file .env alembic check
```

`alembic check` should report that no new upgrade operations are detected.

## 5. Create a local user

Create an initial manager user:

```bash
uv run --env-file .env python scripts/create_user.py \
  --email manager@example.com \
  --full-name "Ops Manager" \
  --role ops_manager
```

The script asks for the password twice, lowercases the email, and stores only
an Argon2id password hash in PostgreSQL. `--role` accepts `floor_staff`,
`ops_manager` or `admin`.

Create an admin user (needed for the dev page, tickets and the `/dev/cameras` routes):

```bash
uv run --env-file .env python scripts/create_user.py \
  --email admin@example.com \
  --full-name "Admin" \
  --role admin
```

Create a floor staff user when needed:

```bash
uv run --env-file .env python scripts/create_user.py \
  --email staff@example.com \
  --full-name "Floor Staff" \
  --role floor_staff
```

For local dispatch testing, `scripts/seed_dispatch.py` seeds one admin
(`admin@plongelven.io`), one manager (`manager@plongelven.io`) and three
floor-staff users, plus the first zone in `zones.json` (or `ZONES_PATH`). All
seeded users share the password `password123`. Existing rows are skipped:

```bash
uv run --env-file .env python scripts/seed_dispatch.py
```

Verify the users and roles directly in PostgreSQL:

```bash
docker compose --env-file .env -f infra/docker-compose.yml exec -T db \
  psql -U plong_elven -d plong_elven \
  -c "SELECT email, full_name, role_id, is_active FROM users;" \
  -c "SELECT id, name FROM roles ORDER BY id;"
```

## 6. Start the API

Start the API:

```bash
make api
```

Keep the API terminal running. Open the API documentation in a browser:

```text
http://127.0.0.1:8080/docs
```

## 7. Check database readiness

Run:

```bash
curl -i http://127.0.0.1:8080/readyz
```

Expected response:

```json
{
  "status": "ready",
  "database": "connected"
}
```

If the database query fails, the response is
`{"status": "not_ready", "database": "unavailable"}`.

## 8. Test login

In Swagger, open `POST /api/v1/auth/login`, click **Try it out**, and submit:

```json
{
  "email": "manager@example.com",
  "password": "the-password-used-during-user-setup"
}
```

The response contains:

```json
{
  "accessToken": "...",
  "refreshToken": "...",
  "tokenType": "bearer",
  "expiresIn": 900,
  "user": {
    "id": "...",
    "email": "manager@example.com",
    "fullName": "Ops Manager",
    "role": "ops_manager",
    "isActive": true
  }
}
```

`expiresIn` is the access-token lifetime in seconds
(`ACCESS_TOKEN_MINUTES * 60`, so `900` by default). The refresh token lasts
`REFRESH_TOKEN_DAYS` (default 30). A wrong password, unknown email or inactive
user all return `401 unauthenticated` with the message
`Invalid email or password`.

Keep both tokens temporarily for the following tests. Never commit or share
them.

## 9. Test the profile endpoint

Copy the `accessToken` from the login response. Click the Swagger **Authorize**
button and paste the access token without the `Bearer` prefix:

```text
eyJhbGciOiJIUzI1NiIs...
```

Then execute:

```text
GET /api/v1/profile
```

Expected response:

```json
{
  "id": "...",
  "email": "manager@example.com",
  "fullName": "Ops Manager",
  "role": "ops_manager",
  "isActive": true
}
```

The same request can be tested with curl:

```bash
curl -i http://127.0.0.1:8080/api/v1/profile \
  -H "Authorization: Bearer <access-token>"
```

## 10. Test refresh token rotation

Open `POST /api/v1/auth/refresh` and submit the `refreshToken` from login:

```json
{
  "refreshToken": "<refresh-token>"
}
```

The response contains `accessToken`, `refreshToken`, `tokenType` and
`expiresIn`, without the `user` object. The old refresh token is revoked; using
it again returns `401 unauthenticated` (`Invalid refresh token`).

## 11. Test logout

Open `POST /api/v1/auth/logout` and submit the newest refresh token:

```json
{
  "refreshToken": "<latest-refresh-token>"
}
```

Expected response:

```text
200 {"status": "ok"}
```

The refresh token is marked as revoked in the database and the user's push
token is cleared. An unknown or already revoked token also returns `200` and
changes nothing. Logout does not invalidate access tokens already issued; they
stay valid until they expire.

## 12. Verify token records

Check refresh token records directly:

```bash
docker compose --env-file .env -f infra/docker-compose.yml exec -T db \
  psql -U plong_elven -d plong_elven \
  -c "SELECT user_id, expires_at, revoked_at, created_at FROM refresh_tokens ORDER BY created_at;"
```

Expected behavior:

- Login creates a refresh token row.
- Refresh sets `revoked_at` on the old row and creates a new row.
- Logout sets `revoked_at` on the row of the submitted token.
- `user_agent` and `ip_address` stay `NULL`; no code writes them.
- Raw refresh token values are never stored in the database.

## Authentication endpoint summary

| Endpoint | Authentication | Database behavior |
|---|---|---|
| `POST /api/v1/auth/login` | None | Reads user, updates login time, creates refresh token |
| `POST /api/v1/auth/refresh` | `refreshToken` in body | Revokes old token and creates a new one |
| `POST /api/v1/auth/logout` | `refreshToken` in body | Revokes refresh token, clears push token; returns `{"status": "ok"}` |
| `GET /api/v1/profile` | Bearer access token | Reads active user and role |
| `PUT /api/v1/profile/push-token` | Bearer access token | Stores `pushToken` on the user row; returns `{"status": "ok"}` |
| `POST /dev/tickets` | Bearer access token, role `admin` (`APP_ENV=dev` only) | None (signs a 60-second ticket; body `{"scope": "stream:<cameraId>"}` or `{"scope": "crowd:live"}`, response `{"ticket", "expiresIn": 60}`) |
| `GET /readyz` | None | Executes `SELECT 1` |

### Registering a device for push notifications

```bash
curl -i -X PUT http://127.0.0.1:8080/api/v1/profile/push-token \
  -H "Authorization: Bearer <access-token>" -H "Content-Type: application/json" \
  -d '{"pushToken": "<apns-device-token>"}'
```

The server only checks that `pushToken` is a non-empty string; it does not
check the APNs token format. A new call replaces the stored token (one token
per user). Which iOS code calls this route after login: TBD (frontend repo, not
checked here). Old route names are listed in
[API_V1_MIGRATION.md](API_V1_MIGRATION.md). The token is only used once APNs is
configured server-side; see [PUSH_NOTIFICATIONS.md](PUSH_NOTIFICATIONS.md).

## Troubleshooting

### `503 auth_not_configured`

Only possible with `APP_ENV=dev`. Set `JWT_SECRET` in `.env` to at least 32
characters, restart the API, and try again:

```bash
openssl rand -hex 32
```

### `Invalid email or password`

Check that the user exists and use the password entered during
`create_user.py`. The password is case-sensitive.

### `Not authenticated`

The request does not contain an access token. Use Swagger's
**Authorize** button or send:

```http
Authorization: Bearer <access-token>
```

### `Invalid access token`

The access token is expired, malformed, signed with another `JWT_SECRET`, or
belongs to a user that is missing or inactive. Refresh or log in again.

### `403 wrong_role`

The token is valid but the user's role is not allowed on that route. Roles do
not inherit, so `admin` cannot create or accept broadcasts.

### `Invalid refresh token`

The token may have expired, been used during rotation, or been revoked during
logout. Login again to obtain a new token pair.

## Stop local services

Stop the API with `Ctrl+C`. Stop PostgreSQL when it is no longer needed:

```bash
make db-down
```
