"""3D Activity Flythrough — MapLibre GL JS cinematic camera animation.

Two modes:
  dark         — dark vector basemap with star-field fog (original style)
  satellite_3d — ESRI satellite imagery + terrain DEM, real 3D mountains

Animation engine:
  - Continuous 60 fps rAF loop with sub-point interpolation
  - Speed follows actual GPS timing (fast sections = fast camera, slow = slow)
  - Speed-adaptive EMA bearing: no jarky turns
  - Dynamic pitch and zoom: tilt up on climbs, zoom out at speed

Tile caching:
  - Pre-warms all route tiles at 10 keyframes before animation starts
  - Eliminates empty-tile artefacts on first pass

Video export:
  - 🎬 Export Full: pre-warms, auto-plays, auto-stops, downloads
  - ⏺ Record: manual start/stop recording from any point
  - Landscape 16:9 and Portrait 9:16 — both via centre-cropped offscreen canvas
  - Rendered at 2× pixel-ratio for high quality output
  - Downloads as .webm (VP9, 12 Mbps)
"""

import json
from typing import List, Optional

import streamlit as st


# ── Data pipeline ─────────────────────────────────────────────────────────────

def _fetch_track(activity_id: int) -> List[List[float]]:
    """Return [[lon, lat, ele, time_s], ...] — time_s may be None."""
    from ui.activity_analysis import _load_streams
    data   = _load_streams(activity_id)
    points = data.get("points", [])
    if not points:
        raise ValueError("No GPS stream data for this activity.")
    return [
        [p["lon"], p["lat"], p.get("ele") or 0.0, p.get("time_s")]
        for p in points
        if p.get("lat") is not None and p.get("lon") is not None
    ]


def _downsample(pts: List[List[float]], max_pts: int = 700) -> List[List[float]]:
    if len(pts) <= max_pts:
        return pts
    step = len(pts) / max_pts
    return [pts[int(i * step)] for i in range(max_pts)]


def _smooth(pts: List[List[float]], window: int = 3) -> List[List[float]]:
    """Smooth lon/lat/ele; preserve time_s (index 3) unchanged."""
    n   = len(pts)
    out = []
    for i in range(n):
        lo    = max(0, i - window)
        hi    = min(n, i + window + 1)
        chunk = pts[lo:hi]
        row   = [sum(p[k] for p in chunk) / len(chunk) for k in range(3)]
        if len(pts[i]) > 3:
            row.append(pts[i][3])  # keep original time, never average it
        out.append(row)
    return out


def _prepare_track(raw: List[List[float]]) -> List[List[float]]:
    return _smooth(_downsample(raw, 1200), 5)


# ── Map style definitions ─────────────────────────────────────────────────────

_DARK_STYLE_JS = "'https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json'"

# ESRI World Imagery (satellite, free, no key) + Amazon Terrarium DEM (free, no key)
_SAT3D_STYLE_JS = """{
  version: 8,
  sources: {
    satellite: {
      type: 'raster',
      tiles: ['https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}'],
      tileSize: 256,
      maxzoom: 19,
      attribution: '&copy; Esri, Maxar, GeoEye, Earthstar Geographics'
    },
    terrain_dem: {
      type: 'raster-dem',
      tiles: ['https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png'],
      tileSize: 256,
      encoding: 'terrarium',
      maxzoom: 15
    }
  },
  layers: [
    { id: 'satellite', type: 'raster', source: 'satellite' }
  ],
  terrain: { source: 'terrain_dem', exaggeration: 1.6 }
}"""


# ── HTML / JS template ────────────────────────────────────────────────────────
# Replaced by _build_html():
#   ACT_NAME        activity name (HTML-safe)
#   TRACK_JSON      [[lon,lat,ele,time_s_or_null], ...]
#   MAP_STYLE_INIT  JS style value (URL string or inline object)
#   MAP_MODE_INIT   'dark' | 'satellite_3d'
#   PITCH_INIT      initial pitch degrees
#   ZOOM_INIT       initial zoom level

_HTML = r"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<link rel="stylesheet" href="https://unpkg.com/maplibre-gl@3.6.2/dist/maplibre-gl.css">
<style>
*{margin:0;padding:0;box-sizing:border-box}
html,body{width:100%;height:100%;background:#080812;overflow:hidden}
#map-wrapper{width:100%;height:100%;position:relative;overflow:hidden}
#map{width:100%;height:100%}

/* ── Loading / status overlay ───────────────────────────────────────────── */
.overlay{
  position:absolute;inset:0;background:rgba(8,8,18,0.92);
  backdrop-filter:blur(14px);display:none;flex-direction:column;
  align-items:center;justify-content:center;gap:18px;z-index:200;
  transition:opacity .45s;
}
.overlay p{color:#9BA3C8;font:14px system-ui,sans-serif;text-align:center;}
.progress-track{width:260px;height:5px;background:#1a1a2e;border-radius:5px;overflow:hidden}
.progress-fill{height:100%;width:0%;background:linear-gradient(90deg,#FC4C02,#FF8C61);
  border-radius:5px;transition:width .35s ease}
.load-fill{height:100%;width:30%;background:linear-gradient(90deg,#FC4C02,#FF8C61);
  border-radius:5px;animation:sweep 1.4s ease-in-out infinite}
@keyframes sweep{0%{margin-left:0%;width:30%}50%{margin-left:70%;width:30%}100%{margin-left:0%;width:30%}}

/* ── Top progress bar ───────────────────────────────────────────────────── */
#progress-bar{
  position:absolute;top:0;left:0;height:3px;width:0%;
  background:linear-gradient(90deg,#FC4C02,#FF8C61);
  box-shadow:0 0 10px rgba(252,76,2,1);
  transition:width .1s linear;z-index:30;pointer-events:none
}

/* ── Info card (top-left) ───────────────────────────────────────────────── */
#info-card{
  position:absolute;top:14px;left:14px;
  background:rgba(6,6,18,0.84);backdrop-filter:blur(18px);
  border:1px solid rgba(252,76,2,0.42);border-radius:13px;
  padding:11px 16px;z-index:20;min-width:190px;max-width:260px
}
#act-name{color:#EEEEFF;font:700 14px system-ui,sans-serif;margin-bottom:8px;
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.stat-row{display:flex;gap:16px;flex-wrap:wrap}
.stat{color:#9BA3C8;font:10px/1.5 system-ui,sans-serif}
.stat b{color:#EEEEFF;display:block;font-size:13px;font-weight:700}

/* ── REC badge (top-right) ──────────────────────────────────────────────── */
#rec-badge{
  position:absolute;top:14px;right:14px;
  background:rgba(160,0,0,0.92);backdrop-filter:blur(10px);
  border:1px solid rgba(255,70,70,.55);border-radius:8px;
  padding:5px 12px;display:none;align-items:center;gap:7px;
  z-index:30;font:700 11px system-ui,sans-serif;color:#fff;letter-spacing:.04em
}
.rec-dot{width:7px;height:7px;background:#fff;border-radius:50%;
  animation:blink 1.1s ease-in-out infinite}
@keyframes blink{0%,100%{opacity:1;transform:scale(1)}50%{opacity:.2;transform:scale(.6)}}

/* ── Elevation canvas (compact bottom-right widget) ────────────────────── */
#elevation-bar{
  position:absolute;bottom:58px;right:12px;width:200px;height:58px;
  border-radius:10px;pointer-events:none;z-index:10;
  background:rgba(6,6,18,0.72);backdrop-filter:blur(10px);
  border:1px solid rgba(252,76,2,0.20);
}

/* ── Main controls bar (bottom-centre) ─────────────────────────────────── */
#controls{
  position:absolute;bottom:12px;left:50%;transform:translateX(-50%);
  background:rgba(6,6,18,0.90);backdrop-filter:blur(18px);
  border:1px solid rgba(252,76,2,0.30);border-radius:14px;
  padding:8px 16px;display:flex;align-items:center;gap:12px;
  z-index:20;white-space:nowrap
}
.ctrl-btn{
  background:#FC4C02;border:none;color:#fff;border-radius:8px;
  padding:6px 14px;cursor:pointer;font:600 12px system-ui,sans-serif;
  transition:background .15s,transform .08s;flex-shrink:0
}
.ctrl-btn:hover{background:#e04400}.ctrl-btn:active{transform:scale(.95)}
.ctrl-sep{width:1px;height:28px;background:rgba(252,76,2,.22);flex-shrink:0}
.ctrl-label{color:#9BA3C8;font:10px system-ui,sans-serif;display:flex;flex-direction:column;align-items:center;gap:2px;flex-shrink:0}
.ctrl-lbl-hd{display:flex;align-items:baseline;gap:4px;white-space:nowrap;height:14px}
.ctrl-val{color:#FC4C02;font-weight:700;font-size:11px}
input[type=range]{width:76px;accent-color:#FC4C02;cursor:pointer}

/* ── Record controls (bottom-right) ────────────────────────────────────── */
#rec-controls{
  position:fixed;bottom:12px;right:12px;
  background:rgba(6,6,18,0.90);backdrop-filter:blur(18px);
  border:1px solid rgba(252,76,2,0.30);border-radius:14px;
  padding:8px 12px;display:flex;align-items:center;gap:9px;z-index:60
}
.ctrl-select{
  background:rgba(16,16,32,0.92);border:1px solid rgba(252,76,2,.35);
  color:#9BA3C8;border-radius:7px;padding:4px 8px;
  font:11px system-ui,sans-serif;cursor:pointer;outline:none
}
.ctrl-select:hover{border-color:rgba(252,76,2,.7)}
.btn-export{background:#1d4ed8}
.btn-export:hover:not(.btn-recording){background:#1e3a8a!important}
.btn-recording{background:#b91c1c!important}

.maplibregl-ctrl-bottom-right,.maplibregl-ctrl-bottom-left{display:none!important}
</style>
</head>
<body>

<!-- Initial loading overlay (removed after map.load) -->
<div class="overlay" id="load-overlay" style="display:flex">
  <p>Loading 3D map…</p>
  <div class="progress-track"><div class="load-fill"></div></div>
</div>

<!-- Status overlay: tile pre-warm / render progress (reusable) -->
<div class="overlay" id="status-overlay">
  <p id="status-msg"></p>
  <div class="progress-track"><div class="progress-fill" id="status-fill"></div></div>
</div>

<div id="progress-bar"></div>
<div id="map-wrapper"><div id="map"></div></div>
<canvas id="elevation-bar"></canvas>

<div id="info-card">
  <div id="act-name">ACT_NAME</div>
  <div class="stat-row">
    <div class="stat"><b id="stat-dist">0.00 km</b>Distance</div>
    <div class="stat"><b id="stat-elev">0 m</b>Elevation</div>
    <div class="stat"><b id="stat-spd">— km/h</b>Speed</div>
    <div class="stat"><b id="stat-progress">0%</b>Progress</div>
  </div>
</div>

<div id="rec-badge"><span class="rec-dot"></span>REC</div>

<div id="controls">
  <button class="ctrl-btn" id="play-btn">&#9646;&#9646; Pause</button>
  <div class="ctrl-sep"></div>
  <label class="ctrl-label">
    <span class="ctrl-lbl-hd">Duration <span class="ctrl-val" id="speed-lbl">60s</span></span>
    <input type="range" id="speed" min="30" max="120" value="60" step="5">
  </label>
  <label class="ctrl-label">
    <span class="ctrl-lbl-hd">Tilt <span class="ctrl-val" id="pitch-lbl">PITCH_INIT°</span></span>
    <input type="range" id="pitch" min="20" max="85" value="PITCH_INIT">
  </label>
  <label class="ctrl-label">
    <span class="ctrl-lbl-hd">Height <span class="ctrl-val" id="zoom-lbl">mid</span></span>
    <input type="range" id="zoom" min="11" max="18" value="ZOOM_INIT" step="0.25">
  </label>
</div>

<div id="rec-controls">
  <select id="rec-format" class="ctrl-select">
    <option value="landscape">🖥&nbsp;16:9</option>
    <option value="portrait">📱&nbsp;9:16</option>
  </select>
  <select id="rec-res" class="ctrl-select">
    <option value="HD">HD</option>
    <option value="2K" selected>2K</option>
    <option value="4K">4K</option>
  </select>
  <button class="ctrl-btn btn-export" id="export-btn">&#127916; Export</button>
</div>

<script src="https://unpkg.com/maplibre-gl@3.6.2/dist/maplibre-gl.js"></script>
<script>
'use strict';

// ── Track & mode ─────────────────────────────────────────────────────────────
const TRACK       = TRACK_JSON;
const N           = TRACK.length;
const MAP_MODE    = 'MAP_MODE_INIT';
const ROUTE_NAME  = 'ACT_NAME';
const AUTO_EXPORT = 'AUTO_EXPORT_INIT' === 'true';  // set by Python for agent-triggered export
const HIDDEN      = 'HIDDEN_INIT' === 'true';        // suppress all visible UI when called from chat/server

const elevations = TRACK.map(p => p[2] || 0);
const minElev    = Math.min(...elevations);
const maxElev    = Math.max(...elevations);
const elevRange  = maxElev - minElev || 1;

// ── GPS timing ───────────────────────────────────────────────────────────────
// normTimes[i] ∈ [0,1]: fraction of total route time elapsed at point i.
// hasTiming=true when Strava provides time_s in the stream.
const hasTiming = TRACK.some(p => p.length > 3 && p[3] !== null);

let normTimes, totalDurationSec;
if (hasTiming) {
  const t0 = TRACK[0][3] || 0;
  const tN = TRACK[N-1][3] || (N-1);
  totalDurationSec = Math.max(1, tN - t0);
  normTimes = TRACK.map(p => ((p[3] || 0) - t0) / totalDurationSec);
} else {
  totalDurationSec = N - 1;
  normTimes = TRACK.map((_, i) => i / (N-1));
}

// Local speed at each point: normalised 0 (slowest) … 1 (fastest)
// Used for dynamic zoom and the speed readout in the UI.
const localSpeeds = new Float32Array(N);
for (let i = 0; i < N; i++) {
  const lo = Math.max(0, i - 4), hi = Math.min(N-1, i + 4);
  const dt = normTimes[hi] - normTimes[lo];
  localSpeeds[i] = dt > 0 ? (hi - lo) / dt : 0;
}
const maxLocalSpd = Math.max(...localSpeeds) || 1;
for (let i = 0; i < N; i++) localSpeeds[i] /= maxLocalSpd;

// Actual speed in km/h at each point (for info card)
const speedsKmh = new Float32Array(N);
if (hasTiming && totalDurationSec > 0) {
  for (let i = 0; i < N; i++) {
    const lo = Math.max(0, i-3), hi = Math.min(N-1, i+3);
    if (hi === lo) { speedsKmh[i] = 0; continue; }
    const dT = (TRACK[hi][3] - TRACK[lo][3]) || 0.001;
    const lon1=TRACK[lo][0],lat1=TRACK[lo][1],lon2=TRACK[hi][0],lat2=TRACK[hi][1];
    const dx = (lon2-lon1)*111320*Math.cos(lat1*Math.PI/180);
    const dy = (lat2-lat1)*110540;
    speedsKmh[i] = Math.sqrt(dx*dx+dy*dy) / dT * 3.6;
  }
}

// ── Total distance ───────────────────────────────────────────────────────────
const totalDistKm = (() => {
  let d = 0;
  for (let i = 1; i < N; i++) {
    const dx = (TRACK[i][0]-TRACK[i-1][0])*111320*Math.cos(TRACK[i][1]*Math.PI/180);
    const dy = (TRACK[i][1]-TRACK[i-1][1])*110540;
    d += Math.sqrt(dx*dx+dy*dy);
  }
  return d/1000;
})();

// ── Bearings (look 6 pts ahead for anticipatory camera) ─────────────────────
function calcBearing(lon1,lat1,lon2,lat2) {
  const r = d => d*Math.PI/180;
  const dL = r(lon2-lon1);
  const y  = Math.sin(dL)*Math.cos(r(lat2));
  const x  = Math.cos(r(lat1))*Math.sin(r(lat2))-Math.sin(r(lat1))*Math.cos(r(lat2))*Math.cos(dL);
  return (Math.atan2(y,x)*180/Math.PI+360)%360;
}
const bearings = new Float32Array(N);
for (let i = 0; i < N; i++) {
  const a = Math.min(i+20, N-1);
  bearings[i] = calcBearing(TRACK[i][0],TRACK[i][1],TRACK[a][0],TRACK[a][1]);
}
// 3 passes of angular running-average — eliminates GPS jitter while preserving real turns
for (let pass = 0; pass < 3; pass++) {
  const tmp = new Float32Array(bearings);
  for (let i = 1; i < N-1; i++) {
    bearings[i] = (tmp[i]
      + bearingDiff(tmp[i], tmp[i-1]) * 0.25
      + bearingDiff(tmp[i], tmp[i+1]) * 0.25 + 360) % 360;
  }
}

// ── Map init ─────────────────────────────────────────────────────────────────
const map = new maplibregl.Map({
  container:             'map',
  style:                 MAP_STYLE_INIT,
  center:                [TRACK[0][0], TRACK[0][1]],
  zoom:                  ZOOM_INIT,
  pitch:                 PITCH_INIT,
  bearing:               bearings[0],
  antialias:             true,
  attributionControl:    false,
  preserveDrawingBuffer: true,   // required for video capture
  pixelRatio:            Math.min(2, window.devicePixelRatio || 1),
});

// ── UI refs ──────────────────────────────────────────────────────────────────
const pb = document.getElementById('progress-bar');
const elCanvas = document.getElementById('elevation-bar');
const elCtx    = elCanvas.getContext('2d');
const recBadge = document.getElementById('rec-badge');

let pitchVal    = parseInt(document.getElementById('pitch').value);
let zoomVal     = parseFloat(document.getElementById('zoom').value);
let smoothPitch = pitchVal;
let smoothZoom  = zoomVal;

// Duration slider: 30–120 s → derive speedVal from desired video duration
function durLabel(s) {
  if (s < 60) return s + 's';
  const m = Math.floor(s / 60), sec = s % 60;
  return m + ':' + String(sec).padStart(2, '0');
}
function durToSpeed(durSec) { return totalDurationSec / Math.max(durSec, 1); }

// DURATION_INIT_SEC > 0 → agent-specified duration; 0 → auto-compute from route length
const _agentDur = DURATION_INIT_SEC;
const initDur = _agentDur > 0
  ? Math.max(30, Math.min(120, _agentDur))
  : Math.max(30, Math.min(120, Math.round(totalDurationSec / 90)));
let speedVal  = durToSpeed(initDur);
document.getElementById('speed').value = initDur;
document.getElementById('speed-lbl').textContent = durLabel(initDur);

// ── Animation state ──────────────────────────────────────────────────────────
// playbackTime: normalised 0…1, advances at speedVal/totalDurationSec per second.
// Maps to fractional track index via timeToIndex().
let playbackTime  = 0;
let smoothBearing = bearings[0];
let lastTime      = null;
let playing       = true;
let animId        = null;
let autoStopOnComplete = false;
let _origMapDims = null;  // saved before portrait resize, restored after

function lerp(a, b, t) { return a + (b-a)*t; }

function bearingDiff(from, to) { return ((to-from)%360+540)%360-180; }

// Binary-search normTimes to find fractional index for a given normalised time
function timeToIndex(t) {
  const clamped = Math.max(0, Math.min(0.99999, t));
  let lo = 0, hi = N-1;
  while (lo < hi-1) {
    const mid = (lo+hi)>>1;
    if (normTimes[mid] <= clamped) lo = mid; else hi = mid;
  }
  const denom = normTimes[lo+1] - normTimes[lo];
  return lo + (denom > 0 ? Math.min((clamped-normTimes[lo])/denom, 0.9999) : 0);
}

// ── Animation loop ───────────────────────────────────────────────────────────
function animStep(time) {
  animId = requestAnimationFrame(animStep);
  if (!playing) return;

  if (lastTime === null) { lastTime = time; return; }
  const dt = Math.min(time - lastTime, 80);
  lastTime = time;

  playbackTime += (dt/1000) * speedVal / totalDurationSec;

  if (playbackTime >= 1) {
    playbackTime = 0;
    if (autoStopOnComplete) {
      autoStopOnComplete = false;
      playing = false;
      document.getElementById('play-btn').innerHTML = '&#9654; Play';
      stopRecording();
      return;
    }
  }

  const progress  = timeToIndex(playbackTime);
  const idx       = Math.floor(progress);
  const frac      = progress - idx;
  const nxt       = Math.min(idx+1, N-1);

  // Interpolated position
  const lon = lerp(TRACK[idx][0], TRACK[nxt][0], frac);
  const lat = lerp(TRACK[idx][1], TRACK[nxt][1], frac);

  // Cinematic EMA bearing — very low alpha for buttery-smooth panning
  const alpha = Math.min(0.008 + speedVal*0.0003, 0.035);
  smoothBearing = (smoothBearing + bearingDiff(smoothBearing, bearings[idx])*alpha + 360) % 360;

  // Dynamic pitch: slope-aware tilt, EMA-smoothed so changes are gradual
  const slopeWin   = Math.min(16, Math.max(1, Math.round(N*0.02)));
  const elevFwd    = elevations[Math.min(idx+slopeWin, N-1)];
  const elevBwd    = elevations[Math.max(idx-slopeWin, 0)];
  const slopeAdj   = Math.max(-5, Math.min(5, (elevFwd-elevBwd)*0.18));
  const targetPitch = Math.max(22, Math.min(83, pitchVal+slopeAdj));
  smoothPitch += (targetPitch - smoothPitch) * 0.035;

  // Dynamic zoom: fast → pull back, slow → push in. EMA prevents sudden jumps.
  const normSpd    = localSpeeds[idx];
  const targetZoom = zoomVal + (normSpd - 0.5) * -0.55;
  smoothZoom += (targetZoom - smoothZoom) * 0.025;

  map.jumpTo({ center:[lon,lat], bearing:smoothBearing, pitch:smoothPitch, zoom:smoothZoom });

  updateSources(idx, frac);
  updateStats(idx, frac);
  drawElevProfile(playbackTime);
}

// ── Source updates ───────────────────────────────────────────────────────────
function updateSources(idx, frac) {
  if (!map.getSource('done')) return;
  const nxt = Math.min(idx+1, N-1);

  // Completed route — interpolate tip to exact sub-point position
  const coords = TRACK.slice(0, idx+2).map(p => [p[0],p[1]]);
  coords[coords.length-1] = [lerp(TRACK[idx][0],TRACK[nxt][0],frac),
                              lerp(TRACK[idx][1],TRACK[nxt][1],frac)];
  map.getSource('done').setData({type:'Feature',geometry:{type:'LineString',coordinates:coords}});

  map.getSource('dot').setData({type:'Feature',geometry:{type:'Point',coordinates:[
    lerp(TRACK[idx][0],TRACK[nxt][0],frac),
    lerp(TRACK[idx][1],TRACK[nxt][1],frac),
  ]}});
}

function updateStats(idx, frac) {
  const nxt = Math.min(idx+1, N-1);
  const elev = lerp(elevations[idx], elevations[nxt], frac);
  const spd  = lerp(speedsKmh[idx], speedsKmh[nxt], frac);
  document.getElementById('stat-dist').textContent     = (playbackTime*totalDistKm).toFixed(2)+' km';
  document.getElementById('stat-elev').textContent     = Math.round(elev)+' m';
  document.getElementById('stat-spd').textContent      = spd > 0.5 ? spd.toFixed(1)+' km/h' : '—';
  document.getElementById('stat-progress').textContent = Math.round(playbackTime*100)+'%';
  pb.style.width = (playbackTime*100)+'%';
}

// ── Elevation profile ────────────────────────────────────────────────────────
// Generic renderer — works for both the compact widget and the export composite.
// Draws a background fill so it overlays correctly when composited onto the map.
function drawElevToCtx(ctx, t, W, H) {
  // Semi-transparent background (matches #elevation-bar CSS for the widget;
  // in the export composite this covers the strip area on top of the map frame).
  ctx.fillStyle = 'rgba(6,6,18,0.72)';
  ctx.fillRect(0, 0, W, H);

  // Filled area under profile
  ctx.beginPath();
  for (let i = 0; i < N; i++) {
    const x = (i/(N-1))*W;
    const y = H - ((elevations[i]-minElev)/elevRange)*(H-10) - 5;
    i===0 ? ctx.moveTo(x,y) : ctx.lineTo(x,y);
  }
  ctx.lineTo(W,H); ctx.lineTo(0,H); ctx.closePath();
  const g = ctx.createLinearGradient(0,0,0,H);
  g.addColorStop(0,'rgba(252,76,2,0.55)'); g.addColorStop(1,'rgba(252,76,2,0.04)');
  ctx.fillStyle = g; ctx.fill();

  // Profile outline
  ctx.beginPath();
  for (let i = 0; i < N; i++) {
    const x = (i/(N-1))*W;
    const y = H - ((elevations[i]-minElev)/elevRange)*(H-10) - 5;
    i===0 ? ctx.moveTo(x,y) : ctx.lineTo(x,y);
  }
  ctx.strokeStyle = 'rgba(252,76,2,0.80)';
  ctx.lineWidth = Math.max(1, H/60);
  ctx.stroke();

  // Playback progress tint
  const px = t*W;
  ctx.fillStyle = 'rgba(252,76,2,0.12)'; ctx.fillRect(0,0,px,H);

  // Playhead line
  ctx.beginPath(); ctx.moveTo(px,0); ctx.lineTo(px,H);
  ctx.strokeStyle = 'rgba(255,255,255,0.85)';
  ctx.lineWidth = Math.max(1.5, H/40);
  ctx.stroke();

  // Elevation labels (scale font with widget height)
  const fs = Math.max(10, Math.round(H * 0.20));
  ctx.fillStyle = 'rgba(155,163,200,0.80)';
  ctx.font = fs+'px system-ui,sans-serif';
  ctx.fillText(Math.round(maxElev)+'m', 5, fs+2);
  ctx.fillText(Math.round(minElev)+'m', 5, H-4);
}

// Thin wrapper: draws into the fixed-size compact widget canvas
function drawElevProfile(t) {
  elCanvas.width  = 400;   // resetting dimensions clears the canvas
  elCanvas.height = 116;
  drawElevToCtx(elCtx, t, 400, 116);
}

// ── Map layers ───────────────────────────────────────────────────────────────
map.on('load', () => {
  // Always remove loading overlay first
  const loadOvl = document.getElementById('load-overlay');
  loadOvl.style.opacity = '0';
  setTimeout(() => loadOvl.remove(), 500);

  // Layer setup is wrapped in try/catch: an exception here must NOT prevent
  // the animation loop from starting. MapLibre silently swallows callback
  // exceptions, so without this the loop would never start and the map would
  // appear frozen.
  try {
    const fullCoords = TRACK.map(p => [p[0],p[1]]);

    // Atmospheric / terrain effects ─────────────────────────────────────────
    if (MAP_MODE === 'satellite_3d') {
      try { map.setTerrain({ source:'terrain_dem', exaggeration:1.6 }); } catch(_) {}
      try {
        map.setFog({
          color: 'rgba(205,190,165,0.22)', 'high-color': '#c2d8f2',
          'horizon-blend': 0.07, 'space-color': '#0a1420', 'star-intensity': 0.04,
        });
      } catch(_) {}
      try {
        map.addLayer({id:'sky',type:'sky',paint:{
          'sky-type': 'atmosphere',
          'sky-atmosphere-sun': [42.0, 58.0],
          'sky-atmosphere-sun-intensity': 15,
        }});
      } catch(_) {}
    } else {
      try {
        map.setFog({
          color: 'rgba(4,5,14,0.88)', 'high-color': '#060a18',
          'horizon-blend': 0.15, 'space-color': '#000104', 'star-intensity': 0.98,
        });
      } catch(_) {}
    }

    const isSat = MAP_MODE === 'satellite_3d';

    // Ghost route — dashed ahead-line; subtler on satellite ──────────────────
    map.addSource('ghost',{type:'geojson',data:{
      type:'Feature',geometry:{type:'LineString',coordinates:fullCoords}
    }});
    map.addLayer({id:'ghost-line',type:'line',source:'ghost',
      layout:{'line-join':'round','line-cap':'butt'},
      paint:{
        'line-color': '#FFFFFF',
        'line-width':  isSat ? 1.8 : 2,
        'line-opacity': isSat ? 0.38 : 0.28,
        'line-dasharray': [5, 3],
      }
    });

    // Completed route ─────────────────────────────────────────────────────────
    // Satellite: thin dark outline + 2.5 px orange core — clean GPS-track look.
    // Dark:      wide neon glow + 4 px core + bright edge highlight.
    map.addSource('done',{type:'geojson',data:{
      type:'Feature',geometry:{type:'LineString',coordinates:[fullCoords[0], fullCoords[0]]}
    }});
    if (isSat) {
      map.addLayer({id:'done-outline',type:'line',source:'done',
        layout:{'line-join':'round','line-cap':'round'},
        paint:{'line-color':'rgba(0,0,0,0.55)','line-width':5,'line-opacity':0.60}});
    } else {
      map.addLayer({id:'done-glow',type:'line',source:'done',
        layout:{'line-join':'round','line-cap':'round'},
        paint:{'line-color':'#FF5515','line-width':16,'line-opacity':0.22,'line-blur':11}});
    }
    map.addLayer({id:'done-core',type:'line',source:'done',
      layout:{'line-join':'round','line-cap':'round'},
      paint:{'line-color':'#FC4C02','line-width': isSat ? 2.5 : 4,'line-opacity':1}});
    if (!isSat) {
      map.addLayer({id:'done-edge',type:'line',source:'done',
        layout:{'line-join':'round','line-cap':'round'},
        paint:{'line-color':'rgba(255,225,190,0.80)','line-width':1.0,'line-opacity':0.80}});
    }

    // Start / finish pins ───────────────────────────────────────────────────
    map.addSource('pins',{type:'geojson',data:{type:'FeatureCollection',features:[
      {type:'Feature',properties:{c:'#22C55E'},geometry:{type:'Point',coordinates:fullCoords[0]}},
      {type:'Feature',properties:{c:'#EF4444'},geometry:{type:'Point',coordinates:fullCoords[N-1]}},
    ]}});
    map.addLayer({id:'pin-dots',type:'circle',source:'pins',paint:{
      'circle-radius': isSat ? 6 : 8,'circle-color':['get','c'],
      'circle-stroke-width': isSat ? 1.5 : 2,'circle-stroke-color':'#fff',
    }});

    // Position dot ────────────────────────────────────────────────────────────
    // Satellite: tiny white dot + orange ring, zero bloom (realistic GPS marker).
    // Dark:      outer halo + mid glow + white core (cinematic neon).
    map.addSource('dot',{type:'geojson',data:{
      type:'Feature',geometry:{type:'Point',coordinates:fullCoords[0]}
    }});
    if (!isSat) {
      map.addLayer({id:'dot-halo',type:'circle',source:'dot',paint:{
        'circle-radius':20,'circle-color':'#FC4C02','circle-opacity':0.10,'circle-blur':2.0}});
      map.addLayer({id:'dot-glow',type:'circle',source:'dot',paint:{
        'circle-radius':13,'circle-color':'#FF6030','circle-opacity':0.20,'circle-blur':1.2}});
    }
    map.addLayer({id:'dot-core',type:'circle',source:'dot',paint:{
      'circle-radius': isSat ? 5 : 7,'circle-color':'#ffffff',
      'circle-stroke-width': isSat ? 2 : 2.5,'circle-stroke-color':'#FC4C02'}});

  } catch(err) {
    console.error('[flythrough] layer setup error:', err);
  }

  // Always start animation — even if layer setup partially failed
  drawElevProfile(0);
  updateStats(0, 0);
  lastTime = null;
  animId   = requestAnimationFrame(animStep);

  // Agent-triggered auto-export: starts recording immediately after map loads
  if (AUTO_EXPORT) setTimeout(() => exportFull(), 1500);
});

// ── Tile pre-warming ─────────────────────────────────────────────────────────
// Two-pass strategy:
//   Pass 1 (60%): visit numFrames keyframes at playback zoom — warms base tiles.
//   Pass 2 (40%): spot-check 10 positions at zoom+1.5 — warms close-up tiles.
// Per-frame timeout is 3.5 s; a 150 ms poll avoids the flaky map.once('idle').
async function prewarmTiles(numFrames = 20) {
  const solay = document.getElementById('status-overlay');
  solay.style.display = 'flex'; solay.style.opacity = '1';
  document.getElementById('status-msg').textContent = 'Pre-loading map tiles…';
  const fillEl = document.getElementById('status-fill');

  const isSatMode = MAP_MODE === 'satellite_3d';
  async function waitIdle(maxMs) {
    // map.once('idle') fires after all tiles are loaded AND the frame is fully rendered
    // (including terrain DEM mesh generation). More reliable than polling areTilesLoaded().
    await new Promise(resolve => {
      let settled = false;
      const done = () => { if (!settled) { settled = true; resolve(); } };
      setTimeout(done, maxMs);
      map.once('idle', done);
      map.triggerRepaint();  // ensure idle fires even if map is already idle
    });
    // Extra settle: satellite terrain GPU mesh finalises slightly after idle fires
    await new Promise(r => setTimeout(r, isSatMode ? 900 : 250));
  }

  // Pass 1 — all keyframes at playback zoom
  for (let i = 0; i < numFrames; i++) {
    const idx = Math.floor(i * (N-1) / Math.max(numFrames-1, 1));
    map.jumpTo({center:[TRACK[idx][0],TRACK[idx][1]],bearing:bearings[idx],pitch:pitchVal,zoom:zoomVal});
    await waitIdle(isSatMode ? 5000 : 3500);
    fillEl.style.width = ((i+1)/numFrames * 60) + '%';
  }

  // Pass 2 — 10 spot-checks at zoom+1.5 to warm close-up satellite/terrain tiles
  const zoomIn = Math.min(zoomVal + 1.5, 17.5);
  const p2n = Math.min(numFrames, 10);
  for (let i = 0; i < p2n; i++) {
    const idx = Math.floor(i * (N-1) / Math.max(p2n-1, 1));
    map.jumpTo({center:[TRACK[idx][0],TRACK[idx][1]],bearing:bearings[idx],pitch:pitchVal,zoom:zoomIn});
    await waitIdle(2000);
    fillEl.style.width = (60 + (i+1)/p2n * 40) + '%';
  }

  // Return to start
  map.jumpTo({center:[TRACK[0][0],TRACK[0][1]],bearing:bearings[0],pitch:pitchVal,zoom:zoomVal});
  await new Promise(r => setTimeout(r, 500));

  solay.style.opacity = '0';
  await new Promise(r => setTimeout(r, 450));
  solay.style.display = 'none';
  solay.style.opacity = '1';
  fillEl.style.width = '0%';
}

// ── Error handling ────────────────────────────────────────────────────────────
// Only disable terrain on terrain-source errors — never replace the style.
// Replacing the style (setStyle) wipes all GeoJSON sources and layers.
// status:0 fires on every cancelled tile request (e.g. during prewarm jumps)
// so any broader error handler would destroy the route layers constantly.
map.on('error', e => {
  if ((e.sourceId||'').includes('terrain')) {
    // Cancelled tile requests (status:0, AbortError) fire continuously during prewarm
    // jumps — NEVER disable terrain for those, only for genuine network failures.
    const status = e.error?.status ?? 0;
    if (status > 0 && e.error?.name !== 'AbortError') {
      try { map.setTerrain(null); } catch(_) {}
    }
  }
});

// ── Video recording ──────────────────────────────────────────────────────────
let recorder     = null;
let recChunks    = [];
let isRecording  = false;
let portCanvas   = null;
let portCtx      = null;
let copyFrame    = null;

function roundRect(ctx, x, y, w, h, r) {
  ctx.beginPath();
  ctx.moveTo(x+r, y); ctx.lineTo(x+w-r, y);
  ctx.arcTo(x+w, y, x+w, y+r, r); ctx.lineTo(x+w, y+h-r);
  ctx.arcTo(x+w, y+h, x+w-r, y+h, r); ctx.lineTo(x+r, y+h);
  ctx.arcTo(x, y+h, x, y+h-r, r); ctx.lineTo(x, y+r);
  ctx.arcTo(x, y, x+r, y, r); ctx.closePath();
}

function getCropDims(format) {
  // Map is pre-sized to the exact target resolution before recording starts.
  const src = map.getCanvas();
  return { w: src.width, h: src.height, sx: 0, sy: 0 };
}

function startRecording(format) {
  if (isRecording) return;
  const mapCanvas = map.getCanvas();
  const nW = mapCanvas.width, nH = mapCanvas.height;
  const { w: outW, h: outH, sx, sy } = getCropDims(format);

  portCanvas = document.createElement('canvas');
  portCanvas.width = outW; portCanvas.height = outH;
  portCtx = portCanvas.getContext('2d');

  copyFrame = () => {
    if (!portCtx) return;

    // 1. Map WebGL frame (native physical pixels, no resize)
    portCtx.drawImage(mapCanvas, sx, sy, outW, outH, 0, 0, outW, outH);

    // 2. Compact elevation widget — bottom-right corner, proportional to video width
    //    Max 22% of video width (capped at 380 px) to prevent horizontal stretch.
    const elW    = Math.min(Math.round(outW * 0.22), 380);
    const elH    = Math.max(52, Math.round(elW * 0.30));
    const elPad  = Math.round(outW * 0.012);
    const elX    = outW - elW - elPad;
    const elY    = outH - elH - Math.round(outH * 0.025);
    portCtx.save();
    portCtx.translate(elX, elY);
    drawElevToCtx(portCtx, playbackTime, elW, elH);
    portCtx.restore();

    // 3. Orange progress bar
    const barH = Math.max(3, Math.round(nW / 400));
    const pg = portCtx.createLinearGradient(0, 0, outW * playbackTime, 0);
    pg.addColorStop(0, '#FC4C02'); pg.addColorStop(1, '#FF8C61');
    portCtx.fillStyle = pg;
    portCtx.fillRect(0, 0, outW * playbackTime, barH);

    // 4. Info card (top-left) — scales with output resolution
    const s = Math.max(0.6, Math.min(3.0, outW / 700));
    const pad = Math.round(12 * s);
    const cW = Math.round(220 * s), cH = Math.round(64 * s);
    portCtx.fillStyle = 'rgba(6,6,18,0.82)';
    roundRect(portCtx, pad, pad + barH, cW, cH, Math.round(9 * s));
    portCtx.fill();

    const curIdx  = timeToIndex(playbackTime);
    const iLo     = Math.floor(curIdx);
    const iFrac   = curIdx - iLo;
    const iHi     = Math.min(iLo + 1, N - 1);
    const curElev = Math.round(lerp(elevations[iLo], elevations[iHi], iFrac));
    const curDist = (playbackTime * totalDistKm).toFixed(2) + ' km';
    const curProg = Math.round(playbackTime * 100) + '%';

    const tx = pad + Math.round(10 * s);
    const ty0 = pad + barH + Math.round(18 * s);
    portCtx.fillStyle = '#EEEEFF';
    portCtx.font = `700 ${Math.round(12*s)}px system-ui,sans-serif`;
    portCtx.fillText(ROUTE_NAME.substring(0, 26), tx, ty0);

    const ty1 = ty0 + Math.round(20 * s);
    portCtx.fillStyle = '#FC4C02';
    portCtx.font = `700 ${Math.round(13*s)}px system-ui,sans-serif`;
    portCtx.fillText(curDist,           tx,                        ty1);
    portCtx.fillText(curElev + ' m',    tx + Math.round(90*s),     ty1);
    portCtx.fillText(curProg,           tx + Math.round(168*s),    ty1);

    const ty2 = ty1 + Math.round(13 * s);
    portCtx.fillStyle = '#9BA3C8';
    portCtx.font = `${Math.round(9*s)}px system-ui,sans-serif`;
    portCtx.fillText('Distance',  tx,                     ty2);
    portCtx.fillText('Elevation', tx + Math.round(90*s),  ty2);
    portCtx.fillText('Progress',  tx + Math.round(168*s), ty2);
  };

  map.on('render', copyFrame);
  copyFrame();

  const mime = [
    'video/mp4;codecs=avc1,mp4a.40.2',
    'video/mp4;codecs=avc1',
    'video/mp4',
    'video/webm;codecs=vp9',
    'video/webm',
  ].find(t => MediaRecorder.isTypeSupported(t)) || '';
  const bitrateByRes = { HD: 15_000_000, '2K': 30_000_000, '4K': 80_000_000 };
  const recRes2 = document.getElementById('rec-res').value;
  const recOpts = { videoBitsPerSecond: bitrateByRes[recRes2] || 30_000_000 };
  if (mime) recOpts.mimeType = mime;
  recorder = new MediaRecorder(portCanvas.captureStream(60), recOpts);
  recChunks = [];
  const recStart = performance.now();
  recorder.ondataavailable = e => { if(e.data.size>0) recChunks.push(e.data); };
  recorder.onstop = () => downloadVideo(format, mime, (performance.now() - recStart) / 1000);
  recorder.start(500);  // 500 ms chunks → less fragmentation → better MP4 compat

  isRecording = true;
  recBadge.style.display = 'flex';
  const eb = document.getElementById('export-btn');
  eb.innerHTML = '&#9209; Stop Export';
  eb.classList.add('btn-recording');
}

function stopRecording() {
  if (!recorder && !isRecording) return;
  if (copyFrame) { map.off('render', copyFrame); copyFrame = null; }
  portCanvas = null; portCtx = null;
  if (recorder) { recorder.stop(); recorder = null; }
  isRecording = false;
  smoothZoom  = zoomVal;
  // Restore map canvas after export
  if (_origMapDims !== null) {
    const mapEl      = document.getElementById('map');
    const mapWrapper = document.getElementById('map-wrapper');
    mapEl.style.cssText      = _origMapDims.mapCssText;
    mapWrapper.style.cssText = _origMapDims.wrapperCssText;
    if (_origMapDims.pixelRatio) map.setPixelRatio(_origMapDims.pixelRatio);
    map.resize();
    if (MAP_MODE === 'satellite_3d') {
      try { map.setTerrain({ source:'terrain_dem', exaggeration:1.6 }); } catch(_) {}
    }
    _origMapDims = null;
  }
  recBadge.style.display = 'none';
  const eb = document.getElementById('export-btn');
  eb.innerHTML = '&#127916; Export';
  eb.classList.remove('btn-recording');
}

// Patches the `duration` field in an fMP4 moov/mvhd box so players show elapsed time.
// Chrome's MediaRecorder writes duration=0 in the mvhd atom; this corrects it in-place.
async function fixMp4Duration(blob, durationSecs) {
  const buf = await blob.arrayBuffer();
  const dv  = new DataView(buf);
  let off = 0;
  while (off < buf.byteLength - 8) {
    const sz   = dv.getUint32(off);
    const type = String.fromCharCode(dv.getUint8(off+4),dv.getUint8(off+5),
                                     dv.getUint8(off+6),dv.getUint8(off+7));
    if (type === 'moov') {
      let inner = off + 8;
      while (inner < off + sz - 8) {
        const iSz   = dv.getUint32(inner);
        const iType = String.fromCharCode(dv.getUint8(inner+4),dv.getUint8(inner+5),
                                          dv.getUint8(inner+6),dv.getUint8(inner+7));
        if (iType === 'mvhd') {
          const ver = dv.getUint8(inner + 8);
          if (ver === 1) {
            // version 1: timescale at +28 (4 bytes), duration at +32 (8 bytes)
            const ts = dv.getUint32(inner + 28);
            dv.setBigUint64(inner + 32, BigInt(Math.round(durationSecs * ts)));
          } else {
            // version 0: timescale at +20 (4 bytes), duration at +24 (4 bytes)
            const ts = dv.getUint32(inner + 20);
            dv.setUint32(inner + 24, Math.round(durationSecs * ts));
          }
          break;
        }
        inner += iSz || 8;
      }
      break;
    }
    off += sz || 8;
  }
  return new Blob([buf], { type: blob.type });
}

async function downloadVideo(format, mime, durationSecs) {
  let blob = new Blob(recChunks, { type: mime || 'video/webm' });
  const ext = (mime || '').startsWith('video/mp4') ? 'mp4' : 'webm';
  if (ext === 'mp4' && durationSecs > 0) {
    try { blob = await fixMp4Duration(blob, durationSecs); } catch(e) {
      console.warn('[flythrough] fixMp4Duration failed:', e);
    }
  }
  const url = URL.createObjectURL(blob);
  const a   = document.createElement('a');
  a.href = url; a.download = `flythrough_${format}_${Date.now()}.${ext}`;
  document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 8000);
  if (HIDDEN) {
    try { if (window.frameElement) { window.frameElement.style.height = '0px'; window.frameElement.style.display = 'none'; } } catch(_) {}
  }
}

// ── Full export: pre-warm → reset → record → auto-stop ───────────────────────
async function exportFull() {
  if (isRecording) { stopRecording(); return; }
  const fmt = document.getElementById('rec-format').value;

  // Pause main animation
  playing = false;
  if (animId) { cancelAnimationFrame(animId); animId = null; }

  // Resize map to the exact recording resolution using fixed-position overlay.
  // Covers the iframe without touching the iframe height → no Streamlit layout break.
  {
    const solay = document.getElementById('status-overlay');
    solay.style.display = 'flex'; solay.style.opacity = '1';
    document.getElementById('status-msg').textContent = 'Preparing view…';
    document.getElementById('status-fill').style.width = '0%';

    const recRes = document.getElementById('rec-res').value;
    const resMap = {
      landscape: { HD:[1920,1080], '2K':[2560,1440], '4K':[3840,2160] },
      portrait:  { HD:[1080,1920], '2K':[1440,2560], '4K':[2160,3840] },
    };
    const [physW, physH] = resMap[fmt][recRes];

    // Use an explicit export pixelRatio independent of screen DPR so that:
    //   1. Canvas allocation is always physW × physH regardless of screen type.
    //   2. MapLibre loads tiles at this ratio's zoom level — higher ratio = richer tiles.
    // Portrait uses 3× because its narrower CSS viewport would otherwise get fewer tile
    // columns than landscape; forcing 3× makes MapLibre load tiles as if it's an
    // ultra-retina display, compensating for the narrower view with much higher detail.
    const EXPORT_DPR = fmt === 'portrait' ? 3 : 2;
    map.setPixelRatio(EXPORT_DPR);
    const cssW = Math.round(physW / EXPORT_DPR);
    const cssH = Math.round(physH / EXPORT_DPR);

    const mapEl      = document.getElementById('map');
    const mapWrapper = document.getElementById('map-wrapper');
    const origPixelRatio = Math.min(2, window.devicePixelRatio || 1);
    _origMapDims = { mapCssText: mapEl.style.cssText, wrapperCssText: mapWrapper.style.cssText, pixelRatio: origPixelRatio };

    // Resize map element — canvas is now exactly physW × physH (cssW * EXPORT_DPR).
    mapEl.style.cssText = `position:absolute;top:0;left:0;width:${cssW}px;height:${cssH}px;`;
    map.resize();

    // Hidden mode: position off-screen so WebGL still renders but the user sees nothing.
    // Visible mode: scale wrapper to fit viewport for a preview of the recording frame.
    if (HIDDEN) {
      mapWrapper.style.cssText = `position:fixed;left:${-(cssW+500)}px;top:0;width:${cssW}px;height:${cssH}px;z-index:-100;`;
    } else {
      // Transform is on the wrapper only — MapLibre watches #map whose layout dimensions
      // stay at cssW×cssH, so the canvas is unaffected by this visual-only scaling.
      const viewW    = window.innerWidth;
      const viewH    = window.innerHeight;
      const fitScale = Math.min(viewW / cssW, viewH / cssH, 1.0);
      const offsetX  = Math.round((viewW - cssW * fitScale) / 2);
      const offsetY  = Math.round((viewH - cssH * fitScale) / 2);
      mapWrapper.style.cssText = `position:fixed;top:0;left:0;width:${cssW}px;height:${cssH}px;transform:translate(${offsetX}px,${offsetY}px) scale(${fitScale});transform-origin:top left;z-index:50;`;
    }

    if (MAP_MODE === 'satellite_3d') {
      const exag = fmt === 'portrait' ? 2.2 : 1.6;
      try { map.setTerrain({ source:'terrain_dem', exaggeration:exag }); } catch(_) {}
    }
    await new Promise(r => setTimeout(r, 400));
  }

  smoothZoom  = zoomVal;
  smoothPitch = pitchVal;

  // 1. Pre-warm tiles
  await prewarmTiles(MAP_MODE === 'satellite_3d' ? 30 : 20);

  // 2. Reset to start
  playbackTime  = 0;
  smoothBearing = bearings[0];
  smoothZoom    = zoomVal;
  smoothPitch   = pitchVal;
  map.jumpTo({center:[TRACK[0][0],TRACK[0][1]],bearing:bearings[0],pitch:pitchVal,zoom:zoomVal});
  await new Promise(r => setTimeout(r, 400));

  // 3. Start recording
  startRecording(fmt);

  // 4. Play — will auto-stop when route complete
  autoStopOnComplete = true;
  playing   = true;
  lastTime  = null;
  animId    = requestAnimationFrame(animStep);
}

// ── Controls ─────────────────────────────────────────────────────────────────
document.getElementById('play-btn').addEventListener('click', () => {
  playing = !playing;
  document.getElementById('play-btn').innerHTML = playing ? '&#9646;&#9646; Pause' : '&#9654; Play';
  if (playing) {
    lastTime = null;
    if (!animId) animId = requestAnimationFrame(animStep);  // restart if loop died
  }
});

function zoomLabel(z) { return z <= 12.5 ? 'far' : z >= 15.5 ? 'close' : 'mid'; }

// Initialise all label values
document.getElementById('pitch-lbl').textContent = pitchVal + '°';
document.getElementById('zoom-lbl').textContent  = zoomLabel(zoomVal);

document.getElementById('speed').addEventListener('input', e => {
  const dur = parseInt(e.target.value);
  speedVal  = durToSpeed(dur);
  document.getElementById('speed-lbl').textContent = durLabel(dur);
});

document.getElementById('pitch').addEventListener('input', e => {
  pitchVal    = parseInt(e.target.value);
  smoothPitch = pitchVal;
  document.getElementById('pitch-lbl').textContent = pitchVal + '°';
  map.easeTo({pitch:pitchVal, duration:180});
});

document.getElementById('zoom').addEventListener('input', e => {
  zoomVal    = parseFloat(e.target.value);
  smoothZoom = zoomVal;
  document.getElementById('zoom-lbl').textContent = zoomLabel(zoomVal);
  map.easeTo({zoom:zoomVal, duration:180});
});

document.getElementById('export-btn').addEventListener('click', exportFull);
document.getElementById('rec-format').value = 'ORIENTATION_INIT';
if (!HIDDEN) {
  try { window.frameElement && window.frameElement.scrollIntoView({behavior:'smooth',block:'start'}); } catch(_){}
}
</script>
</body>
</html>"""


# ── HTML builder ──────────────────────────────────────────────────────────────

def _build_html(
    track: List[List[float]],
    name: str,
    mode: str = "satellite_3d",
    auto_export: bool = False,
    duration_sec: int = 0,
    orientation: str = "landscape",
    hidden: bool = False,
) -> str:
    safe_name = name.replace('"', '\\"').replace("<", "").replace(">", "")

    if mode == "satellite_3d":
        style_js   = _SAT3D_STYLE_JS
        pitch_init = "72"
        zoom_init  = "13.5"
    else:
        style_js   = _DARK_STYLE_JS
        pitch_init = "65"
        zoom_init  = "14.5"

    return (
        _HTML
        .replace("ACT_NAME",          safe_name)
        .replace("TRACK_JSON",        json.dumps(track))
        .replace("MAP_STYLE_INIT",    style_js)
        .replace("MAP_MODE_INIT",     mode)
        .replace("PITCH_INIT",        pitch_init)
        .replace("ZOOM_INIT",         zoom_init)
        .replace("AUTO_EXPORT_INIT",  "true" if auto_export else "false")
        .replace("DURATION_INIT_SEC", str(max(0, int(duration_sec))))
        .replace("ORIENTATION_INIT",  "portrait" if orientation == "portrait" else "landscape")
        .replace("HIDDEN_INIT",       "true" if hidden else "false")
    )


# ── Public entry point ────────────────────────────────────────────────────────

def show_flythrough(
    activity_id: int,
    activity_name: str = "",
    auto_export: bool = False,
    mode: Optional[str] = None,
    duration_sec: int = 0,
    orientation: str = "landscape",
    hidden: bool = False,
) -> None:
    name = activity_name or f"Activity {activity_id}"

    if mode is None:
        if hidden:
            mode = "satellite_3d"
        else:
            # ── Mode selector (dashboard / manual use only) ──
            mode_label = st.radio(
                "Map style",
                ["Satellite 3D", "Dark Flat"],
                index=0,
                horizontal=True,
                key=f"flythrough_mode_{activity_id}",
                label_visibility="collapsed",
            )
            mode = "satellite_3d" if mode_label == "Satellite 3D" else "dark"

    try:
        if hidden:
            raw   = _fetch_track(activity_id)
            track = _prepare_track(raw)
        else:
            with st.spinner("Loading GPS track…"):
                raw   = _fetch_track(activity_id)
                track = _prepare_track(raw)
    except Exception as e:
        if not hidden:
            st.error(f"Could not load GPS data: {e}")
        return

    if not hidden:
        ele_values = [p[2] for p in track if p[2]]
        has_timing = any(p[3] is not None for p in track if len(p) > 3)
        ele_range  = f"{min(ele_values):.0f} – {max(ele_values):.0f} m" if ele_values else "—"

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("GPS Points",      f"{len(track):,}")
        c2.metric("Elevation Range", ele_range)
        c3.metric("Speed Data",      "✓ GPS timing" if has_timing else "uniform")
        c4.metric("Render Quality",  "2× (HD)")

        mode_label_str = "Satellite 3D · real terrain" if mode == "satellite_3d" else "Dark flat · star-field"
        st.caption(
            f"{'🌍' if mode == 'satellite_3d' else '🗺'} {mode_label_str} · "
            "🎬 Export = select 16:9 or 9:16, pick HD/2K/4K, click Export — "
            "pre-warms tiles, renders at native resolution, auto-downloads as MP4"
        )

    st.iframe(
        _build_html(track, name, mode=mode, auto_export=auto_export, duration_sec=duration_sec, orientation=orientation, hidden=hidden),
        height=1 if hidden else 630,
    )
