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
export const C_AMBER = "#F59E0B"; // status caution — amber (HRV / warnings)
export const C_ROSE = "#FB7185"; // heart rate
export const C_INDIGO = "#818CF8"; // sleep
export const C_CYAN = "#22D3EE"; // steps
export const C_PURPLE = "#C084FC"; // stress
export const C_RED = "#EF4444";

// Chart palette — led by the two brand accents (teal, orange), then a distinct set.
export const CHART_COLORS = [
  ACCENT, ACCENT_SECONDARY, C_GREEN, "#3B82F6",
  C_PURPLE, C_AMBER, C_CYAN, "#EC4899",
];

// Dark CARTO basemap raster — reads well on the deep-navy canvas.
export const DARK_MAP_TILES = "https://a.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}.png";
export const DARK_MAP_ATTR =
  '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> © <a href="https://carto.com/attributions">CARTO</a>';

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
