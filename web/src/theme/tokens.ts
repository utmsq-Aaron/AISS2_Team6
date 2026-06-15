// Colour + chart constants mirrored from AISS2_Team6/ui/styles.py, for use in
// JS contexts (Plotly figures, MapLibre layers) where Tailwind classes can't reach.

export const ACCENT = "#FC4C02";
export const STRAVA_ORANGE = ACCENT;

export const BG_CARD = "#0F0F1E";
export const BG_SURFACE = "#16162A";
export const BORDER = "#2A2A45";

export const TEXT_PRIMARY = "#EEEEFF";
export const TEXT_MUTED = "#9BA3C8";

export const C_GREEN = "#22C55E";
export const C_ROSE = "#FB7185";
export const C_INDIGO = "#818CF8";
export const C_CYAN = "#22D3EE";
export const C_PURPLE = "#C084FC";
export const C_AMBER = "#FCD34D";
export const C_RED = "#EF4444";
export const C_ORANGE = ACCENT;

// Sport-breakdown palette (styles.CHART_COLORS)
export const CHART_COLORS = [
  C_ORANGE, "#3B82F6", C_GREEN, "#8B5CF6",
  C_AMBER, "#EC4899", C_CYAN, "#84CC16",
];

// Dark CARTO basemap raster — same tiles as styles.DARK_MAP_TILES
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
