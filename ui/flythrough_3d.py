"""3D Activity Flythrough — MapLibre GL JS cinematic camera animation."""

import json
from typing import List

import streamlit as st


# ── Data pipeline ─────────────────────────────────────────────────────────────

def _fetch_track(activity_id: int) -> List[List[float]]:
    from ui.activity_analysis import _load_streams
    data = _load_streams(activity_id)
    points = data.get("points", [])
    if not points:
        raise ValueError("No GPS stream data for this activity.")
    return [
        [p["lon"], p["lat"], p.get("ele") or 0.0]
        for p in points
        if p.get("lat") is not None and p.get("lon") is not None
    ]


def _downsample(pts: List[List[float]], max_pts: int = 500) -> List[List[float]]:
    if len(pts) <= max_pts:
        return pts
    step = len(pts) / max_pts
    return [pts[int(i * step)] for i in range(max_pts)]


def _smooth(pts: List[List[float]], window: int = 2) -> List[List[float]]:
    n = len(pts)
    out = []
    for i in range(n):
        lo, hi = max(0, i - window), min(n, i + window + 1)
        ch = pts[lo:hi]
        out.append([sum(p[k] for p in ch) / len(ch) for k in range(3)])
    return out


def _prepare_track(raw: List[List[float]]) -> List[List[float]]:
    return _smooth(_downsample(raw, 500), 2)


# ── HTML ──────────────────────────────────────────────────────────────────────

_HTML = """<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<link rel="stylesheet" href="https://unpkg.com/maplibre-gl@3.6.2/dist/maplibre-gl.css">
<style>
* { margin:0; padding:0; box-sizing:border-box; }
html,body { width:100%; height:100%; background:#0F0F1E; overflow:hidden; }
#map { width:100%; height:100%; }

#loading-overlay {
  position:absolute; inset:0; background:#0F0F1E;
  display:flex; flex-direction:column; align-items:center;
  justify-content:center; gap:16px; z-index:100; transition:opacity .4s;
}
#loading-overlay p { color:#9BA3C8; font-family:system-ui,sans-serif; font-size:13px; }
.load-track { width:200px; height:4px; background:#1E1E38; border-radius:4px; overflow:hidden; }
.load-fill {
  height:100%; width:30%;
  background:linear-gradient(90deg,#FC4C02,#FF8C61); border-radius:4px;
  animation:sweep 1.4s ease-in-out infinite;
}
@keyframes sweep {
  0%   { margin-left:0%;  width:30%; }
  50%  { margin-left:70%; width:30%; }
  100% { margin-left:0%;  width:30%; }
}

#progress-bar {
  position:absolute; top:0; left:0; height:8px; width:0%;
  background:linear-gradient(90deg,#FC4C02,#FF8C61);
  border-radius:0 5px 5px 0;
  box-shadow:0 0 14px rgba(252,76,2,0.8),0 0 4px rgba(252,76,2,0.5);
  transition:width .25s ease-out; z-index:20;
}

#info-card {
  position:absolute; top:14px; left:14px;
  background:rgba(10,10,24,0.88); backdrop-filter:blur(14px);
  border:1px solid rgba(252,76,2,0.45); border-radius:12px;
  padding:12px 16px; z-index:20; min-width:200px;
}
#act-name { color:#EEEEFF; font-weight:700; font-size:15px;
            font-family:system-ui,sans-serif; margin-bottom:8px; }
.stat-row { display:flex; gap:18px; flex-wrap:wrap; }
.stat { color:#9BA3C8; font-size:11px; font-family:system-ui,sans-serif; }
.stat b { color:#EEEEFF; display:block; font-size:13px; font-weight:700; }

#controls {
  position:absolute; bottom:18px; left:50%; transform:translateX(-50%);
  background:rgba(10,10,24,0.88); backdrop-filter:blur(14px);
  border:1px solid rgba(252,76,2,0.35); border-radius:14px;
  padding:10px 20px; display:flex; align-items:center; gap:18px;
  z-index:20; white-space:nowrap;
}
.ctrl-btn {
  background:#FC4C02; border:none; color:#fff; border-radius:8px;
  padding:7px 18px; cursor:pointer; font-size:13px; font-weight:600;
  font-family:system-ui,sans-serif; transition:background .15s;
}
.ctrl-btn:hover { background:#e04400; }
.ctrl-label {
  color:#9BA3C8; font-size:11px; font-family:system-ui,sans-serif;
  display:flex; flex-direction:column; gap:4px;
}
input[type=range] { width:90px; accent-color:#FC4C02; cursor:pointer; }

#elevation-bar {
  position:absolute; bottom:72px; left:0; right:0; height:56px;
  pointer-events:none; z-index:10;
}
.maplibregl-ctrl-bottom-right,
.maplibregl-ctrl-bottom-left { display:none !important; }
</style>
</head>
<body>

<div id="loading-overlay">
  <p>Loading 3D Map…</p>
  <div class="load-track"><div class="load-fill"></div></div>
</div>

<div id="progress-bar"></div>
<div id="map"></div>
<canvas id="elevation-bar"></canvas>

<div id="info-card">
  <div id="act-name">ACT_NAME</div>
  <div class="stat-row">
    <div class="stat"><b id="stat-dist">0.00 km</b>Distance</div>
    <div class="stat"><b id="stat-elev">0 m</b>Elevation</div>
    <div class="stat"><b id="stat-progress">0%</b>Progress</div>
  </div>
</div>

<div id="controls">
  <button class="ctrl-btn" id="play-btn">&#9646;&#9646; Pause</button>
  <label class="ctrl-label">Speed
    <input type="range" id="speed" min="1" max="30" value="SPEED_DEFAULT">
  </label>
  <label class="ctrl-label">Tilt
    <input type="range" id="pitch" min="20" max="85" value="65">
  </label>
  <label class="ctrl-label">Height
    <input type="range" id="zoom" min="13" max="18" value="15" step="0.5">
  </label>
</div>

<script src="https://unpkg.com/maplibre-gl@3.6.2/dist/maplibre-gl.js"></script>
<script>
const TRACK = TRACK_JSON;
const N = TRACK.length;
const elevations = TRACK.map(p => p[2] || 0);
const minElev = Math.min.apply(null, elevations);
const maxElev = Math.max.apply(null, elevations);
const elevRange = maxElev - minElev || 1;

const totalDistKm = (() => {
  let d = 0;
  for (let i = 1; i < N; i++) {
    const dx = (TRACK[i][0]-TRACK[i-1][0]) * 111320 * Math.cos(TRACK[i][1]*Math.PI/180);
    const dy = (TRACK[i][1]-TRACK[i-1][1]) * 110540;
    d += Math.sqrt(dx*dx + dy*dy);
  }
  return d / 1000;
})();

function calcBearing(lon1,lat1,lon2,lat2) {
  const toR = d => d*Math.PI/180;
  const dL = toR(lon2-lon1);
  const y = Math.sin(dL)*Math.cos(toR(lat2));
  const x = Math.cos(toR(lat1))*Math.sin(toR(lat2))
           -Math.sin(toR(lat1))*Math.cos(toR(lat2))*Math.cos(dL);
  return (Math.atan2(y,x)*180/Math.PI+360)%360;
}

const bearings = new Float32Array(N);
for (let i = 0; i < N-1; i++)
  bearings[i] = calcBearing(TRACK[i][0],TRACK[i][1],TRACK[Math.min(i+5,N-1)][0],TRACK[Math.min(i+5,N-1)][1]);
bearings[N-1] = bearings[N-2]||0;

// Auto-scroll iframe into view
try { window.frameElement && window.frameElement.scrollIntoView({behavior:'smooth',block:'start'}); } catch(e){}

// ── MapLibre init ──────────────────────────────────────────────────────────
const map = new maplibregl.Map({
  container: 'map',
  style: 'https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json',
  center: [TRACK[0][0], TRACK[0][1]],
  zoom: 15,
  pitch: 65,
  bearing: bearings[0],
  antialias: true,
  attributionControl: false,
});

const overlay = document.getElementById('loading-overlay');
const pb = document.getElementById('progress-bar');

let frame=0, playing=true, animId=null, lastStep=0;
let speedVal = parseInt(document.getElementById('speed').value);
let pitchVal = parseInt(document.getElementById('pitch').value);
let zoomVal  = parseFloat(document.getElementById('zoom').value);

// ── Elevation canvas ───────────────────────────────────────────────────────
const elCanvas = document.getElementById('elevation-bar');
const elCtx    = elCanvas.getContext('2d');

function drawElevProfile(idx) {
  elCanvas.width  = window.innerWidth;
  elCanvas.height = 56;
  const W=elCanvas.width, H=elCanvas.height;
  elCtx.clearRect(0,0,W,H);
  elCtx.beginPath();
  for (let i=0;i<N;i++) {
    const x=(i/(N-1))*W;
    const y=H-((elevations[i]-minElev)/elevRange)*(H-6)-3;
    i===0?elCtx.moveTo(x,y):elCtx.lineTo(x,y);
  }
  elCtx.lineTo(W,H); elCtx.lineTo(0,H); elCtx.closePath();
  const g=elCtx.createLinearGradient(0,0,0,H);
  g.addColorStop(0,'rgba(252,76,2,0.55)'); g.addColorStop(1,'rgba(252,76,2,0.04)');
  elCtx.fillStyle=g; elCtx.fill();
  const px=(idx/(N-1))*W;
  elCtx.fillStyle='rgba(252,76,2,0.20)'; elCtx.fillRect(0,0,px,H);
  elCtx.beginPath(); elCtx.moveTo(px,0); elCtx.lineTo(px,H);
  elCtx.strokeStyle='#fff'; elCtx.lineWidth=2; elCtx.stroke();
}

function updateStats(idx) {
  const pct=idx/(N-1);
  document.getElementById('stat-dist').textContent     = (pct*totalDistKm).toFixed(2)+' km';
  document.getElementById('stat-elev').textContent     = Math.round(elevations[idx])+' m';
  document.getElementById('stat-progress').textContent = Math.round(pct*100)+'%';
  pb.style.width = (pct*100)+'%';
}

// ── Update sources each frame ──────────────────────────────────────────────
function updateFrame(idx) {
  if (!map.getSource('done')) return;

  map.getSource('done').setData({
    type:'Feature',
    geometry:{ type:'LineString', coordinates: TRACK.slice(0,idx+1).map(p=>[p[0],p[1]]) }
  });
  map.getSource('dot').setData({
    type:'Feature',
    geometry:{ type:'Point', coordinates:[TRACK[idx][0],TRACK[idx][1]] }
  });

  const interval = Math.max(50, 800/speedVal);
  map.easeTo({
    center:[TRACK[idx][0],TRACK[idx][1]],
    bearing: bearings[idx],
    pitch:   pitchVal,
    zoom:    zoomVal,
    duration: interval*1.6,
    easing: t=>t,
  });

  updateStats(idx);
  drawElevProfile(idx);
}

function animLoop(ts) {
  if (!playing) { animId=null; return; }
  const interval = Math.max(50, 800/speedVal);
  if (ts-lastStep >= interval) {
    lastStep=ts;
    frame=(frame+1)%N;
    updateFrame(frame);
  }
  animId=requestAnimationFrame(animLoop);
}

// ── Add layers once map style has loaded ──────────────────────────────────
map.on('load', () => {
  overlay.style.opacity='0';
  setTimeout(()=>overlay.remove(),400);

  const fullCoords = TRACK.map(p=>[p[0],p[1]]);

  // Ghost route (full path, dim white)
  map.addSource('ghost',{type:'geojson',data:{type:'Feature',geometry:{type:'LineString',coordinates:fullCoords}}});
  map.addLayer({id:'ghost-line',type:'line',source:'ghost',
    layout:{'line-join':'round','line-cap':'round'},
    paint:{'line-color':'rgba(255,255,255,0.20)','line-width':3}});

  // Glow underneath completed route
  map.addSource('done',{type:'geojson',data:{type:'Feature',geometry:{type:'LineString',coordinates:[fullCoords[0]]}}});
  map.addLayer({id:'done-glow',type:'line',source:'done',
    layout:{'line-join':'round','line-cap':'round'},
    paint:{'line-color':'#FC4C02','line-width':14,'line-opacity':0.18,'line-blur':6}});
  map.addLayer({id:'done-line',type:'line',source:'done',
    layout:{'line-join':'round','line-cap':'round'},
    paint:{'line-color':'#FC4C02','line-width':5,'line-blur':0.5}});

  // Start / finish dots
  map.addSource('pins',{type:'geojson',data:{type:'FeatureCollection',features:[
    {type:'Feature',properties:{c:'#2ECC71'},geometry:{type:'Point',coordinates:fullCoords[0]}},
    {type:'Feature',properties:{c:'#E74C3C'},geometry:{type:'Point',coordinates:fullCoords[N-1]}},
  ]}});
  map.addLayer({id:'pin-dots',type:'circle',source:'pins',paint:{
    'circle-radius':7,'circle-color':['get','c'],
    'circle-stroke-width':2,'circle-stroke-color':'#fff',
  }});

  // Current-position dot
  map.addSource('dot',{type:'geojson',data:{type:'Feature',geometry:{type:'Point',coordinates:fullCoords[0]}}});
  map.addLayer({id:'dot-glow',type:'circle',source:'dot',paint:{
    'circle-radius':18,'circle-color':'#FC4C02','circle-opacity':0.22,'circle-blur':1,
  }});
  map.addLayer({id:'dot-core',type:'circle',source:'dot',paint:{
    'circle-radius':8,'circle-color':'#fff',
    'circle-stroke-width':3,'circle-stroke-color':'#FC4C02',
  }});

  drawElevProfile(0);
  updateStats(0);

  // Brief intro pan, then start
  setTimeout(()=>{ lastStep=0; animId=requestAnimationFrame(animLoop); }, 1200);
});

// Handle style load errors — fall back to OSM raster style
map.on('error', e => {
  if (e.error && e.error.status === 403) {
    map.setStyle({
      version:8,
      sources:{ osm:{type:'raster',tiles:['https://tile.openstreetmap.org/{z}/{x}/{y}.png'],tileSize:256} },
      layers:[{id:'osm',type:'raster',source:'osm'}]
    });
  }
});

// ── Controls ──────────────────────────────────────────────────────────────
document.getElementById('play-btn').addEventListener('click',()=>{
  playing=!playing;
  document.getElementById('play-btn').innerHTML=playing?'&#9646;&#9646; Pause':'&#9654; Play';
  if (playing) { lastStep=0; animId=requestAnimationFrame(animLoop); }
  else if (animId) { cancelAnimationFrame(animId); animId=null; }
});
document.getElementById('speed').addEventListener('input',e=>{ speedVal=parseInt(e.target.value); });
document.getElementById('pitch').addEventListener('input',e=>{ pitchVal=parseInt(e.target.value); map.easeTo({pitch:pitchVal,duration:300}); });
document.getElementById('zoom').addEventListener('input', e=>{ zoomVal=parseFloat(e.target.value); map.easeTo({zoom:zoomVal,duration:300}); });
window.addEventListener('resize',()=>drawElevProfile(frame));
</script>
</body>
</html>"""


def _build_html(track: List[List[float]], name: str, speed_default: int = 12) -> str:
    safe = name.replace('"', '\\"').replace("<", "").replace(">", "")
    return (
        _HTML
        .replace("ACT_NAME",      safe)
        .replace("TRACK_JSON",    json.dumps(track))
        .replace("SPEED_DEFAULT", str(speed_default))
    )


# ── Public entry point ────────────────────────────────────────────────────────

def show_flythrough(activity_id: int, activity_name: str = "") -> None:
    name = activity_name or f"Activity {activity_id}"

    with st.spinner("Loading GPS track…"):
        try:
            raw   = _fetch_track(activity_id)
            track = _prepare_track(raw)
        except Exception as e:
            st.error(f"Could not load GPS data: {e}")
            return

    ele_values = [p[2] for p in track if p[2]]
    ele_range  = f"{min(ele_values):.0f} – {max(ele_values):.0f} m" if ele_values else "—"

    col1, col2, col3 = st.columns(3)
    col1.metric("GPS Points",      f"{len(track):,}")
    col2.metric("Elevation Range", ele_range)
    col3.metric("Raw → Smoothed",  f"{len(raw):,} → {len(track):,} pts")

    st.caption("🖱 Drag to pan · Scroll to zoom · Controls bottom-centre")
    st.iframe(_build_html(track, name), height=600)
