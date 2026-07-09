// Shared Strava tool-result shapes used across the Analysis sections and the
// Dashboard signals. Confirmed against the live FastAPI seam.

export interface Activity {
  id: number;
  name: string;
  type?: string;
  sport_type?: string;
  date?: string;
  start_date?: string;
  distance_km?: number;
  moving_time_hours?: number;
  elevation_gain_m?: number;
  avg_speed_kmh?: number;
  avg_heart_rate?: number | null;
  map_polyline?: string;
  kudos?: number;
}

export interface ActivitiesResult {
  total_count?: number;
  activities?: Activity[];
  error?: string;
}

export interface SportTotals {
  count: number;
  distance_km: number;
  moving_time_hours: number;
  elevation_gain_m: number;
}

export interface PeriodStats {
  run?: SportTotals;
  ride?: SportTotals;
  swim?: SportTotals;
}

export interface OfficialStats {
  year_to_date?: PeriodStats;
  last_4_weeks?: PeriodStats;
  all_time?: PeriodStats;
  biggest_ride_distance_km?: number;
  biggest_climb_elevation_gain_m?: number;
}

export interface AthleteProfile {
  name?: string;
  firstname?: string;
  lastname?: string;
  city?: string;
  state?: string;
  country?: string;
  premium?: boolean;
  member_since?: string;
  created_at?: string;
  profile_url?: string;
  profile?: string;
  lat?: number;
  lon?: number;
}

export interface AthleteResult {
  profile?: AthleteProfile;
  official_stats?: OfficialStats;
}

export interface DeleteResult {
  success?: boolean;
  error?: string;
}

// ── Period definitions (mirror ui/dashboard.py _DASH_PERIODS) ────────────────────
export const PERIODS = [
  "All time",
  "1 year",
  "6 months",
  "3 months",
  "30 days",
  "14 days",
  "7 days",
] as const;
export type Period = (typeof PERIODS)[number];
export const PERIOD_DAYS: Record<Period, number> = {
  "All time": 0,
  "1 year": 365,
  "6 months": 180,
  "3 months": 90,
  "30 days": 30,
  "14 days": 14,
  "7 days": 7,
};

// ── Shared activity helpers ──────────────────────────────────────────────────────
export function sportOf(a: Activity): string {
  return a.sport_type || a.type || "Unknown";
}
export function dayStr(a: Activity): string {
  return a.date || (a.start_date || "").slice(0, 10) || "";
}
export function paceStr(avgSpeedKmh: number): string {
  if (avgSpeedKmh <= 0) return "-";
  const p = 60 / avgSpeedKmh;
  const min = Math.floor(p);
  const sec = Math.floor((p % 1) * 60);
  return `${min}:${String(sec).padStart(2, "0")} /km`;
}
