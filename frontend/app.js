"use strict";

const API_BASE = "/api/v1";

// ── Map setup ──────────────────────────────────────────────────────────────
const map = L.map("map").setView([0, -55], 4);

L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
  attribution: "© OpenStreetMap contributors",
  maxZoom: 18,
}).addTo(map);

// ── Bbox drawing ───────────────────────────────────────────────────────────
let drawState = null;   // { startLatLng, rect }
let currentRect = null; // L.Rectangle on map
let currentBbox = null; // { min_lon, min_lat, max_lon, max_lat }
const bboxDisplay = document.getElementById("bbox-display");
const drawHint = document.getElementById("draw-hint");
const mapEl = document.getElementById("map");

map.on("mousedown", (e) => {
  if (!e.originalEvent.shiftKey) return;
  e.originalEvent.preventDefault();

  if (currentRect) { currentRect.remove(); currentRect = null; }
  mapEl.classList.add("drawing");
  map.dragging.disable();

  drawState = { startLatLng: e.latlng };
  drawState.rect = L.rectangle(
    [e.latlng, e.latlng],
    { color: "#63b3ed", weight: 2, fillOpacity: 0.15 }
  ).addTo(map);
});

map.on("mousemove", (e) => {
  if (!drawState) return;
  drawState.rect.setBounds([drawState.startLatLng, e.latlng]);
});

map.on("mouseup", (e) => {
  if (!drawState) return;
  map.dragging.enable();
  mapEl.classList.remove("drawing");

  const bounds = drawState.rect.getBounds();
  drawState = null;
  currentRect = bounds ? L.rectangle(bounds, { color: "#63b3ed", weight: 2, fillOpacity: 0.15 }) : null;
  if (currentRect) currentRect.addTo(map);

  if (!bounds) return;
  currentBbox = {
    min_lon: bounds.getWest(),
    min_lat: bounds.getSouth(),
    max_lon: bounds.getEast(),
    max_lat: bounds.getNorth(),
  };

  const fmt = (v) => v.toFixed(4);
  bboxDisplay.textContent =
    `SW: ${fmt(currentBbox.min_lat)}°N, ${fmt(currentBbox.min_lon)}°E  ` +
    `NE: ${fmt(currentBbox.max_lat)}°N, ${fmt(currentBbox.max_lon)}°E`;
  drawHint.style.display = "none";
});

// ── Analysis trigger ───────────────────────────────────────────────────────
const analyzeBtn = document.getElementById("analyze-btn");
const statusBar  = document.getElementById("status-bar");

async function triggerAnalysis() {
  const name       = document.getElementById("area-name").value.trim();
  const dateBefore = document.getElementById("date-before").value;
  const dateAfter  = document.getElementById("date-after").value;

  if (!name)        return setStatus("error", "Please enter an area name.");
  if (!currentBbox) return setStatus("error", "Please draw a bounding box on the map.");
  if (!dateBefore)  return setStatus("error", "Please select the 'before' date.");
  if (!dateAfter)   return setStatus("error", "Please select the 'after' date.");
  if (dateBefore >= dateAfter) return setStatus("error", "Date Before must be earlier than Date After.");

  analyzeBtn.disabled = true;
  setStatus("loading", "Creating area…");

  try {
    // Create area
    const areaRes = await apiFetch("POST", "/areas", {
      name,
      ...currentBbox,
    });
    if (!areaRes.ok) {
      const err = await areaRes.json().catch(() => ({}));
      throw new Error(err.detail || `Area creation failed (${areaRes.status})`);
    }
    const area = await areaRes.json();

    setStatus("loading", "Triggering analysis pipeline (may take 1-3 minutes)…");

    // Trigger analysis
    const anlRes = await apiFetch("POST", "/analyses", {
      area_id:     area.id,
      date_before: dateBefore,
      date_after:  dateAfter,
    });
    if (!anlRes.ok) {
      const err = await anlRes.json().catch(() => ({}));
      throw new Error(err.detail || `Analysis trigger failed (${anlRes.status})`);
    }
    const analysis = await anlRes.json();

    setStatus("loading", `Processing… (analysis id: ${analysis.id.slice(0, 8)}…)`);

    // Poll until done or error
    await pollAnalysis(analysis.id);

  } catch (err) {
    setStatus("error", err.message);
  } finally {
    analyzeBtn.disabled = false;
  }
}

async function pollAnalysis(analysisId) {
  const POLL_INTERVAL_MS = 5000;
  const MAX_POLLS = 120; // 10 minutes max

  for (let i = 0; i < MAX_POLLS; i++) {
    await sleep(POLL_INTERVAL_MS);
    const res = await apiFetch("GET", `/analyses/${analysisId}`);
    const analysis = await res.json();

    if (analysis.status === "done") {
      setStatus("ok", "Analysis complete! Loading alerts…");
      await loadAlerts();
      return;
    }
    if (analysis.status === "error") {
      throw new Error(`Pipeline error: ${analysis.error_message || "unknown"}`);
    }
    setStatus("loading", `Still processing… (${(i + 1) * 5}s elapsed)`);
  }
  throw new Error("Analysis timed out after 10 minutes.");
}

// ── Alerts loading ─────────────────────────────────────────────────────────
async function loadAlerts() {
  const res = await apiFetch("GET", "/alerts");
  if (!res.ok) { setStatus("error", "Could not load alerts."); return; }
  const alerts = await res.json();
  renderAlerts(alerts);
  overlayAlerts(alerts);
}

function renderAlerts(alerts) {
  const list = document.getElementById("alerts-list");
  list.innerHTML = "";

  if (!alerts.length) {
    list.innerHTML = `<p style="color:#718096;font-size:13px;padding:8px 0;">No alerts yet.</p>`;
    return;
  }

  for (const alert of alerts) {
    const card = document.createElement("div");
    card.className = "alert-card";
    card.dataset.severity = alert.severity;
    card.dataset.alertId  = alert.id;

    const date = new Date(alert.created_at).toLocaleDateString();
    card.innerHTML = `
      <div class="alert-header">
        <span class="badge badge-severity-${alert.severity}">${alert.severity}</span>
        <span class="badge badge-type">${alert.change_type.replace("_", " ")}</span>
        <span class="alert-area">${date}</span>
      </div>
      <div class="alert-description">${escapeHtml(alert.description)}</div>
      <div class="alert-meta">Confidence: ${Math.round(alert.confidence * 100)}%</div>
    `;

    list.appendChild(card);
  }
}

// Overlay coloured rectangles on the map for each alert (requires area bbox)
const areaCache = {};
async function overlayAlerts(alerts) {
  for (const alert of alerts) {
    const area = await getArea(alert.area_id);
    if (!area) continue;

    const color = {
      low:      "#68d391",
      medium:   "#f6e05e",
      high:     "#f6ad55",
      critical: "#fc8181",
    }[alert.severity] || "#a0aec0";

    L.rectangle(
      [[area.min_lat, area.min_lon], [area.max_lat, area.max_lon]],
      { color, weight: 2, fillOpacity: 0.2, fillColor: color }
    )
      .bindPopup(
        `<strong>${alert.change_type.replace("_", " ")}</strong> (${alert.severity})<br/>` +
        `${escapeHtml(alert.description)}`
      )
      .addTo(map);
  }
}

async function getArea(areaId) {
  if (areaCache[areaId]) return areaCache[areaId];
  const res = await apiFetch("GET", `/areas/${areaId}`);
  if (!res.ok) return null;
  const area = await res.json();
  areaCache[areaId] = area;
  return area;
}

// ── Utilities ──────────────────────────────────────────────────────────────
function apiFetch(method, path, body) {
  const opts = {
    method,
    headers: { "Content-Type": "application/json" },
  };
  if (body) opts.body = JSON.stringify(body);
  return fetch(API_BASE + path, opts);
}

function setStatus(type, msg) {
  if (type === "loading") {
    statusBar.innerHTML = `<div class="spinner"></div><span>${escapeHtml(msg)}</span>`;
    statusBar.className = "";
  } else if (type === "error") {
    statusBar.innerHTML = `<span>⚠ ${escapeHtml(msg)}</span>`;
    statusBar.className = "status-error";
  } else {
    statusBar.innerHTML = `<span>✓ ${escapeHtml(msg)}</span>`;
    statusBar.className = "status-ok";
  }
}

function escapeHtml(str) {
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function sleep(ms) {
  return new Promise((r) => setTimeout(r, ms));
}

// ── Initial load ───────────────────────────────────────────────────────────
loadAlerts().catch(() => {});
