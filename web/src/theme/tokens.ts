// Colour + chart constants for the "Training Copilot Professional" design system.
// Premium dark mode: deep-navy canvas, dark-slate cards, vibrant-teal primary
// accent with an energizing-orange secondary. Mirrored into tailwind.config.js
// (Tailwind classes) and used directly here for Plotly figures + MapLibre layers.

// ── Accents ───────────────────────────────────────────────────────────────────
export const ACCENT = "#2DD4BF"; // primary — vibrant teal
export const ACCENT_HOVER = "#14B8A6";
export const ACCENT_SECONDARY = "#F97316"; // secondary — energizing orange
export const STRAVA_ORANGE = ACCENT_SECONDARY; // Strava / route / activity orange
export const C_ORANGE = ACCENT_SECONDARY;

// ── Surfaces ──────────────────────────────────────────────────────────────────
export const BG_APP = "#0B1219"; // deep navy canvas
export const BG_CARD = "#16212B"; // dark slate cards
export const BG_SURFACE = "#16212B";
export const BORDER = "#1E293B";

// ── Text ──────────────────────────────────────────────────────────────────────
export const TEXT_PRIMARY = "#F8FAFC"; // off-white
export const TEXT_MUTED = "#94A3B8"; // muted gray

// ── Status + per-metric chart colours ───────────────────────────────────────────
export const C_GREEN = "#10B981"; // status positive — emerald (body battery / good)
export const C_GREEN_DARK = "#16A34A"; // deeper green — "one of your easiest" assessment
export const C_AMBER = "#F59E0B"; // status caution — amber (HRV / warnings)
export const C_ROSE = "#FB7185"; // heart rate
export const C_INDIGO = "#818CF8"; // sleep
export const C_CYAN = "#22D3EE"; // steps
export const C_PURPLE = "#C084FC"; // stress
export const C_RED = "#EF4444";
export const C_BLUE = "#3B82F6"; // blue-500 — CTL / "not on Strava" / chart series
export const C_BLUE_LIGHT = "#60A5FA"; // blue-400 — badge text, light sleep

// Text (extra) + neutrals
export const TEXT_FAINT = "#64748B"; // slate-500 — faintest label row
export const WHITE = "#FFFFFF";

// Chart palette — led by the two brand accents (teal, orange), then a distinct set.
export const CHART_COLORS = [
  ACCENT, ACCENT_SECONDARY, C_GREEN, C_BLUE,
  C_PURPLE, C_AMBER, C_CYAN, "#EC4899",
];

// ── Sleep-stage colours (mirrors ui/health.py) ──────────────────────────────────
export const C_SLEEP_DEEP = "#1E40AF";
export const C_SLEEP_REM = "#7C3AED";
export const C_SLEEP_LIGHT = C_BLUE_LIGHT;
export const C_SLEEP_AWAKE = "#ED79D5";

// ── Map / route group ───────────────────────────────────────────────────────────
// Mirrors core/route_render.py so the web map and the Telegram static map stay
// in visual parity — the same route rendered in either place looks identical.
export const ISO_BLUE = "#1E96FF"; // isochrone fill / route accent
export const ISO_BLUE_DARK = "#0050AA"; // isochrone outline
export const MAP_START = "#2ECC71"; // start marker (folium green)
export const MAP_FINISH = "#E74C3C"; // finish marker (folium red)
export const TRAIL_COLORS = [ACCENT_SECONDARY, ISO_BLUE, "#00C864", "#C832C8", "#FFC800"];

// ── Metric-gradient stops (HR/pace/etc. route overlay legend + line) ────────────
// Data-coupled: gradientColor() interpolates between these exact endpoints, and
// the Legend's CSS gradient reuses them, so legend and line can never drift.
export const GRAD_LOW = "#22C55E"; // low end of the metric (bottom of legend)
export const GRAD_MID = "#FCDA4D"; // midpoint
export const GRAD_HIGH = C_RED; // high end (top of legend)

// Dark CARTO basemap raster — reads well on the deep-navy canvas.
export const DARK_MAP_TILES = "https://a.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}.png";
export const DARK_MAP_ATTR =
  '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> © <a href="https://carto.com/attributions">CARTO</a>';

// Standard OpenStreetMap raster ("Map" mode).
export const OSM_MAP_TILES = "https://tile.openstreetmap.org/{z}/{x}/{y}.png";
export const OSM_MAP_ATTR = "© OpenStreetMap contributors";

// Esri World Imagery satellite raster — same source as the 3D flythrough.
export const SATELLITE_MAP_TILES =
  "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}";
export const SATELLITE_MAP_ATTR = "© Esri, Maxar, GeoEye, Earthstar Geographics";

// Activity icons (styles.ACTIVITY_ICONS)
export const ACTIVITY_ICONS: Record<string, string> = {
  Run: "🏃", Ride: "🚴", Hike: "🥾", Walk: "🚶",
  Swim: "🏊", Workout: "💪", WeightTraining: "🏋️",
  Yoga: "🧘", EBikeRide: "⚡", VirtualRide: "🖥️",
  VirtualRun: "🖥️", NordicSki: "⛷️", AlpineSki: "⛷️",
  BackcountrySki: "⛷️", IceSkate: "⛸️", Rowing: "🚣",
  Kayaking: "🛶", StandUpPaddling: "🏄", Soccer: "⚽",
  Tennis: "🎾", RockClimbing: "🧗", Crossfit: "💪",
};

export function activityIcon(sport?: string): string {
  return (sport && ACTIVITY_ICONS[sport]) || "🏅";
}
