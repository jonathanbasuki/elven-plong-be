"""Dev-only camera page and its backend, under /dev.

create_app() registers this router only when APP_ENV=dev (§6.1). Nothing here
exists in production. The page itself (/dev/cameras/{cameraId}) needs no login: a
browser navigation cannot send a bearer header, and the login form is on the page
(§6.5). It holds no data. Every API call the page makes needs an admin access
token, kept in a JS variable (never localStorage):

- POST /dev/cameras/{cameraId}/detections: manual counts.
- POST /dev/tickets: 60-second tickets for the stream and the WebSocket.
- /dev/cameras/{cameraId}/layout, /snapshot (admin token) and /stream (a
  `stream:<cameraId>` ticket, because an <img> cannot send headers; the internal
  key is never accepted).

# ponytail: frames live in in-memory dicts and every viewer re-reads the latest
# one. Fine for one API process; swap for Redis if this ever runs behind
# multiple uvicorn workers.

Plain HTML/JS, no build step or dependency.
"""

import asyncio
import html
import json
from collections.abc import AsyncIterator

from fastapi import APIRouter, BackgroundTasks, Depends, Path, Query, Response
from fastapi.responses import HTMLResponse, StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from plong_elven.api.deps import (
    get_alert_service,
    get_camera_service,
    get_db,
    get_session_factory,
    require_role,
)
from plong_elven.api.routers.crowd import ingest_counts
from plong_elven.db.models import User
from plong_elven.schemas.common import CAMERA_ID_PATTERN
from plong_elven.schemas.detections import DetectionIn
from plong_elven.schemas.layout import ZonesIn
from plong_elven.schemas.tickets import TicketOut, TicketRequest
from plong_elven.services.auth_service import AuthService
from plong_elven.services.camera_service import CameraService
from plong_elven.services.errors import AppError
from plong_elven.services.internal.alert_service import AlertService
from plong_elven.state.live_state import LiveState, get_live_state

BOUNDARY = b"frame"
STREAM_INTERVAL = 0.2  # seconds between re-sent frames to each viewer

camera_router = APIRouter(prefix="/dev/cameras/{cameraId}")
tickets_router = APIRouter(prefix="/dev")
router = APIRouter(tags=["dev"])  # routers are included at the bottom of this file

_PAGE = """<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>Plong Elven - __CAMERA_ID__</title>
<style>
  body { font-family: system-ui, sans-serif; margin: 0; background: #111; color: #eee; }
  header { padding: 12px 16px; background: #1b1b1b; display: flex; gap: 12px; align-items: center; }
  header h1 { font-size: 16px; margin: 0; flex: 1; }
  nav button { background: #222; color: #eee; border: 1px solid #444; padding: 6px 14px;
    border-radius: 4px; cursor: pointer; }
  nav button.active { background: #3b7; color: #111; border-color: #3b7; }
  main { padding: 16px; max-width: 900px; margin: 0 auto; }
  .hidden { display: none !important; }
  .tab { display: none; }
  .tab.active { display: block; }
  #stream, #snapshot { max-width: 100%; display: block; border: 1px solid #333; }
  .stage { position: relative; display: inline-block; }
  #overlay { position: absolute; top: 0; left: 0; max-width: 100%; }
  .row { margin: 10px 0; display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
  button, select, input { background: #222; color: #eee; border: 1px solid #444;
    padding: 6px 10px; border-radius: 4px; }
  button { cursor: pointer; }
  button:hover { border-color: #666; }
  ul#zone-list { list-style: none; padding: 0; }
  ul#zone-list li { display: flex; gap: 8px; align-items: center; padding: 4px 0; }
  #status, #live-status, #login-status { color: #9c9; min-height: 20px; }
  #counts { font-size: 14px; }
  #counts span { display: inline-block; margin-right: 16px; padding: 4px 10px;
    background: #222; border-radius: 4px; }
</style>
</head>
<body>
<header>
  <h1>Camera: __CAMERA_ID__ (dev)</h1>
  <nav id="nav" class="hidden">
    <button id="tab-live-btn" class="active">Live Feed</button>
    <button id="tab-edit-btn">Edit Zones</button>
    <button id="tab-dev-btn">Dev Tools</button>
    <button id="logout">Log out</button>
  </nav>
</header>
<main>
  <section id="login">
    <p>Log in with an admin account.</p>
    <form id="login-form">
      <div class="row"><input id="email" type="email" placeholder="email" required></div>
      <div class="row"><input id="password" type="password" placeholder="password" required></div>
      <div class="row"><button type="submit">Log in</button></div>
    </form>
    <div id="login-status"></div>
  </section>

  <div id="app" class="hidden">
  <section id="tab-live" class="tab active">
    <div class="row"><button id="reload-stream">Reload stream</button></div>
    <img id="stream" alt="live stream">
    <div class="row" id="counts"></div>
    <div id="live-status"></div>
  </section>

  <section id="tab-edit" class="tab">
    <div class="row">
      <button id="capture">Capture frame</button>
      <select id="anchor">
        <option value="bottom_center">bottom_center</option>
        <option value="center">center</option>
        <option value="top_center">top_center</option>
      </select>
      <button id="finish-zone">Finish zone (&gt;=3 points)</button>
      <button id="undo">Undo</button>
      <button id="save">Save</button>
    </div>
    <div class="stage">
      <img id="snapshot" alt="captured frame">
      <canvas id="overlay"></canvas>
    </div>
    <ul id="zone-list"></ul>
    <div id="status"></div>
  </section>

  <section id="tab-dev" class="tab">
    <p>Manually set a zone's detected crowd count, the same way the cctv worker's
    counts are recorded. Lets you drive the frontend's Safe/Warning/Crowded states
    without real people in frame.</p>
    <p style="color:#fb3">If the cctv worker is running at the same time, it will overwrite
    this within ~1s (it posts a real count every second). Stop the worker first to keep a
    manual value in place.</p>
    <div class="row">
      <select id="dev-zone"></select>
      <input id="dev-count" type="number" min="0" value="0" style="width:80px">
      <button id="dev-set">Set count</button>
    </div>
    <div id="dev-status"></div>
  </section>
  </div>
</main>
<script>
const CAMERA_ID = "__CAMERA_ID__";
let accessToken = null;  // in memory only, never persisted in the browser
let ws = null;

const loginBox = document.getElementById("login");
const appBox = document.getElementById("app");
const nav = document.getElementById("nav");
const loginStatus = document.getElementById("login-status");
const liveStatus = document.getElementById("live-status");
const stream = document.getElementById("stream");

function showLogin(message) {
  accessToken = null;
  if (ws) { ws.onclose = null; ws.close(); ws = null; }
  stream.removeAttribute("src");
  appBox.classList.add("hidden");
  nav.classList.add("hidden");
  loginBox.classList.remove("hidden");
  loginStatus.textContent = message || "";
}

async function errorMessage(resp) {
  try {
    const body = await resp.json();
    if (body && body.error) return body.error.message;
  } catch (e) { /* not JSON */ }
  return `HTTP ${resp.status}`;
}

// Bearer-authenticated fetch. Returns null after a 401 (the login form is shown).
async function api(path, options = {}) {
  const headers = {...(options.headers || {}), "Authorization": "Bearer " + accessToken};
  const resp = await fetch(path, {...options, headers});
  if (resp.status === 401) {
    showLogin("Session expired or not authorized. Log in again.");
    return null;
  }
  return resp;
}

function postJson(path, payload) {
  return api(path, {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify(payload),
  });
}

async function mintTicket(scope, statusEl) {
  const resp = await postJson("/dev/tickets", {scope});
  if (!resp) return null;
  if (!resp.ok) { statusEl.textContent = `ticket failed: ${await errorMessage(resp)}`; return null; }
  return (await resp.json()).ticket;
}

document.getElementById("login-form").onsubmit = async (ev) => {
  ev.preventDefault();
  loginStatus.textContent = "logging in...";
  const resp = await fetch("/api/v1/auth/login", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({
      email: document.getElementById("email").value,
      password: document.getElementById("password").value,
    }),
  });
  if (!resp.ok) { loginStatus.textContent = `login failed: ${await errorMessage(resp)}`; return; }
  const body = await resp.json();
  if (body.user.role !== "admin") { showLogin("This page needs an admin account."); return; }
  accessToken = body.accessToken;
  document.getElementById("password").value = "";
  loginStatus.textContent = "";
  loginBox.classList.add("hidden");
  appBox.classList.remove("hidden");
  nav.classList.remove("hidden");
  await loadLayout();
  await startStream();
  await startLive();
};

document.getElementById("logout").onclick = () => showLogin("Logged out.");

// --- tabs ---
const tabs = {live: document.getElementById("tab-live"), edit: document.getElementById("tab-edit"), dev: document.getElementById("tab-dev")};
const tabBtns = {live: document.getElementById("tab-live-btn"), edit: document.getElementById("tab-edit-btn"), dev: document.getElementById("tab-dev-btn")};
function showTab(name) {
  for (const k in tabs) { tabs[k].classList.toggle("active", k === name); tabBtns[k].classList.toggle("active", k === name); }
}
tabBtns.live.onclick = () => showTab("live");
tabBtns.edit.onclick = () => showTab("edit");
tabBtns.dev.onclick = () => showTab("dev");

// --- live stream via a stream:<cameraId> ticket ---
async function startStream() {
  const ticket = await mintTicket("stream:" + CAMERA_ID, liveStatus);
  if (!ticket) return;
  stream.onerror = () => { liveStatus.textContent = "stream unavailable (is the cctv worker running?)"; };
  stream.onload = () => { liveStatus.textContent = ""; };
  stream.src = `/dev/cameras/${CAMERA_ID}/stream?ticket=${encodeURIComponent(ticket)}`;
}
document.getElementById("reload-stream").onclick = startStream;

// --- live counts via a crowd:live ticket ---
let zoneNames = {};
async function startLive() {
  const ticket = await mintTicket("crowd:live", liveStatus);
  if (!ticket) return;
  const wsProto = location.protocol === "https:" ? "wss" : "ws";
  ws = new WebSocket(`${wsProto}://${location.host}/api/v1/crowd/live?ticket=${encodeURIComponent(ticket)}`);
  ws.onmessage = (ev) => {
    const msg = JSON.parse(ev.data);
    if (msg.cameraId !== CAMERA_ID) return;
    const el = document.getElementById("counts");
    el.innerHTML = Object.entries(msg.counts)
      .map(([id, n]) => `<span>${zoneNames[id] || id}: ${n}</span>`).join("");
  };
  ws.onclose = () => { liveStatus.textContent = "live counts disconnected"; ws = null; };
}

// --- zone editor state ---
let zones = [];       // [{id, name, polygon: [[x,y]...]}] normalized 0..1
let current = [];     // points for the zone being drawn, normalized 0..1
let anchor = "bottom_center";
const snapshot = document.getElementById("snapshot");
const overlay = document.getElementById("overlay");
const ctx = overlay.getContext("2d");
const status = document.getElementById("status");
const colors = ["#e33", "#39f", "#3b7", "#fb3", "#b3f", "#3bf"];

function syncZoneNames() { zoneNames = {}; zones.forEach(z => zoneNames[z.id] = z.name); renderDevZones(); }

function renderDevZones() {
  const sel = document.getElementById("dev-zone");
  const prev = sel.value;
  sel.innerHTML = zones.map(z => `<option value="${z.id}">${z.id} (${z.name})</option>`).join("");
  if (zones.some(z => z.id === prev)) sel.value = prev;
}

function redraw() {
  if (!snapshot.naturalWidth) return;
  overlay.width = snapshot.naturalWidth;
  overlay.height = snapshot.naturalHeight;
  ctx.clearRect(0, 0, overlay.width, overlay.height);
  const drawPoly = (pts, color, label) => {
    if (pts.length === 0) return;
    if (pts.length >= 2) {
      ctx.strokeStyle = color; ctx.lineWidth = 3;
      ctx.beginPath();
      pts.forEach(([x, y], i) => {
        const px = x * overlay.width, py = y * overlay.height;
        i === 0 ? ctx.moveTo(px, py) : ctx.lineTo(px, py);
      });
      if (pts.length > 2) ctx.closePath();
      ctx.stroke();
    }
    pts.forEach(([x, y]) => {
      ctx.fillStyle = color;
      ctx.beginPath(); ctx.arc(x * overlay.width, y * overlay.height, 4, 0, Math.PI * 2); ctx.fill();
    });
    if (label) {
      const [cx, cy] = pts.reduce((a, p) => [a[0] + p[0], a[1] + p[1]], [0, 0]).map(v => v / pts.length);
      ctx.fillStyle = color; ctx.font = "16px sans-serif";
      ctx.fillText(label, cx * overlay.width, cy * overlay.height);
    }
  };
  zones.forEach((z, i) => drawPoly(z.polygon, colors[i % colors.length], z.name));
  drawPoly(current, "#fff", null);
}

function renderZoneList() {
  const ul = document.getElementById("zone-list");
  ul.innerHTML = "";
  zones.forEach((z, i) => {
    const li = document.createElement("li");
    li.innerHTML = `<span style="color:${colors[i % colors.length]}">&#9632;</span> ${z.name} (${z.polygon.length} pts) `;
    const del = document.createElement("button");
    del.textContent = "Delete";
    del.onclick = () => { zones.splice(i, 1); syncZoneNames(); renderZoneList(); redraw(); };
    li.appendChild(del);
    ul.appendChild(li);
  });
}

document.getElementById("capture").onclick = async () => {
  status.textContent = "capturing...";
  const resp = await api(`/dev/cameras/${CAMERA_ID}/snapshot`);
  if (!resp) return;
  if (!resp.ok) { status.textContent = `capture failed: ${await errorMessage(resp)}`; return; }
  const blob = await resp.blob();
  snapshot.onload = () => { redraw(); status.textContent = "frame captured"; };
  snapshot.src = URL.createObjectURL(blob);
};

overlay.addEventListener("click", (ev) => {
  const rect = overlay.getBoundingClientRect();
  const x = (ev.clientX - rect.left) / rect.width;
  const y = (ev.clientY - rect.top) / rect.height;
  current.push([Math.round(x * 10000) / 10000, Math.round(y * 10000) / 10000]);
  redraw();
});

document.getElementById("finish-zone").onclick = () => {
  if (current.length < 3) { status.textContent = "need at least 3 points"; return; }
  const n = zones.length + 1;
  zones.push({id: `z${n}`, name: `Zone ${n}`, polygon: current});
  current = [];
  syncZoneNames(); renderZoneList(); redraw();
};

document.getElementById("undo").onclick = () => {
  if (current.length) current.pop();
  else zones.pop();
  syncZoneNames(); renderZoneList(); redraw();
};

document.getElementById("anchor").onchange = (ev) => { anchor = ev.target.value; };

document.getElementById("save").onclick = async () => {
  status.textContent = "saving...";
  const resp = await api(`/dev/cameras/${CAMERA_ID}/layout`, {
    method: "PUT",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({anchor, zones}),
  });
  if (!resp) return;
  if (!resp.ok) { status.textContent = `save failed: ${await errorMessage(resp)}`; return; }
  const body = await resp.json();
  status.textContent = `saved ${body.zones} zone(s)`;
};

document.getElementById("dev-set").onclick = async () => {
  const devStatus = document.getElementById("dev-status");
  const zoneId = document.getElementById("dev-zone").value;
  const count = parseInt(document.getElementById("dev-count").value, 10) || 0;
  if (!zoneId) { devStatus.textContent = "no zones loaded yet"; return; }
  devStatus.textContent = "setting...";
  const resp = await postJson(`/dev/cameras/${CAMERA_ID}/detections`, {counts: {[zoneId]: count}});
  if (!resp) return;
  devStatus.textContent = resp.ok ? `${zoneId} set to ${count}` : `failed: ${await errorMessage(resp)}`;
};

async function loadLayout() {
  const resp = await api(`/dev/cameras/${CAMERA_ID}/layout`);
  if (!resp) return;
  if (!resp.ok) { status.textContent = `layout failed: ${await errorMessage(resp)}`; return; }
  const cfg = await resp.json();
  zones = cfg.zones || [];
  anchor = cfg.anchor || "bottom_center";
  document.getElementById("anchor").value = anchor;
  syncZoneNames(); renderZoneList();
}
</script>
</body>
</html>
"""


def render_page(camera_id: str) -> str:
    """Substitute camera_id into the page, escaped for each context.

    The route already restricts camera_id to CAMERA_ID_PATTERN; this is defence
    in depth. JS context: json.dumps, with <, > and & as \\u escapes so a value
    containing "</script>" cannot close the script block. HTML context:
    html.escape.
    """
    js_value = (
        json.dumps(camera_id)
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
    )
    page = _PAGE.replace('"__CAMERA_ID__"', js_value)
    return page.replace("__CAMERA_ID__", html.escape(camera_id))


@camera_router.get("", response_class=HTMLResponse)
async def dev_page(cameraId: str = Path(pattern=CAMERA_ID_PATTERN)) -> str:
    """The dev camera page. No login: the login form is on the page (dev only)."""
    return render_page(cameraId)


@camera_router.post("/detections")
async def post_manual_count(
    detection: DetectionIn,
    background_tasks: BackgroundTasks,
    cameraId: str = Path(pattern=CAMERA_ID_PATTERN),
    _: User = Depends(require_role("admin")),  # noqa: B008
    live: LiveState = Depends(get_live_state),  # noqa: B008
    factory: async_sessionmaker[AsyncSession] = Depends(get_session_factory),  # noqa: B008
    alerts: AlertService = Depends(get_alert_service),  # noqa: B008
) -> dict:
    """Manual count from the dev page; same ingest path as the worker route."""
    await ingest_counts(
        cameraId, detection.counts, background_tasks, live, factory, alerts
    )
    return {"status": "ok"}


@tickets_router.post("/tickets", response_model=TicketOut)
async def mint_ticket(
    body: TicketRequest,
    user: User = Depends(require_role("admin")),  # noqa: B008
    session: AsyncSession = Depends(get_db),  # noqa: B008
) -> TicketOut:
    """Mint a short-lived ticket JWT for the dev page's stream and WebSocket.

    Requires admin role. Returns a ticket valid for 60 seconds, accepted only
    as ?ticket= on the stream and WebSocket endpoints for the given scope.
    """
    return AuthService(session).mint_ticket(user, body.scope)


async def _stream_ticket_user(
    cameraId: str = Path(pattern=CAMERA_ID_PATTERN),
    ticket: str | None = Query(default=None),
    factory: async_sessionmaker[AsyncSession] = Depends(get_session_factory),  # noqa: B008
) -> User:
    """Authenticate the stream by a `stream:<cameraId>` ticket.

    The session is opened and closed here, so an open stream does not hold a
    pool connection for its whole life (same reason as P5).
    """
    if not ticket:
        raise AppError(401, "unauthenticated", "Missing ticket")
    async with factory() as session:
        return await AuthService(session).verify_ticket(ticket, f"stream:{cameraId}")


@camera_router.get("/layout")
async def get_layout(
    cameraId: str = Path(pattern=CAMERA_ID_PATTERN),
    _: User = Depends(require_role("admin")),  # noqa: B008
    svc: CameraService = Depends(get_camera_service),  # noqa: B008
) -> dict:
    """The shared zone layout (one file for all cameras today)."""
    return svc.get_layout()


@camera_router.put("/layout")
async def put_layout(
    body: ZonesIn,
    cameraId: str = Path(pattern=CAMERA_ID_PATTERN),
    _: User = Depends(require_role("admin")),  # noqa: B008
    svc: CameraService = Depends(get_camera_service),  # noqa: B008
) -> dict:
    count = svc.save_layout(body.anchor, [z.model_dump() for z in body.zones])
    return {"status": "ok", "zones": count}


@camera_router.get("/snapshot")
async def get_snapshot(
    cameraId: str = Path(pattern=CAMERA_ID_PATTERN),
    _: User = Depends(require_role("admin")),  # noqa: B008
    svc: CameraService = Depends(get_camera_service),  # noqa: B008
) -> Response:
    """The latest unannotated frame, for the zone editor's 'capture frame' step."""
    return Response(
        content=svc.latest_frame(cameraId, raw=True), media_type="image/jpeg"
    )


@camera_router.get("/stream")
async def get_stream(
    cameraId: str = Path(pattern=CAMERA_ID_PATTERN),
    _: User = Depends(_stream_ticket_user),  # noqa: B008
    live: LiveState = Depends(get_live_state),  # noqa: B008
    svc: CameraService = Depends(get_camera_service),  # noqa: B008
) -> StreamingResponse:
    """Annotated frames as multipart/x-mixed-replace for a browser <img>."""
    svc.latest_frame(cameraId, raw=False)  # 404 before the first frame

    async def frames() -> AsyncIterator[bytes]:
        while True:
            jpeg = live.frames[cameraId]
            yield (
                b"--" + BOUNDARY + b"\r\n"
                b"Content-Type: image/jpeg\r\n"
                b"Content-Length: "
                + str(len(jpeg)).encode()
                + b"\r\n\r\n"
                + jpeg
                + b"\r\n"
            )
            await asyncio.sleep(STREAM_INTERVAL)

    return StreamingResponse(
        frames(), media_type=f"multipart/x-mixed-replace; boundary={BOUNDARY.decode()}"
    )


router.include_router(camera_router)
router.include_router(tickets_router)
