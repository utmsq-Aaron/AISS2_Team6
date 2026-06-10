"""3D Activity Flythrough — MapLibre GL JS cinematic camera animation.

Two modes:
  dark         — dark vector basemap with star-field fog
  satellite_3d — ESRI satellite imagery + terrain DEM, real 3D mountains

Animation engine:
  - Speed follows actual GPS timing; speed-adaptive EMA bearing
  - Dynamic pitch and zoom: tilts up on climbs, zooms out at speed

Video export (WebCodecs + mp4-muxer):
  - Deterministic frame-by-frame encoding — no real-time capture, no frame drops
  - H.264 hardware acceleration via VideoEncoder; MP4 container via mp4-muxer
  - Landscape 16:9 or Portrait 9:16 at HD / 2K / 4K
  - map.once('idle') per frame guarantees all tiles are loaded before capture
  - Auto-downloads as .mp4; double-click Export to cancel mid-encoding
"""

import json
import threading
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



<script src="https://unpkg.com/maplibre-gl@3.6.2/dist/maplibre-gl.js"></script>
<script src="https://cdn.jsdelivr.net/npm/mp4-muxer@4.3.3/build/mp4-muxer.min.js"></script>
<script>
'use strict';

// ── Track & mode ─────────────────────────────────────────────────────────────
const TRACK       = TRACK_JSON;
const N           = TRACK.length;
const MAP_MODE    = 'MAP_MODE_INIT';
const ROUTE_NAME  = 'ACT_NAME';
const AUTO_EXPORT = 'AUTO_EXPORT_INIT' === 'true';
const RES_INIT    = 'RES_INIT_PY';   // HD | 2K | 4K — injected by Python

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
  bearings[i] = a > i
    ? calcBearing(TRACK[i][0],TRACK[i][1],TRACK[a][0],TRACK[a][1])
    : i > 0 ? bearings[i-1] : 0;
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
  maxTileCacheSize:      3000,   // must hold all pre-warmed tiles (~70 tiles/position × 90 positions)
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
// Encode-phase EMA state — null means raw/prewarm mode, set before the encode loop.
let _encSmBearing = null;
let _encSmPitch   = null;
let _encSmZoom    = null;
let lastTime      = null;
let playing       = true;
let animId        = null;
let _exportHwFailed = false;  // set true after a hw encode failure; forces software on retry

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

  if (playbackTime >= 1) playbackTime = 0;

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

  console.log('[ft] map loaded  mode=' + MAP_MODE + '  points=' + N + '  auto_export=' + AUTO_EXPORT);

  // Agent-triggered auto-export: starts recording immediately after map loads
  if (AUTO_EXPORT) setTimeout(() => exportFull(), 1500);
});

// ── WebCodecs video export ────────────────────────────────────────────────────
let isEncoding   = false;
let _origMapDims = null;

function roundRect(ctx, x, y, w, h, r) {
  ctx.beginPath();
  ctx.moveTo(x+r,y); ctx.lineTo(x+w-r,y); ctx.arcTo(x+w,y,x+w,y+r,r);
  ctx.lineTo(x+w,y+h-r); ctx.arcTo(x+w,y+h,x+w-r,y+h,r);
  ctx.lineTo(x+r,y+h); ctx.arcTo(x,y+h,x,y+h-r,r);
  ctx.lineTo(x,y+r); ctx.arcTo(x,y,x+r,y,r); ctx.closePath();
}

// Waits for MapLibre 'idle' — used during pre-warm where full quality matters.
// 'idle' fires only after ALL pending ops (including background prefetches) complete.
function waitForIdle(maxMs = 6000) {
  return new Promise(resolve => {
    let done = false;
    const finish = () => { if (!done) { done = true; resolve(); } };
    setTimeout(finish, maxMs);
    map.once('idle', finish);
    map.triggerRepaint();
  });
}

// Faster alternative used in the encode loop.
// Polls map.areTilesLoaded() — returns true as soon as all VISIBLE tiles are
// ready, WITHOUT waiting for off-screen background prefetches that 'idle' would.
// With a pre-warmed cache this resolves in one poll interval (~30–60 ms/frame).
// Initial delay is one rAF so MapLibre has had a render cycle to discover which
// tiles the new camera position needs before we query the loaded state.
// Wait for all visible tiles to finish loading, resolving on the render event that
// fires AFTER MapLibre has completed drawing to its WebGL canvas — so the canvas is
// guaranteed up-to-date when we resolve.  Uses map.on('render') instead of double-rAF
// so it costs one GPU frame (5–8 ms at 2K with vsync off) instead of two rAF ticks.
// A hard setTimeout fallback handles the rare case where no render event fires.
function waitForTiles(maxMs) {
  return new Promise(resolve => {
    const deadline = Date.now() + maxMs;
    let done = false;
    function finish() {
      if (done) return;
      done = true;
      map.off('render', afterRender);
      clearTimeout(timer);
      resolve();
    }
    function afterRender() {
      if (map.areTilesLoaded() || Date.now() >= deadline) {
        finish();
      } else {
        map.triggerRepaint();  // tiles still loading — ask for another render
      }
    }
    const timer = setTimeout(finish, maxMs);
    map.on('render', afterRender);
    map.triggerRepaint();
  });
}

// Advance animation to normalised time t ∈ [0,1] without the rAF loop.
// During export (_encSmBearing !== null) applies the same EMA smoothing as animStep
// so bearing/pitch/zoom transitions are glitch-free across every encoded frame.
// During prewarm (_encSmBearing === null) uses raw values for accurate tile loading.
function advanceToTime(t) {
  playbackTime = Math.max(0, Math.min(1, t));
  const progress = timeToIndex(playbackTime);
  const idx  = Math.floor(progress);
  const frac = progress - idx;
  const nxt  = Math.min(idx + 1, N - 1);
  const lon = lerp(TRACK[idx][0], TRACK[nxt][0], frac);
  const lat = lerp(TRACK[idx][1], TRACK[nxt][1], frac);
  const win = Math.min(16, Math.max(1, Math.round(N * 0.02)));
  const slopeAdj    = Math.max(-5, Math.min(5,
    (elevations[Math.min(idx+win,N-1)] - elevations[Math.max(idx-win,0)]) * 0.18));
  const pitchTarget   = Math.max(22, Math.min(83, pitchVal + slopeAdj));
  const zoomTarget    = zoomVal + (localSpeeds[idx] - 0.5) * -0.55;
  const bearingTarget = bearings[idx];
  let bearing, pitch, zoom;
  if (_encSmBearing !== null) {
    // EMA alphas scaled for encFps (2× the ~60 fps live-view values for 30 fps;
    // bear slightly more aggressive to match perceived animStep responsiveness).
    _encSmBearing = (_encSmBearing + bearingDiff(_encSmBearing, bearingTarget) * 0.07 + 360) % 360;
    _encSmPitch  += (pitchTarget - _encSmPitch) * 0.07;
    _encSmZoom   += (zoomTarget  - _encSmZoom)  * 0.05;
    bearing = _encSmBearing; pitch = _encSmPitch; zoom = _encSmZoom;
  } else {
    bearing = bearingTarget; pitch = pitchTarget; zoom = zoomTarget;
  }
  map.jumpTo({ center:[lon,lat], bearing, pitch, zoom });
  updateSources(idx, frac);
  updateStats(idx, frac);
}

// Draw elevation widget + progress bar + info card onto ctx at output resolution.
function compositeOverlays(ctx, t, W, H) {
  const curIdx = timeToIndex(t);
  const iLo = Math.floor(curIdx), iFrac = curIdx - iLo;
  const iHi = Math.min(iLo+1, N-1);

  const elW   = Math.min(Math.round(W*0.22), 380);
  const elH   = Math.max(52, Math.round(elW*0.30));
  const elPad = Math.round(W*0.012);
  ctx.save();
  ctx.translate(W - elW - elPad, H - elH - Math.round(H*0.025));
  drawElevToCtx(ctx, t, elW, elH);
  ctx.restore();

  const barH = Math.max(3, Math.round(W/400));
  const pg = ctx.createLinearGradient(0,0,W*t,0);
  pg.addColorStop(0,'#FC4C02'); pg.addColorStop(1,'#FF8C61');
  ctx.fillStyle = pg; ctx.fillRect(0,0,W*t,barH);

  const s   = Math.max(0.6, Math.min(3.0, W/700));
  const pad = Math.round(12*s);
  ctx.fillStyle = 'rgba(6,6,18,0.82)';
  roundRect(ctx, pad, pad+barH, Math.round(220*s), Math.round(64*s), Math.round(9*s));
  ctx.fill();

  const curElev = Math.round(lerp(elevations[iLo], elevations[iHi], iFrac));
  const tx  = pad + Math.round(10*s);
  const ty0 = pad + barH + Math.round(18*s);
  ctx.fillStyle = '#EEEEFF';
  ctx.font = `700 ${Math.round(12*s)}px system-ui,sans-serif`;
  ctx.fillText(ROUTE_NAME.substring(0,26), tx, ty0);
  const ty1 = ty0 + Math.round(20*s);
  ctx.fillStyle = '#FC4C02';
  ctx.font = `700 ${Math.round(13*s)}px system-ui,sans-serif`;
  ctx.fillText((t*totalDistKm).toFixed(2)+' km', tx,                    ty1);
  ctx.fillText(curElev+' m',                     tx+Math.round(90*s),   ty1);
  ctx.fillText(Math.round(t*100)+'%',            tx+Math.round(168*s),  ty1);
  const ty2 = ty1 + Math.round(13*s);
  ctx.fillStyle = '#9BA3C8';
  ctx.font = `${Math.round(9*s)}px system-ui,sans-serif`;
  ctx.fillText('Distance',  tx,                   ty2);
  ctx.fillText('Elevation', tx+Math.round(90*s),  ty2);
  ctx.fillText('Progress',  tx+Math.round(168*s), ty2);
}

// ── Full export: deterministic H.264 frame-by-frame encode via WebCodecs ─────
async function exportFull() {
  if (isEncoding) { isEncoding = false; return; }  // second click = cancel
  isEncoding = true;

  // Format and resolution come from Python-injected JS constants (no UI panel)
  const fmt    = 'ORIENTATION_INIT';
  const recRes = RES_INIT;
  const resMap = {
    landscape: { HD:[1920,1080], '2K':[2560,1440], '4K':[3840,2160] },
    portrait:  { HD:[1080,1920], '2K':[1440,2560], '4K':[2160,3840] },
  };
  const [physW, physH] = resMap[fmt][recRes];

  // Pause live animation
  playing = false;
  if (animId) { cancelAnimationFrame(animId); animId = null; }

  // Resize map canvas to target resolution.
  // Portrait DPR=3: narrow CSS width would otherwise load fewer tile columns;
  // forcing 3× makes MapLibre load tiles as if it were an ultra-retina display.
  const EXPORT_DPR = fmt === 'portrait' ? 3 : 2;
  const cssW = Math.round(physW / EXPORT_DPR);
  const cssH = Math.round(physH / EXPORT_DPR);

  const mapEl      = document.getElementById('map');
  const mapWrapper = document.getElementById('map-wrapper');
  _origMapDims = {
    mapCssText:     mapEl.style.cssText,
    wrapperCssText: mapWrapper.style.cssText,
    pixelRatio:     map.getPixelRatio ? map.getPixelRatio() : Math.min(2, window.devicePixelRatio||1),
  };
  map.setPixelRatio(EXPORT_DPR);
  mapEl.style.cssText = `position:absolute;top:0;left:0;width:${cssW}px;height:${cssH}px;`;
  map.resize();

  const viewW    = window.innerWidth, viewH = window.innerHeight;
  const fitScale = Math.min(viewW/cssW, viewH/cssH, 1.0);
  const offsetX  = Math.round((viewW - cssW*fitScale)/2);
  const offsetY  = Math.round((viewH - cssH*fitScale)/2);
  mapWrapper.style.cssText = `position:fixed;top:0;left:0;width:${cssW}px;height:${cssH}px;`+
    `transform:translate(${offsetX}px,${offsetY}px) scale(${fitScale});transform-origin:top left;z-index:50;`;
  if (MAP_MODE === 'satellite_3d') {
    try { map.setTerrain({ source:'terrain_dem', exaggeration: fmt==='portrait' ? 2.2 : 1.6 }); } catch(_) {}
  }

  // Status overlay
  const solay      = document.getElementById('status-overlay');
  const statusMsg  = document.getElementById('status-msg');
  const statusFill = document.getElementById('status-fill');
  solay.style.display = 'flex'; solay.style.opacity = '1';
  statusMsg.textContent = 'Preparing…'; statusFill.style.width = '0%';
  document.getElementById('rec-badge').style.display = 'flex';

  console.log('[ft] exportFull start  fmt=' + fmt + '  res=' + recRes + '  ' + physW + 'x' + physH);

  // Jump to start and wait for full tile + terrain load at export resolution
  map.jumpTo({ center:[TRACK[0][0],TRACK[0][1]], bearing:bearings[0], pitch:pitchVal, zoom:zoomVal });
  console.log('[ft] waiting for initial idle (12 s budget)…');
  await waitForIdle(12000);
  console.log('[ft] initial idle done');

  // ── Tile pre-warm: step through the full route before recording ───────────
  // Visits 91 evenly-spaced positions (t=0 … t=1) so tiles for the ENTIRE
  // route — including the start — are in MapLibre's 3000-tile in-memory cache
  // before the encode loop begins.  waitForTiles is used (not waitForIdle) so
  // we only wait for visible tiles, not off-screen prefetches.
  const PREWARM_STEPS = 90;
  const _pwT0 = Date.now();
  console.log('[ft] prewarm start  steps=' + PREWARM_STEPS + '  cacheSize=3000');
  for (let s = 0; s <= PREWARM_STEPS && isEncoding; s++) {
    advanceToTime(s / PREWARM_STEPS);
    await waitForTiles(4000);
    const pp = Math.round((s / PREWARM_STEPS) * 40);
    statusMsg.textContent  = `Pre-loading tiles… ${pp}%`;
    statusFill.style.width = pp + '%';
    if (s % 15 === 0 || s === PREWARM_STEPS) {
      console.log('[ft] prewarm ' + s + '/' + PREWARM_STEPS
        + '  areTilesLoaded=' + map.areTilesLoaded()
        + '  elapsed=' + ((Date.now()-_pwT0)/1000).toFixed(1) + 's');
    }
  }
  if (!isEncoding) {
    if (_origMapDims) {
      mapEl.style.cssText      = _origMapDims.mapCssText;
      mapWrapper.style.cssText = _origMapDims.wrapperCssText;
      map.setPixelRatio(_origMapDims.pixelRatio); map.resize();
      if (MAP_MODE === 'satellite_3d') try { map.setTerrain({source:'terrain_dem',exaggeration:1.6}); } catch(_) {}
      _origMapDims = null;
    }
    solay.style.opacity = '0';
    setTimeout(() => { solay.style.display='none'; solay.style.opacity='1'; statusFill.style.width='0%'; }, 450);
    document.getElementById('rec-badge').style.display = 'none';
    playing = true; lastTime = null; animId = requestAnimationFrame(animStep);
    return;
  }
  console.log('[ft] prewarm done  total=' + ((Date.now()-_pwT0)/1000).toFixed(1) + 's');

  // Return to start — tiles are warm from prewarm step s=0, so waitForTiles suffices
  advanceToTime(0);
  await waitForTiles(5000);

  // Setup VideoEncoder + mp4-muxer
  // Hardware (NVENC) easily sustains 60 fps capture; software path stays at 15 fps
  // to finish in a reasonable time (halved frame count vs 30 fps).
  const targetFps  = 60;
  const durSec     = parseInt(document.getElementById('speed').value);
  const bitrateMap = { HD:8_000_000, '2K':16_000_000, '4K':40_000_000 };
  const bitrate    = bitrateMap[recRes] || 16_000_000;

  // Two-stage hardware probe:
  //   Stage 1 — create a throwaway VideoEncoder (not connected to any muxer) and
  //             actually call configure() with prefer-hardware.  Wait 250 ms for
  //             the async error callback.  This is the only reliable way to know
  //             if NVENC/AMF/QuickSync will accept the resolution — isConfigSupported
  //             is documented as a hint and can lie.
  //   Stage 2 — now that we know the real hw availability, pick fps:
  //             hardware → targetFps (60 fps — NVENC handles it easily);
  //             software → 15 fps (half the frames vs 30, completes in ~300 s
  //             for a 30 s video; the per-frame MapLibre cost dominates anyway).
  //   Then create the muxer with the confirmed fps and the real encoder.
  const _hwTestConfig = {
    codec: 'avc1.640033', width: physW, height: physH,
    bitrate, framerate: targetFps, hardwareAcceleration: 'prefer-hardware',
    latencyMode: 'quality',
  };
  let hwAvailable = false;
  if (_exportHwFailed) {
    // A prior attempt already confirmed hw encode fails at this resolution — skip probe.
    console.log('[ft] hw probe skipped — previous hw failure flagged, using software');
  } else {
    let _hwFailed = false;
    const _testEnc = new VideoEncoder({
      output: () => {},
      error:  () => { _hwFailed = true; },
    });
    _testEnc.configure(_hwTestConfig);
    // Also encode a 1-pixel test frame: configure() alone can return OK on some
    // drivers (e.g. NVENC on portrait-4K) even when encode() will later fail.
    try {
      const _ptc = new OffscreenCanvas(physW, physH);
      _ptc.getContext('2d').fillRect(0, 0, 1, 1);
      const _ptf = new VideoFrame(_ptc, { timestamp: 0 });
      _testEnc.encode(_ptf);
      _ptf.close();
    } catch (_) { _hwFailed = true; }
    await new Promise(r => setTimeout(r, 1000));  // 1 s: wider window for async encoder error
    hwAvailable = !_hwFailed;
    try { _testEnc.close(); } catch(_) {}
    console.log('[ft] hw probe done  hwAvailable=' + hwAvailable);
  }

  const encFps      = hwAvailable ? targetFps : 15;
  const totalFrames = Math.round(durSec * encFps);
  console.log('[ft] encode start  frames=' + totalFrames + '  fps=' + encFps
    + '  durSec=' + durSec + '  hw=' + hwAvailable);

  const muxTarget = new Mp4Muxer.ArrayBufferTarget();
  const muxer = new Mp4Muxer.Muxer({
    target: muxTarget,
    video:  { codec:'avc', width:physW, height:physH, frameRate:encFps },
    fastStart: 'in-memory',
  });
  let encError = null;
  let encoder  = null;
  const enc = new VideoEncoder({
    output: (chunk, meta) => muxer.addVideoChunk(chunk, meta),
    error:  e => { encError = e; console.error('[flythrough] encode error', e); },
  });
  enc.configure({
    codec:                'avc1.640033',
    width:                physW,
    height:               physH,
    bitrate,
    framerate:            encFps,
    hardwareAcceleration: hwAvailable ? 'prefer-hardware' : 'prefer-software',
    latencyMode:          'quality',
  });
  await new Promise(r => setTimeout(r, 250));
  if (!encError) {
    encoder = enc;
    console.log('[ft] VideoEncoder ready  hw=' + (hwAvailable ? 'prefer-hardware' : 'prefer-software')
      + '  ' + physW + 'x' + physH + '  bitrate=' + (bitrate/1e6).toFixed(0) + 'Mbps'
      + '  fps=' + encFps);
  } else {
    console.error('[flythrough] real encoder configure failed — aborting');
    try { enc.close(); } catch(_) {}
    isEncoding = false;
  }

  // Seed encode-phase EMA at the exact frame-0 camera state so advanceToTime
  // starts smooth from the very first frame, with no cold-start drift.
  {
    const _i0  = Math.floor(timeToIndex(0));
    const _win = Math.min(16, Math.max(1, Math.round(N * 0.02)));
    const _sa  = Math.max(-5, Math.min(5,
      (elevations[Math.min(_i0+_win,N-1)] - elevations[Math.max(_i0-_win,0)]) * 0.18));
    _encSmBearing = bearings[_i0];
    _encSmPitch   = Math.max(22, Math.min(83, pitchVal + _sa));
    _encSmZoom    = zoomVal + (localSpeeds[_i0] - 0.5) * -0.55;
  }

  const outCanvas = new OffscreenCanvas(physW, physH);
  const outCtx    = outCanvas.getContext('2d');
  const _encT0    = Date.now();

  // Frame render loop — each frame waits for MapLibre idle before capture.
  // Pauses automatically when the tab is hidden (WebGL render loop is throttled
  // in background tabs, which would stall waitForIdle and corrupt frames).
  for (let i = 0; i <= totalFrames && isEncoding && encoder && !encError; i++) {
    // Pause while tab is hidden — resume automatically when user returns
    while (document.hidden && isEncoding) {
      statusMsg.textContent = 'Tab hidden — waiting for you to return…';
      await new Promise(r => setTimeout(r, 400));
    }
    if (!isEncoding) break;

    const t = i / totalFrames;
    advanceToTime(t);
    await waitForTiles(2000);  // cache-hit resolves in ~30–100 ms; 2 s fallback for edge tiles

    outCtx.drawImage(map.getCanvas(), 0, 0, physW, physH);
    compositeOverlays(outCtx, t, physW, physH);

    const vf = new VideoFrame(outCanvas, { timestamp: Math.round(i * 1_000_000 / encFps) });
    encoder.encode(vf, { keyFrame: i % Math.round(encFps) === 0 });
    vf.close();

    const pct = Math.round(40 + t * 60);  // 40–100%: pre-warm occupied 0–40%
    statusMsg.textContent  = `Encoding… ${pct}%`;
    statusFill.style.width = pct + '%';
    if (i % 90 === 0) {
      const fps_actual = i > 0 ? (i / ((Date.now() - _encT0) / 1000)).toFixed(1) : '—';
      console.log('[ft] frame ' + i + '/' + totalFrames
        + '  ' + pct + '%'
        + '  fps=' + fps_actual
        + '  elapsed=' + ((Date.now()-_encT0)/1000).toFixed(1) + 's');
    }
    if (i % 120 === 0) await new Promise(r => setTimeout(r, 0));  // drain NVENC output queue
  }

  if (!encError && isEncoding && encoder) {
    const encElapsed = ((Date.now() - _encT0) / 1000).toFixed(1);
    const avgFps     = (totalFrames / ((Date.now() - _encT0) / 1000)).toFixed(1);
    console.log('[ft] encode loop done  elapsed=' + encElapsed + 's  avg=' + avgFps + ' fps');
    statusMsg.textContent = 'Finalizing…';
    await encoder.flush();
    encoder.close();
    console.log('[ft] encoder flushed+closed');
    muxer.finalize();
    const mb = (muxTarget.buffer.byteLength / 1e6).toFixed(1);
    console.log('[ft] muxer finalized  size=' + mb + ' MB — triggering download');
    const blob = new Blob([muxTarget.buffer], { type:'video/mp4' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = `flythrough_${fmt}_${Date.now()}.mp4`;
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 8000);
    console.log('[ft] download link clicked');
  } else {
    console.log('[ft] encode aborted  encError=' + !!encError + '  isEncoding=' + isEncoding + '  hasEncoder=' + !!encoder);
    try { if (encoder) encoder.close(); } catch(_) {}
    // If hw encoder failed mid-encode (not a user cancel), flag it and auto-retry with software.
    if (encError && isEncoding && hwAvailable) {
      _exportHwFailed = true;
      console.log('[ft] hw encode failed — retrying with software encoder');
      setTimeout(exportFull, 200);
    }
  }

  // Disable encode EMA so live playback resumes with its own animStep EMA
  _encSmBearing = null; _encSmPitch = null; _encSmZoom = null;

  // Restore map
  if (_origMapDims) {
    mapEl.style.cssText      = _origMapDims.mapCssText;
    mapWrapper.style.cssText = _origMapDims.wrapperCssText;
    map.setPixelRatio(_origMapDims.pixelRatio);
    map.resize();
    if (MAP_MODE === 'satellite_3d') {
      try { map.setTerrain({ source:'terrain_dem', exaggeration:1.6 }); } catch(_) {}
    }
    _origMapDims = null;
  }
  solay.style.opacity = '0';
  setTimeout(() => { solay.style.display='none'; solay.style.opacity='1'; statusFill.style.width='0%'; }, 450);
  document.getElementById('rec-badge').style.display = 'none';
  isEncoding = false;

  // Restart live animation from beginning
  smoothZoom = zoomVal; smoothPitch = pitchVal; smoothBearing = bearings[0];
  playbackTime = 0; playing = true; lastTime = null;
  animId = requestAnimationFrame(animStep);
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

try { window.frameElement && window.frameElement.scrollIntoView({behavior:'smooth',block:'start'}); } catch(_){}
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
    resolution: str = "2K",
) -> str:
    """Build the self-contained HTML page for the flythrough animation.

    `auto_export=True` is used by the server-side renderer (Playwright) to start
    encoding automatically.  For the interactive preview iframe, pass False.
    """
    safe_name = (name.replace("\\", "\\\\")
                      .replace("'", "\\'")
                      .replace('"', '\\"')
                      .replace("<", "")
                      .replace(">", ""))
    valid_res = resolution if resolution in ("HD", "2K", "4K") else "2K"

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
        .replace("RES_INIT_PY",       valid_res)
    )


# ── Public entry point ────────────────────────────────────────────────────────

def show_flythrough(
    activity_id: int,
    activity_name: str = "",
    mode: Optional[str] = None,
    duration_sec: int = 0,
    orientation: str = "landscape",
    hidden: bool = False,
    resolution: str = "2K",
) -> None:
    """Render a 3D flythrough for an activity.

    hidden=True  — server-side render via Playwright; emits a download button.
    hidden=False — interactive preview iframe + Python export controls below.
    """
    import time as _time

    name = activity_name or f"Activity {activity_id}"

    # ── Load GPS track ────────────────────────────────────────────────────────
    try:
        if hidden:
            raw   = _fetch_track(activity_id)
            track = _prepare_track(raw)
        else:
            with st.spinner("Loading GPS track…"):
                raw   = _fetch_track(activity_id)
                track = _prepare_track(raw)
    except Exception as e:
        st.error(f"Could not load GPS data: {e}")
        return

    # ── Mode ──────────────────────────────────────────────────────────────────
    if mode is None:
        mode_label = st.radio(
            "Map style",
            ["Satellite 3D", "Dark Flat"],
            index=0,
            horizontal=True,
            key=f"flythrough_mode_{activity_id}",
            label_visibility="collapsed",
        )
        mode = "satellite_3d" if mode_label == "Satellite 3D" else "dark"

    # ── Hidden / agent-triggered: non-blocking server-side render ────────────
    if hidden:
        render_key = f"ft_video_{activity_id}_{orientation}_{resolution}_{duration_sec}"
        thread_key = render_key + "_thread"

        # Promote a completed background render into the permanent cache
        ti = st.session_state.get(thread_key)
        if ti and ti["status"] == "done":
            st.session_state[render_key] = ti["data"]
            del st.session_state[thread_key]
            ti = None

        video_bytes = st.session_state.get(render_key)

        if video_bytes is None:
            if ti is None:
                # First call — kick off background thread
                ti = {"status": "running", "data": None, "error": None}
                st.session_state[thread_key] = ti

                def _run(ti=ti):
                    try:
                        from ui.video_renderer import render_flythrough
                        ti["data"] = render_flythrough(
                            track, name,
                            mode=mode,
                            duration_sec=duration_sec,
                            orientation=orientation,
                            resolution=resolution,
                        )
                        ti["status"] = "done"
                    except Exception as exc:
                        ti["error"] = str(exc)
                        ti["status"] = "error"

                threading.Thread(target=_run, daemon=True).start()

            if ti["status"] == "error":
                st.error(f"Render failed: {ti['error']}")
                del st.session_state[thread_key]
                return

            # Still running — show status and poll every 3 s
            st.info(
                f"🎬 Rendering **{name}** "
                f"({duration_sec} s · {orientation} · {resolution}) in the background — "
                "keep chatting or exploring the dashboard!"
            )
            _time.sleep(3)
            st.rerun()
            return

        # ── Video ready ───────────────────────────────────────────────────────
        safe_fn = name.replace(" ", "_").replace("/", "-")[:40]
        # Constrain preview via column width — st.video fills its container and the
        # browser scales height proportionally, so a narrow column keeps portrait-4K
        # from filling the screen.  Download delivers the full-resolution file.
        # Portrait 9:16  → 28 % col  ≈ 336 px wide → ~597 px tall
        # Landscape 16:9 → 55 % col  ≈ 660 px wide → ~371 px tall
        if orientation == "portrait":
            vid_col, _ = st.columns([5, 13])
        else:
            vid_col, _ = st.columns([6, 5])
        with vid_col:
            st.video(video_bytes, format="video/mp4")
        st.download_button(
            label=f"⬇ Download full-quality MP4 — {name}",
            data=video_bytes,
            file_name=f"flythrough_{safe_fn}.mp4",
            mime="video/mp4",
            type="primary",
            key=f"ft_dl_{render_key}",
        )
        return

    # ── Visible: interactive preview iframe ───────────────────────────────────
    ele_values = [p[2] for p in track if p[2]]
    has_timing = any(p[3] is not None for p in track if len(p) > 3)
    ele_range  = f"{min(ele_values):.0f} – {max(ele_values):.0f} m" if ele_values else "—"

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("GPS Points",      f"{len(track):,}")
    c2.metric("Elevation Range", ele_range)
    c3.metric("Speed Data",      "✓ GPS timing" if has_timing else "uniform")
    c4.metric("Mode",            "Satellite 3D" if mode == "satellite_3d" else "Dark Flat")

    mode_str = "Satellite 3D · real terrain" if mode == "satellite_3d" else "Dark flat · star-field"
    st.caption(f"{'🌍' if mode == 'satellite_3d' else '🗺'} {mode_str} · "
               "Adjust pitch / zoom / duration in the preview, then export below.")

    st.iframe(
        _build_html(track, name, mode=mode, duration_sec=duration_sec),
        height=630,
    )

    # ── Export controls (Python-level, below the preview) ────────────────────
    st.markdown("**Export video**")
    ec1, ec2, ec3 = st.columns(3)
    exp_orient = ec1.radio(
        "Orientation", ["Landscape", "Portrait"],
        horizontal=True, key=f"ft_orient_{activity_id}",
    )
    exp_res = ec2.radio(
        "Resolution", ["HD", "2K", "4K"],
        horizontal=True, index=1, key=f"ft_res_{activity_id}",
    )
    exp_dur = ec3.slider(
        "Duration (s)", 30, 120, max(30, min(120, duration_sec or 60)),
        step=5, key=f"ft_dur_{activity_id}",
    )

    render_key = f"ft_video_{activity_id}_{exp_orient}_{exp_res}_{exp_dur}"
    thread_key = render_key + "_thread"

    # Promote completed background render into permanent cache
    ti = st.session_state.get(thread_key)
    if ti and ti["status"] == "done":
        st.session_state[render_key] = ti["data"]
        del st.session_state[thread_key]
        ti = None

    if st.button("Render & Export", type="primary", key=f"ft_render_{activity_id}",
                 disabled=bool(st.session_state.get(thread_key))):
        if render_key not in st.session_state and not st.session_state.get(thread_key):
            ti = {"status": "running", "data": None, "error": None}
            st.session_state[thread_key] = ti

            def _run(ti=ti):
                try:
                    from ui.video_renderer import render_flythrough
                    ti["data"] = render_flythrough(
                        track, name,
                        mode=mode,
                        duration_sec=exp_dur,
                        orientation=exp_orient.lower(),
                        resolution=exp_res,
                    )
                    ti["status"] = "done"
                except Exception as exc:
                    ti["error"] = str(exc)
                    ti["status"] = "error"

            threading.Thread(target=_run, daemon=True).start()
            st.rerun()

    # Status / result
    ti = st.session_state.get(thread_key)
    if ti:
        if ti["status"] == "error":
            st.error(f"Render failed: {ti['error']}")
            del st.session_state[thread_key]
        else:
            st.info(
                f"🎬 Rendering **{exp_dur} s · {exp_orient} · {exp_res}** in the background — "
                "keep exploring the dashboard!"
            )
            _time.sleep(3)
            st.rerun()

    if render_key in st.session_state:
        safe_fn = name.replace(" ", "_").replace("/", "-")[:40]
        # Same column constraint as the chat path — prevents portrait-4K from filling the screen
        if exp_orient == "Portrait":
            _vcol, _ = st.columns([5, 13])
        else:
            _vcol, _ = st.columns([6, 5])
        with _vcol:
            st.video(st.session_state[render_key], format="video/mp4")
        st.download_button(
            label=f"⬇ Download — {exp_orient} {exp_res} {exp_dur}s",
            data=st.session_state[render_key],
            file_name=f"flythrough_{safe_fn}_{exp_orient.lower()}_{exp_res}.mp4",
            mime="video/mp4",
            type="primary",
            key=f"ft_dl_{activity_id}_{render_key}",
        )
