# Authentication Setup

This guide covers only the initial authentication setup and verification. It
assumes the repository has already been set up locally.

The current authentication flow supports:

```text
POST /api/v1/auth/login
POST /api/v1/auth/refresh
POST /api/v1/auth/logout
GET  /api/v1/me
PUT  /api/v1/me/push-token
POST /api/v1/tickets        (admin only; see API_V1_MIGRATION.md §6)
```

All request and response fields are camelCase (`accessToken`, `refreshToken`,
`fullName`, ...). Snake_case bodies get `422 validation_error`. The old profile
and push-token routes are removed; see
[API_V1_MIGRATION.md](API_V1_MIGRATION.md).

The current roles are:

```text
floor_staff   (id 1)
ops_manager   (id 2)
admin         (id 3)
```

Roles do not inherit. Every `/api/v1/*` route except login, refresh and logout
needs a valid access token. `POST /api/v1/broadcasts` and `.../finish` need
`ops_manager`; `.../accept` and `.../complete` need `floor_staff`; tickets and
the camera routes need `admin`. `admin` has no dispatch powers. A request with
no token gets `401 unauthenticated`; a token with the wrong role gets `403`
with `error.code = "wrong_role"`. Every error has the shape
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

Outside `APP_ENV=dev` (the default is `prod`), the API refuses to start when
`JWT_SECRET` or `INTERNAL_API_KEY` is shorter than 32 characters. With
`APP_ENV=dev` it starts, but auth calls fail with `503 auth_not_configured`
until `JWT_SECRET` has at least 32 characters.

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

This runs `alembic upgrade head` and creates (among the dispatch tables):

```text
roles
users
refresh_tokens
alembic_version
```

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

The script asks for the password twice. It stores only an Argon2id password
hash in PostgreSQL.

Create an admin user (needed for the dev page, tickets and the camera routes):

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

For local dispatch testing, `scripts/seed_dispatch.py` seeds one admin, one
manager and three floor-staff users (plus a zone) in one shot, all sharing the
password `password123`:

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

Keep both tokens temporarily for the following tests. Never commit or share
them.

## 9. Test the `/me` endpoint

Copy the `accessToken` from the login response. Click the Swagger **Authorize**
button and paste the access token without the `Bearer` prefix:

```text
eyJhbGciOiJIUzI1NiIs...
```

Then execute:

```text
GET /api/v1/me
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
curl -i http://127.0.0.1:8080/api/v1/me \
  -H "Authorization: Bearer <access-token>"
```

## 10. Test refresh token rotation

Open `POST /api/v1/auth/refresh` and submit the `refreshToken` from login:

```json
{
  "refreshToken": "<refresh-token>"
}
```

The response contains a new access token and a new refresh token. The old
refresh token is revoked and must not be accepted again.

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
token is cleared. An unknown or already revoked token also returns `200`.

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
- Logout sets `revoked_at` on the latest row.
- Raw refresh token values are never stored in the database.

## Authentication endpoint summary

| Endpoint | Authentication | Database behavior |
|---|---|---|
| `POST /api/v1/auth/login` | None | Reads user, updates login time, creates refresh token |
| `POST /api/v1/auth/refresh` | `refreshToken` in body | Revokes old token and creates a new one |
| `POST /api/v1/auth/logout` | `refreshToken` in body | Revokes refresh token, clears push token |
| `GET /api/v1/me` | Bearer access token | Reads active user and role |
| `PUT /api/v1/me/push-token` | Bearer access token | Stores the device's APNs token on the user row |
| `POST /api/v1/tickets` | Bearer access token, role `admin` | None (signs a 60-second ticket) |
| `GET /readyz` | None | Executes `SELECT 1` |

### Registering a device for push notifications

```bash
curl -i -X PUT http://127.0.0.1:8080/api/v1/me/push-token \
  -H "Authorization: Bearer <access-token>" -H "Content-Type: application/json" \
  -d '{"pushToken": "<64-char-hex-apns-device-token>"}'
```

The frontend calls this automatically after login (see `AppDelegate` in
`PushNotifications.swift`). The app must move to this route from the old
one; see [API_V1_MIGRATION.md](API_V1_MIGRATION.md). The token itself is only useful once APNs is
configured server-side — see [PUSH_NOTIFICATIONS.md](PUSH_NOTIFICATIONS.md).

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

### `Invalid refresh token`

The token may have expired, been used during rotation, or been revoked during
logout. Login again to obtain a new token pair.

## Stop local services

Stop the API with `Ctrl+C`. Stop PostgreSQL when it is no longer needed:

```bash
make db-down
```
