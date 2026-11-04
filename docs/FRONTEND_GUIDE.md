# Frontend guide: what the API does and how to call it

For the iOS team. Every fact here comes from the backend code. The full route
list is in [API_REFERENCE.md](API_REFERENCE.md). The change list from the old
API is in [API_V1_MIGRATION.md](API_V1_MIGRATION.md).

Items marked **TBD** could not be checked from this repo.

## 1. The short version

The app uses **15 routes and 1 WebSocket**. All of them are under `/api/v1`.

| Area | What the app does with it | Routes |
|---|---|---|
| Login | Sign in, keep the session alive, sign out | `POST /auth/login`, `/auth/refresh`, `/auth/logout` |
| Profile | Show who I am, register the phone for pushes | `GET /profile`, `PUT /profile/push-token` |
| Zones | Show the list of zones and their map | `GET /zones`, `GET /zones/{zoneId}` |
| Crowd | Show how many people are in a zone now, and the past hour | `GET /zones/{zoneId}/crowd`, `GET /zones/{zoneId}/crowd/history`, `WS /crowd/live` |
| Broadcasts | Manager asks for staff, staff accept and finish | `GET /broadcasts`, `POST /broadcasts`, `POST /broadcasts/{id}/accept`, `/complete`, `/finish` |
| Staff | Look up one staff member | `GET /staff/{staffId}` |

The app does **not** call these. Do not build against them:

| Routes | Who uses them | Why not the app |
|---|---|---|
| `POST /api/v1/cameras/{cameraId}/…` | The camera program (YOLO worker) | Needs a secret key, not a login |
| `/dev/…` | A developer's browser | Exists only when the server runs with `APP_ENV=dev` |
| `GET /healthz`, `GET /readyz` | Monitoring | Not app features |

**Live video does not come from this API.** The app plays HLS from MediaMTX.
See [SETUP_HLS.md](SETUP_HLS.md).

## 2. Rules for every call

| Rule | Detail |
|---|---|
| Base URL | `http://<server>:8080` in dev (port from the `make api` target). Production URL: **TBD** |
| Body | JSON, with `Content-Type: application/json` |
| Field names | camelCase everywhere (`accessToken`, `zoneId`, `staffNeeded`) |
| Times | UTC, always end in `Z`, like `2026-11-03T10:15:30Z` |
| Ids | `broadcastId` and `staffId` are UUIDs. `zoneId` is a short string like `z1` |
| Login header | `Authorization: Bearer <accessToken>` on every route except login, refresh and logout |
| Unknown fields | Old snake_case bodies (`refresh_token`) get `422`. Send camelCase only |

### Errors

Every error has the same shape. Read `error.code`, not the message text.

```json
{"error": {"code": "slots_full", "message": "This broadcast already has enough staff."}}
```

| HTTP | `error.code` | What the app should do |
|---|---|---|
| 401 | `unauthenticated` | Token missing or expired. Refresh once, then retry. If refresh fails, go to the login screen. |
| 403 | `wrong_role` | This user's role may not do this. Hide the button for that role. |
| 403 | `not_your_response` | The staff member did not accept this broadcast. |
| 404 | `not_found` | Unknown zone, broadcast or staff id. |
| 409 | `broadcast_open` | The zone already has an open broadcast. Show it instead. |
| 409 | `expired` | Too late to accept. Refresh the list. |
| 409 | `already_delegating` | This staff member is already on another broadcast. |
| 409 | `slots_full` | Enough staff accepted already. |
| 409 | `not_all_done` | Manager tried to finish before every accepted staff completed. |
| 422 | `validation_error` | The request body or query is wrong. The message names the field. |
| 503 | `auth_not_configured` | Server setup problem. Not the app's fault. |

## 3. Roles: who can do what

There are three roles. They do not inherit from each other.

| Action | `floor_staff` | `ops_manager` | `admin` |
|---|---|---|---|
| Log in, profile, push token | yes | yes | yes |
| Read zones, crowd, history, staff | yes | yes | yes |
| Read broadcasts | yes | yes | yes |
| Create a broadcast | no | **yes** | no |
| Accept a broadcast | **yes** | no | no |
| Complete a broadcast | **yes** | no | no |
| Finish a broadcast | no | **yes** | no |

The role is in the login response (`user.role`) and in `GET /profile`.

## 4. Login and keeping the session

Two tokens:

| Token | Lifetime | Use |
|---|---|---|
| `accessToken` | 15 minutes (`expiresIn` = 900 seconds) | Goes in the `Authorization` header |
| `refreshToken` | 30 days | Only sent to `/auth/refresh` and `/auth/logout` |

```
 login ──> accessToken + refreshToken  (store both safely, for example Keychain)
   │
   ├─ call API with accessToken
   │      └─ 401? ──> POST /auth/refresh ──> new accessToken + new refreshToken
   │                      └─ 401? ──> refresh token is dead: show login screen
   └─ sign out ──> POST /auth/logout
```

**A refresh token works once.** Each refresh returns a new refresh token and
revokes the old one. Always save the new one. Sending an old one gives `401`.

### Login

```bash
curl -X POST http://localhost:8080/api/v1/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"email": "manager@example.com", "password": "secret"}'
```

```json
{
  "accessToken": "eyJ...",
  "refreshToken": "q3f...",
  "tokenType": "bearer",
  "expiresIn": 900,
  "user": {
    "id": "5b2c1c0e-3a52-4b64-9f67-0c1f6a1d2b10",
    "email": "manager@example.com",
    "fullName": "Mina Manager",
    "role": "ops_manager",
    "isActive": true
  }
}
```

Wrong email or password gives `401 unauthenticated`.

### Refresh

```bash
curl -X POST http://localhost:8080/api/v1/auth/refresh \
  -H 'Content-Type: application/json' \
  -d '{"refreshToken": "q3f..."}'
```

The response has `accessToken`, `refreshToken`, `tokenType` and `expiresIn`.
It has **no `user` object**.

### Logout

```bash
curl -X POST http://localhost:8080/api/v1/auth/logout \
  -H 'Content-Type: application/json' \
  -d '{"refreshToken": "q3f..."}'
```

Returns `{"status": "ok"}`, even if the token is unknown. It also clears the
phone's push token on the server, so this user stops getting pushes. An access
token you already hold keeps working until it expires.

## 5. Profile and push notifications

```bash
curl http://localhost:8080/api/v1/profile \
  -H 'Authorization: Bearer <accessToken>'
```

Returns the same `user` object as login.

Register the phone for pushes after login (and again if iOS gives a new token):

```bash
curl -X PUT http://localhost:8080/api/v1/profile/push-token \
  -H 'Authorization: Bearer <accessToken>' \
  -H 'Content-Type: application/json' \
  -d '{"pushToken": "<apns-device-token>"}'
```

Returns `{"status": "ok"}`. A new call replaces the old token. Which iOS code
calls this today: **TBD**.

Who gets a push:

| Event | Who gets it |
|---|---|
| A manager creates a broadcast | All `floor_staff` with a push token |
| A zone becomes `warning` or `crowded` | All `ops_manager` with a push token |

More detail: [PUSH_NOTIFICATIONS.md](PUSH_NOTIFICATIONS.md).

## 6. Zones and live crowd

### List zones

```bash
curl http://localhost:8080/api/v1/zones -H 'Authorization: Bearer <accessToken>'
```

```json
[
  {
    "zoneId": "z1",
    "name": "Main gate",
    "mapImageUrl": "https://…",
    "polygon": [[0.1, 0.2], [0.4, 0.2], [0.4, 0.6], [0.1, 0.6]]
  }
]
```

`polygon` points are `[x, y]` as fractions of the camera picture, from 0 to 1.
`GET /zones/{zoneId}` returns one zone with the same fields.

### Current crowd in a zone

```bash
curl http://localhost:8080/api/v1/zones/z1/crowd -H 'Authorization: Bearer <accessToken>'
```

```json
{"zoneId": "z1", "count": 7, "status": "warning", "observedAt": "2026-11-03T10:15:30Z"}
```

| Field | Meaning |
|---|---|
| `count` | People counted in the last detection |
| `status` | `safe`, `warning` or `crowded`. The thresholds are per zone, set on the server |
| `observedAt` | When the camera program last reported. `null` if it has not reported since the server started |

The count is held in server memory. After a server restart it is empty until
the camera program reports again.

### Last hour (or more) of crowd

```bash
curl 'http://localhost:8080/api/v1/zones/z1/crowd/history?limit=12' \
  -H 'Authorization: Bearer <accessToken>'
```

```json
[
  {"bucketStart": "2026-11-03T10:10:00Z", "avgCount": 6.4, "peakCount": 11},
  {"bucketStart": "2026-11-03T10:05:00Z", "avgCount": 5.0, "peakCount": 8}
]
```

Each bucket is 5 minutes. Newest first. `limit` is 1 to 288. The default, 12, is
one hour. 288 is 24 hours. An unknown zone returns `[]`, not 404.

### Live push (WebSocket)

`WS /api/v1/crowd/live`, with the header `Authorization: Bearer <accessToken>`.

The server sends one message each time the camera program reports counts:

```json
{"cameraId": "default", "counts": {"z1": 7, "z2": 3}}
```

- Messages go to **every** connected client. Pick out the zones you care about.
- Anything the client sends is ignored.
- A missing or bad token closes the socket with code `1008`.
- There is no replay. After a reconnect, call `GET /zones/{zoneId}/crowd` once.
- Whether the iOS app uses this socket today: **TBD**. Polling `/crowd` also works.

## 7. Broadcasts: the dispatch flow

A manager asks for staff in a zone. Floor staff accept. When the job is done,
each one completes. Then the manager finishes it.

```
manager: POST /broadcasts ──> status "open"  (push to all floor staff)
                                  │
   staff: POST /broadcasts/{id}/accept   (repeat until staffNeeded is reached)
                                  │
                      20 seconds after creation ──> status "closed"
                                  │
   staff: POST /broadcasts/{id}/complete   (each accepted staff member)
                                  │
manager: POST /broadcasts/{id}/finish ──> status "finished"
```

| Status | Meaning |
|---|---|
| `open` | Staff can still accept |
| `closed` | The 20 seconds ran out. Accepted staff can still complete |
| `finished` | The manager closed it |

A broadcast lives **20 seconds** (`expiresAt`). The server changes it to
`closed` the next time someone reads or changes broadcasts. There is no
background timer, so always read `expiresAt` and `status` from the response.

### The broadcast object

```json
{
  "broadcastId": "7d8e1c34-5d0b-4f0e-8e0d-1a7f0f6b2c11",
  "zoneId": "z1",
  "status": "open",
  "staffNeeded": 2,
  "expiresAt": "2026-11-03T10:16:10Z",
  "acceptedCount": 1,
  "completedCount": 0,
  "acceptedStaff": [
    {"staffId": "a1b2…", "name": "Sam Staff", "completedAt": null}
  ]
}
```

### Manager: create (`ops_manager`)

```bash
curl -X POST http://localhost:8080/api/v1/broadcasts \
  -H 'Authorization: Bearer <accessToken>' \
  -H 'Content-Type: application/json' \
  -d '{"zoneId": "z1", "staffNeeded": 2}'
```

Returns `201` with the broadcast object. `staffNeeded` must be above 0.
Errors: `404` unknown zone, `409 broadcast_open` if the zone already has an
open broadcast.

### Anyone: list and find the open one

```bash
curl 'http://localhost:8080/api/v1/broadcasts?zoneId=z1&status=open' \
  -H 'Authorization: Bearer <accessToken>'
```

Returns a list, newest first. Filters are optional: `zoneId`, `status`
(`open`, `closed`, `finished`), `limit` (1 to 50, default 20). An empty list
means nothing is open.

### Staff: accept (`floor_staff`)

```bash
curl -X POST http://localhost:8080/api/v1/broadcasts/<broadcastId>/accept \
  -H 'Authorization: Bearer <accessToken>'
```

Returns the updated broadcast. Calling it twice as the same user is safe.
Errors: `409 expired`, `409 already_delegating`, `409 slots_full`.

### Staff: complete (`floor_staff`)

```bash
curl -X POST http://localhost:8080/api/v1/broadcasts/<broadcastId>/complete \
  -H 'Authorization: Bearer <accessToken>'
```

Returns the updated broadcast, with your `completedAt` set. Only a staff member
who accepted it can complete it (`403 not_your_response`).

### Manager: finish (`ops_manager`)

```bash
curl -X POST http://localhost:8080/api/v1/broadcasts/<broadcastId>/finish \
  -H 'Authorization: Bearer <accessToken>'
```

```json
{"broadcastId": "7d8e…", "status": "finished", "finishedAt": "2026-11-03T10:18:00Z"}
```

Works only after every accepted staff member completed (`409 not_all_done`).
It works on both `open` and `closed` broadcasts.

## 8. Staff lookup

```bash
curl http://localhost:8080/api/v1/staff/<staffId> -H 'Authorization: Bearer <accessToken>'
```

```json
{"staffId": "a1b2…", "name": "Sam Staff", "status": "idle"}
```

`status` is `idle` or `delegating` (on a broadcast). `staffId` is a user id. The
server does not check that this user is `floor_staff`. A bad id format gives
`422`.

## 9. A Swift sketch

An example of the pattern only. It is not code from this repo.

```swift
struct APIError: Decodable { struct Body: Decodable { let code: String; let message: String }; let error: Body }

func call(_ path: String, method: String = "GET", body: Data? = nil) async throws -> Data {
    var request = URLRequest(url: baseURL.appendingPathComponent(path))
    request.httpMethod = method
    request.setValue("Bearer \(tokens.access)", forHTTPHeaderField: "Authorization")
    if let body { request.httpBody = body; request.setValue("application/json", forHTTPHeaderField: "Content-Type") }

    var (data, response) = try await URLSession.shared.data(for: request)
    if (response as? HTTPURLResponse)?.statusCode == 401 {
        try await refreshTokens()                       // POST /auth/refresh, save the NEW refresh token
        request.setValue("Bearer \(tokens.access)", forHTTPHeaderField: "Authorization")
        (data, response) = try await URLSession.shared.data(for: request)
    }
    return data   // on non-2xx, decode APIError and switch on error.code
}
```

Decode dates with `ISO8601DateFormatter`. Whether the app's current date parser
accepts the `Z` form: **TBD**.

## 10. Try it without the app

The FastAPI docs page lists every route and lets you call it. Open
`http://localhost:8080/docs`, press **Authorize**, and paste an access token.
To get test users, run `uv run --env-file .env python scripts/seed_dispatch.py`
(see [DEVELOPMENT.md](DEVELOPMENT.md) and [SETUP_AUTH.md](SETUP_AUTH.md)). Start the
server with `make api`.
