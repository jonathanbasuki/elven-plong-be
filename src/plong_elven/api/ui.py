"""Serves a small per-camera browser page: live MJPEG view + live counts + a canvas-based
zone editor backed by GET/PUT /zones/{camera_id}. Plain HTML/JS, no build step/dependency.
"""

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter(tags=["ui"])

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
  .tab { display: none; }
  .tab.active { display: block; }
  #stream, #snapshot { max-width: 100%; display: block; border: 1px solid #333; }
  .stage { position: relative; display: inline-block; }
  #overlay { position: absolute; top: 0; left: 0; max-width: 100%; }
  .row { margin: 10px 0; display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
  button, select { background: #222; color: #eee; border: 1px solid #444; padding: 6px 10px;
    border-radius: 4px; cursor: pointer; }
  button:hover { border-color: #666; }
  ul#zone-list { list-style: none; padding: 0; }
  ul#zone-list li { display: flex; gap: 8px; align-items: center; padding: 4px 0; }
  #status { color: #9c9; min-height: 20px; }
  #counts { font-size: 14px; }
  #counts span { display: inline-block; margin-right: 16px; padding: 4px 10px;
    background: #222; border-radius: 4px; }
</style>
</head>
<body>
<header>
  <h1>Camera: __CAMERA_ID__</h1>
  <nav>
    <button id="tab-live-btn" class="active">Live Feed</button>
    <button id="tab-edit-btn">Edit Zones</button>
  </nav>
</header>
<main>
  <section id="tab-live" class="tab active">
    <img id="stream" alt="live stream">
    <div class="row" id="counts"></div>
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
</main>
<script>
const CAMERA_ID = "__CAMERA_ID__";
const KEY = new URLSearchParams(location.search).get("key") || "";
const qs = KEY ? "?key=" + encodeURIComponent(KEY) : "";
const authHeaders = KEY ? {"X-Internal-Api-Key": KEY} : {};

document.getElementById("stream").src = `/stream/${CAMERA_ID}${qs}`;

// --- tabs ---
const tabs = {live: document.getElementById("tab-live"), edit: document.getElementById("tab-edit")};
const tabBtns = {live: document.getElementById("tab-live-btn"), edit: document.getElementById("tab-edit-btn")};
function showTab(name) {
  for (const k in tabs) { tabs[k].classList.toggle("active", k === name); tabBtns[k].classList.toggle("active", k === name); }
}
tabBtns.live.onclick = () => showTab("live");
tabBtns.edit.onclick = () => showTab("edit");

// --- live counts via websocket ---
let zoneNames = {};
const wsProto = location.protocol === "https:" ? "wss" : "ws";
const ws = new WebSocket(`${wsProto}://${location.host}/ws/counts`);
ws.onmessage = (ev) => {
  const msg = JSON.parse(ev.data);
  if (msg.camera_id !== CAMERA_ID) return;
  const el = document.getElementById("counts");
  el.innerHTML = Object.entries(msg.counts)
    .map(([id, n]) => `<span>${zoneNames[id] || id}: ${n}</span>`).join("");
};

// --- zone editor state ---
let zones = [];       // [{id, name, polygon: [[x,y]...]}] normalized 0..1
let current = [];     // points for the zone being drawn, normalized 0..1
let anchor = "bottom_center";
const snapshot = document.getElementById("snapshot");
const overlay = document.getElementById("overlay");
const ctx = overlay.getContext("2d");
const status = document.getElementById("status");
const colors = ["#e33", "#39f", "#3b7", "#fb3", "#b3f", "#3bf"];

function syncZoneNames() { zoneNames = {}; zones.forEach(z => zoneNames[z.id] = z.name); }

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
  const resp = await fetch(`/frames/${CAMERA_ID}/latest${qs}`);
  if (!resp.ok) { status.textContent = `capture failed: ${resp.status} (is the cctv worker running?)`; return; }
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
  const resp = await fetch(`/zones/${CAMERA_ID}`, {
    method: "PUT",
    headers: {"Content-Type": "application/json", ...authHeaders},
    body: JSON.stringify({anchor, zones}),
  });
  const body = await resp.json();
  status.textContent = resp.ok ? `saved ${body.zones} zone(s)` : `save failed: ${body.detail || resp.status}`;
};

(async () => {
  const resp = await fetch(`/zones/${CAMERA_ID}${qs}`);
  if (resp.ok) {
    const cfg = await resp.json();
    zones = cfg.zones || [];
    anchor = cfg.anchor || "bottom_center";
    document.getElementById("anchor").value = anchor;
    syncZoneNames(); renderZoneList();
  }
})();
</script>
</body>
</html>
"""


@router.get("/ui/{camera_id}", response_class=HTMLResponse)
async def ui(camera_id: str) -> str:
    return _PAGE.replace("__CAMERA_ID__", camera_id)
