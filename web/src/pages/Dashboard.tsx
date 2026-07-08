// Dashboard tab — athlete header, weather, key metrics, activity map + analysis,
// training charts, and official Strava stats. Faithful port of ui/dashboard.py.

import type { Data } from "plotly.js";
import type { ReactNode } from "react";
import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { ActivityAnalysis } from "../components/dashboard/ActivityAnalysis";
import FlythroughModal from "../components/FlythroughModal";
import { MetricCard } from "../components/MetricCard";
import { PageHeader } from "../components/PageHeader";
import { PeriodSelector } from "../components/PeriodSelector";
import { PlotlyChart } from "../components/PlotlyChart";
import { RouteMap, type MarkerSpec, type PolyLineSpec } from "../components/RouteMap";
import { Spinner, ErrorBox, EmptyState } from "../components/Spinner";
import { callTool } from "../lib/api";
import { decodePolyline } from "../lib/format";
import { useUiStore } from "../store/uiStore";
import { ACCENT, C_AMBER, CHART_COLORS, activityIcon } from "../theme/tokens";

// Karlsruhe — used when the athlete profile gives no coords.
const KARLSRUHE = { lat: 49.0069, lon: 8.4037 };

// ── Types (confirmed via live API) ──────────────────────────────────────────
interface Activity {
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
interface ActivitiesResult {
  total_count?: number;
  activities?: Activity[];
  error?: string;
}
interface SportTotals {
  count: number;
  distance_km: number;
  moving_time_hours: number;
  elevation_gain_m: number;
}
interface PeriodStats {
  run?: SportTotals;
  ride?: SportTotals;
  swim?: SportTotals;
}
interface OfficialStats {
  year_to_date?: PeriodStats;
  last_4_weeks?: PeriodStats;
  all_time?: PeriodStats;
  biggest_ride_distance_km?: number;
  biggest_climb_elevation_gain_m?: number;
}
interface AthleteProfile {
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
interface AthleteResult {
  profile?: AthleteProfile;
  official_stats?: OfficialStats;
}
interface CurrentWeather {
  location: string;
  temperature_c: number;
  wind_speed_kmh: number;
  weather_code: number;
  weather_condition: string;
}
interface UvIndex {
  location: string;
  uv_index: number;
  risk: string;
}
interface PollenLevels {
  location: string;
  pollen: Record<string, { value_grains_m3: number; level: string }>;
}
interface DeleteResult {
  success?: boolean;
  error?: string;
}

// ── Period definitions (ui/dashboard.py _DASH_PERIODS) ───────────────────────
const PERIODS = ["All time", "1 year", "6 months", "3 months", "30 days", "14 days", "7 days"] as const;
type Period = (typeof PERIODS)[number];
const PERIOD_DAYS: Record<Period, number> = {
  "All time": 0,
  "1 year": 365,
  "6 months": 180,
  "3 months": 90,
  "30 days": 30,
  "14 days": 14,
  "7 days": 7,
};

// ── WMO / risk lookup tables (ui/dashboard.py) ───────────────────────────────
const WMO: Record<number, string> = {
  0: "☀️ Clear", 1: "🌤️ Mainly clear", 2: "⛅ Partly cloudy", 3: "☁️ Overcast",
  45: "🌫️ Foggy", 48: "🌫️ Foggy",
  51: "🌦️ Light drizzle", 53: "🌦️ Drizzle", 55: "🌧️ Dense drizzle",
  61: "🌧️ Light rain", 63: "🌧️ Rain", 65: "🌧️ Heavy rain",
  71: "🌨️ Light snow", 73: "🌨️ Snow", 75: "❄️ Heavy snow",
  80: "🌦️ Rain showers", 81: "🌧️ Rain showers", 82: "⛈️ Violent showers",
  95: "⛈️ Thunderstorm", 96: "⛈️ Thunderstorm", 99: "⛈️ Thunderstorm",
};
const UV_RISK: Record<string, string> = {
  low: "🟢", moderate: "🟡", high: "🟠", "very high": "🔴", extreme: "🟣",
};
const POL_RISK: Record<string, string> = {
  none: "🟢", low: "🟡", moderate: "🟠", high: "🔴", "very high": "🟣",
};

// ── Helpers ──────────────────────────────────────────────────────────────────
function sportOf(a: Activity): string {
  return a.sport_type || a.type || "Unknown";
}
function dayStr(a: Activity): string {
  return a.date || (a.start_date || "").slice(0, 10) || "";
}
function paceStr(avgSpeedKmh: number): string {
  if (avgSpeedKmh <= 0) return "-";
  const p = 60 / avgSpeedKmh;
  const min = Math.floor(p);
  const sec = Math.floor((p % 1) * 60);
  return `${min}:${String(sec).padStart(2, "0")} /km`;
}
function decodeRoute(a: Activity): [number, number][] {
  return decodePolyline(a.map_polyline);
}
function titleCase(s: string): string {
  return s ? s.charAt(0).toUpperCase() + s.slice(1) : s;
}

// Aggregate key for adaptive period bucketing (mirrors to_df: day/week/month).
function isoWeek(d: Date): string {
  // %Y-W%W style: week of year, Sunday-based (matches Python %W loosely enough for display)
  const start = new Date(Date.UTC(d.getUTCFullYear(), 0, 1));
  const days = Math.floor((d.getTime() - start.getTime()) / 86400000);
  const week = Math.floor((days + start.getUTCDay()) / 7);
  return `${d.getUTCFullYear()}-W${String(week).padStart(2, "0")}`;
}
function bucketKey(d: Date, col: "day" | "week" | "month"): string {
  const y = d.getUTCFullYear();
  const m = String(d.getUTCMonth() + 1).padStart(2, "0");
  const day = String(d.getUTCDate()).padStart(2, "0");
  if (col === "day") return `${y}-${m}-${day}`;
  if (col === "month") return `${y}-${m}`;
  return isoWeek(d);
}

export function Dashboard() {
  const sportFilter = useUiStore((s) => s.sportFilter);
  const refreshVersion = useUiStore((s) => s.refreshVersion);
  const queryClient = useQueryClient();

  const [period, setPeriod] = useState<Period>("30 days");
  const [search, setSearch] = useState("");
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [flythroughOpen, setFlythroughOpen] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [statsTab, setStatsTab] = useState<"ytd" | "lfw" | "all">("ytd");

  const loadDays = PERIOD_DAYS[period];

  // ── Data: activities (refetched when period changes — mirrors load_activities(days)) ──
  const activitiesQ = useQuery({
    queryKey: ["activities", loadDays, refreshVersion],
    queryFn: () => {
      const args: Record<string, unknown> =
        loadDays > 0
          ? {
              limit: Math.min(loadDays * 3, 400),
              start_date: new Date(Date.now() - loadDays * 86400000).toISOString().slice(0, 10),
            }
          : { limit: 500 };
      return callTool<ActivitiesResult>("strava__get_activities", args);
    },
  });

  const athleteQ = useQuery({
    queryKey: ["athlete", refreshVersion],
    queryFn: () => callTool<AthleteResult>("strava__get_athlete_profile", {}),
  });

  // ── Data: weather (Karlsruhe or athlete coords) ──
  const coords = {
    lat: athleteQ.data?.profile?.lat ?? KARLSRUHE.lat,
    lon: athleteQ.data?.profile?.lon ?? KARLSRUHE.lon,
  };
  const weatherQ = useQuery({
    queryKey: ["dash-weather", refreshVersion, coords.lat, coords.lon],
    queryFn: () => callTool<CurrentWeather>("weather__get_current_weather", coords),
  });
  const uvQ = useQuery({
    queryKey: ["dash-uv", refreshVersion, coords.lat, coords.lon],
    queryFn: () => callTool<UvIndex>("weather__get_uv_index", coords),
  });
  const pollenQ = useQuery({
    queryKey: ["dash-pollen", refreshVersion, coords.lat, coords.lon],
    queryFn: () => callTool<PollenLevels>("weather__get_pollen_levels", coords),
  });

  const deleteMut = useMutation({
    mutationFn: (id: number) => callTool<DeleteResult>("strava__delete_activity", { activity_id: id }),
    onSuccess: (res) => {
      setConfirmDelete(false);
      if (res.success) {
        setSelectedId(null);
        queryClient.invalidateQueries({ queryKey: ["activities"] });
      }
    },
  });

  // ── Derived activity list: sport filter -> search -> period cutoff ──
  const allActivities = activitiesQ.data?.activities ?? [];
  const actError = activitiesQ.data?.error;

  const activities = useMemo(() => {
    let list = allActivities;
    if (sportFilter && sportFilter !== "All") {
      list = list.filter((a) => sportOf(a) === sportFilter);
    }
    const q = search.trim().toLowerCase();
    if (q) {
      list = list.filter(
        (a) => (a.name || "").toLowerCase().includes(q) || sportOf(a).toLowerCase().includes(q),
      );
    }
    if (loadDays > 0) {
      const cutoff = new Date(Date.now() - loadDays * 86400000).toISOString().slice(0, 10);
      list = list.filter((a) => dayStr(a) >= cutoff);
    }
    return list;
  }, [allActivities, sportFilter, search, loadDays]);

  // ── Key metrics ──
  const metrics = useMemo(() => {
    const totalDist = activities.reduce((s, a) => s + (a.distance_km || 0), 0);
    const totalH = activities.reduce((s, a) => s + (a.moving_time_hours || 0), 0);
    const totalElev = activities.reduce((s, a) => s + (a.elevation_gain_m || 0), 0);
    const hrs = activities.map((a) => a.avg_heart_rate).filter((h): h is number => h != null);
    const avgHr = hrs.length ? hrs.reduce((a, b) => a + b, 0) / hrs.length : null;
    return { totalDist, totalH, totalElev, avgHr, count: activities.length };
  }, [activities]);

  const profile = athleteQ.data?.profile ?? {};
  const stats = athleteQ.data?.official_stats ?? {};

  const selected = selectedId != null ? activities.find((a) => a.id === selectedId) ?? null : null;

  // Clear the selection if the selected activity is no longer in the filtered list.
  useEffect(() => {
    if (selectedId != null && !activities.some((a) => a.id === selectedId)) {
      setSelectedId(null);
    }
  }, [activities, selectedId]);

  return (
    <div>
      {flythroughOpen && selected && (
        <FlythroughModal
          activityId={selected.id}
          activityName={selected.name}
          onClose={() => setFlythroughOpen(false)}
        />
      )}
      <PageHeader title="Dashboard" subtitle="Activity overview, weather, training charts" />

      {/* Strava error banner — still render weather below */}
      {actError && (
        <div className="mb-4">
          <ErrorBox message={`Strava activities error: ${actError}`} />
        </div>
      )}

      {/* ── Athlete header ── */}
      <AthleteHeader profile={profile} loading={athleteQ.isLoading} />

      {/* ── Weather widget ── */}
      <WeatherWidget
        weather={weatherQ.data}
        uv={uvQ.data}
        pollen={pollenQ.data}
        loading={weatherQ.isLoading || uvQ.isLoading || pollenQ.isLoading}
        error={!!(weatherQ.error || uvQ.error || pollenQ.error)}
      />

      <div className="my-5 h-px bg-border" />

      {/* ── Period selector ── */}
      <div className="mb-4">
        <PeriodSelector
          options={PERIODS}
          value={period}
          onChange={(p) => {
            setPeriod(p);
            setSelectedId(null);
          }}
        />
      </div>

      {/* ── Search filter ── */}
      <input
        className="fd-input mb-4 w-full max-w-md"
        placeholder="🔍 Search activities — name or sport type…"
        value={search}
        onChange={(e) => setSearch(e.target.value)}
      />

      {activitiesQ.isLoading && <Spinner label="Loading Strava data…" />}

      {/* ── Key metrics ── */}
      <div className="mb-2 grid grid-cols-2 gap-3 md:grid-cols-5">
        <MetricCard label="Activities" value={metrics.count.toLocaleString()} />
        <MetricCard label="Total Distance" value={`${metrics.totalDist.toLocaleString(undefined, { maximumFractionDigits: 1 })} km`} />
        <MetricCard label="Total Time" value={`${Math.round(metrics.totalH).toLocaleString()} h`} />
        <MetricCard label="Total Elevation" value={`${Math.round(metrics.totalElev).toLocaleString()} m`} />
        <MetricCard label="Avg Heart Rate" value={metrics.avgHr != null ? `${metrics.avgHr.toFixed(0)} bpm` : "—"} />
      </div>

      <div className="my-5 h-px bg-border" />

      {/* ── Activity map ── */}
      <h3 className="mb-3 text-lg font-semibold text-text-primary">Activity Map</h3>
      <ActivityMapPanel
        activities={activities}
        selectedId={selectedId}
        selected={selected}
        onSelect={(id) => {
          setSelectedId(id);
          setFlythroughOpen(false);
          setConfirmDelete(false);
        }}
        onFlythrough={() => setFlythroughOpen(true)}
        confirmDelete={confirmDelete}
        onDeleteClick={() => setConfirmDelete(true)}
        onDeleteCancel={() => setConfirmDelete(false)}
        onDeleteConfirm={(id) => deleteMut.mutate(id)}
        deleting={deleteMut.isPending}
        deleteError={deleteMut.data?.success === false ? deleteMut.data.error : undefined}
      />

      {/* ── Activity stream analysis ── */}
      {selected && (
        <>
          <div className="my-5 h-px bg-border" />
          <ActivityAnalysis activityId={selected.id} />
        </>
      )}

      <div className="my-5 h-px bg-border" />

      {/* ── Recent activities ── */}
      <RecentActivities activities={activities} />

      {/* ── Training charts ── */}
      <TrainingCharts activities={activities} periodDays={loadDays} />

      <div className="my-5 h-px bg-border" />

      {/* ── Official Strava stats ── */}
      <h3 className="mb-3 text-lg font-semibold text-text-primary">Official Strava Stats</h3>
      <div className="mb-3 flex gap-1 border-b border-border">
        <TabBtn active={statsTab === "ytd"} onClick={() => setStatsTab("ytd")}>Year to Date</TabBtn>
        <TabBtn active={statsTab === "lfw"} onClick={() => setStatsTab("lfw")}>Last 4 Weeks</TabBtn>
        <TabBtn active={statsTab === "all"} onClick={() => setStatsTab("all")}>All Time</TabBtn>
      </div>
      <OfficialStatsTab stats={stats} tab={statsTab} />
    </div>
  );
}

// ── Athlete header ────────────────────────────────────────────────────────────
function AthleteHeader({ profile, loading }: { profile: AthleteProfile; loading: boolean }) {
  if (loading) return <Spinner label="Loading athlete…" />;
  const name = profile.name || `${profile.firstname || ""} ${profile.lastname || ""}`.trim() || "Athlete";
  const loc = [profile.city, profile.state, profile.country].filter(Boolean).join(", ");
  const since = (profile.member_since || profile.created_at || "").slice(0, 4);
  const url = profile.profile_url || profile.profile || "";
  const infoParts: string[] = [];
  if (loc) infoParts.push(`📍 ${loc}`);
  if (since) infoParts.push(`Member since ${since}`);
  if (profile.premium) infoParts.push("⭐ Premium");

  return (
    <div className="mb-4 flex items-center gap-4">
      {url.startsWith("http") && (
        <img src={url} alt={name} className="h-[68px] w-[68px] rounded-full border border-border object-cover" />
      )}
      <div>
        <h2 className="text-2xl font-bold text-text-primary">{name}</h2>
        {infoParts.length > 0 && <p className="mt-0.5 text-sm text-text-muted">{infoParts.join("  ·  ")}</p>}
      </div>
    </div>
  );
}

// ── Weather widget ──────────────────────────────────────────────────────────
function WeatherWidget({
  weather,
  uv,
  pollen,
  loading,
  error,
}: {
  weather?: CurrentWeather;
  uv?: UvIndex;
  pollen?: PollenLevels;
  loading: boolean;
  error: boolean;
}) {
  if (loading) return <Spinner label="Loading weather…" />;
  if (error || !weather) {
    return (
      <p className="text-sm text-text-muted">
        ⚠️ Weather unavailable — make sure the weather MCP server is running.
      </p>
    );
  }

  const condition = WMO[weather.weather_code] ?? "🌡️";
  const uvRisk = uv?.risk ?? "?";
  const uvIcon = UV_RISK[uvRisk] ?? "";

  // Highest pollen type
  const pollenObj = pollen?.pollen ?? {};
  let topKey: string | null = null;
  let topLevel = "none";
  let topVal = -1;
  for (const [k, v] of Object.entries(pollenObj)) {
    if ((v.value_grains_m3 ?? 0) > topVal) {
      topVal = v.value_grains_m3 ?? 0;
      topKey = k;
      topLevel = v.level || "none";
    }
  }
  const polIcon = POL_RISK[topLevel] ?? "";
  const polSub = topKey ? titleCase(topKey.replace("_pollen", "")) : "—";

  return (
    <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
      <MetricCard label={`Weather ${weather.location}`} value={condition} sub={`${weather.temperature_c} °C`} />
      <MetricCard label="Wind" value={`${weather.wind_speed_kmh} km/h`} />
      <MetricCard label="UV Index" value={`${uvIcon} ${uv?.uv_index ?? "?"}`} sub={uvRisk} />
      <MetricCard label="Pollen" value={`${polIcon} ${titleCase(topLevel)}`} sub={polSub} />
    </div>
  );
}

// ── Activity map panel (left control column + right map) ──────────────────────
function ActivityMapPanel({
  activities,
  selectedId,
  selected,
  onSelect,
  onFlythrough,
  confirmDelete,
  onDeleteClick,
  onDeleteCancel,
  onDeleteConfirm,
  deleting,
  deleteError,
}: {
  activities: Activity[];
  selectedId: number | null;
  selected: Activity | null;
  onSelect: (id: number | null) => void;
  onFlythrough: () => void;
  confirmDelete: boolean;
  onDeleteClick: () => void;
  onDeleteCancel: () => void;
  onDeleteConfirm: (id: number) => void;
  deleting: boolean;
  deleteError?: string;
}) {
  const routed = activities.filter((a) => decodeRoute(a).length > 0);

  // Map: selected route only, or all routes (overview).
  const { polylines, markers } = useMemo(() => {
    const lines: PolyLineSpec[] = [];
    const marks: MarkerSpec[] = [];
    const n = routed.length;
    routed.forEach((a, i) => {
      const coords = decodeRoute(a);
      if (!coords.length) return;
      const isSel = selectedId === a.id;
      const isDim = selectedId != null && !isSel;
      const weight = isSel ? 5 : 2;
      const opacity = isSel ? 0.95 : isDim ? 0.1 : Math.max(0.25, 1.0 - (i / Math.max(n, 1)) * 0.75);
      lines.push({ coords, color: ACCENT, weight, opacity });
      if (isSel) {
        marks.push({ lat: coords[0][0], lon: coords[0][1], color: "#2ECC71", label: "Start" });
        marks.push({ lat: coords[coords.length - 1][0], lon: coords[coords.length - 1][1], color: "#E74C3C", label: "Finish" });
      }
    });
    return { polylines: lines, markers: marks };
  }, [routed, selectedId]);

  const sortedForSelect = useMemo(
    () => [...activities].sort((a, b) => (b.start_date || "").localeCompare(a.start_date || "")),
    [activities],
  );

  return (
    <div className="grid grid-cols-1 gap-4 lg:grid-cols-[1fr_3fr]">
      {/* Left control column */}
      <div>
        <select
          className="fd-input w-full"
          value={selectedId ?? ""}
          onChange={(e) => onSelect(e.target.value ? Number(e.target.value) : null)}
        >
          <option value="">All activities</option>
          {sortedForSelect.map((a) => (
            <option key={a.id} value={a.id}>
              {activityIcon(sportOf(a))} {a.name || "?"} ({(a.start_date || "").slice(0, 10)})
            </option>
          ))}
        </select>

        {routed.length === 0 && <p className="mt-3 text-sm text-text-muted">No GPS routes found.</p>}

        {selected ? (
          <SelectedActivityCard
            activity={selected}
            hasRoute={decodeRoute(selected).length > 0}
            onFlythrough={onFlythrough}
            confirmDelete={confirmDelete}
            onDeleteClick={onDeleteClick}
            onDeleteCancel={onDeleteCancel}
            onDeleteConfirm={() => onDeleteConfirm(selected.id)}
            deleting={deleting}
            deleteError={deleteError}
          />
        ) : (
          <p className="mt-3 text-sm text-text-muted">
            <span className="font-semibold text-text-primary">{routed.length}</span> of{" "}
            {activities.length} activities have GPS routes.
          </p>
        )}
      </div>

      {/* Right map */}
      <div>
        {polylines.length > 0 ? (
          <RouteMap polylines={polylines} markers={markers} height={500} />
        ) : (
          <EmptyState message="No GPS route data available." />
        )}
      </div>
    </div>
  );
}

function SelectedActivityCard({
  activity,
  hasRoute,
  onFlythrough,
  confirmDelete,
  onDeleteClick,
  onDeleteCancel,
  onDeleteConfirm,
  deleting,
  deleteError,
}: {
  activity: Activity;
  hasRoute: boolean;
  onFlythrough: () => void;
  confirmDelete: boolean;
  onDeleteClick: () => void;
  onDeleteCancel: () => void;
  onDeleteConfirm: () => void;
  deleting: boolean;
  deleteError?: string;
}) {
  const sport = activity.type || activity.sport_type || "";
  const distKm = activity.distance_km ?? 0;
  const tMin = Math.round((activity.moving_time_hours ?? 0) * 60);
  const elev = Math.round(activity.elevation_gain_m ?? 0);
  const spd = activity.avg_speed_kmh ?? 0;
  const hr = activity.avg_heart_rate;

  return (
    <div className="mt-4 border-t border-border pt-4">
      <div className="font-semibold text-text-primary">
        {activityIcon(sport)} {activity.name || ""}
      </div>
      <p className="text-xs text-text-muted">
        {sport} · {(activity.start_date || "").slice(0, 10)}
      </p>

      <div className="mt-3 space-y-2">
        <MiniMetric label="Distance" value={`${distKm} km`} />
        <MiniMetric label="Duration" value={tMin >= 60 ? `${Math.floor(tMin / 60)}h ${tMin % 60}min` : `${tMin} min`} />
        {sport === "Run" || sport === "Hike" || sport === "Walk" ? (
          <MiniMetric label="Avg Pace" value={paceStr(spd)} />
        ) : spd > 0 ? (
          <MiniMetric label="Avg Speed" value={`${spd.toFixed(1)} km/h`} />
        ) : null}
        <MiniMetric label="Elevation" value={`${elev} m`} />
        {hr != null && <MiniMetric label="Avg HR" value={`${hr.toFixed(0)} bpm`} />}
      </div>

      {hasRoute && (
        <div className="mt-3">
          <button className="fd-btn-primary w-full" onClick={onFlythrough}>
            🎥 3D Flythrough
          </button>
        </div>
      )}

      {/* Delete with two-step confirm */}
      <div className="mt-3">
        {!confirmDelete ? (
          <button className="fd-btn-secondary w-full" onClick={onDeleteClick}>
            🗑️ Delete activity
          </button>
        ) : (
          <div className="rounded-lg border border-metric-amber/40 bg-metric-amber/10 p-3">
            <p className="mb-2 text-sm text-metric-amber">
              Really delete <span className="font-semibold">{activity.name || ""}</span>? This cannot be undone.
            </p>
            <div className="flex gap-2">
              <button className="fd-btn-primary flex-1" disabled={deleting} onClick={onDeleteConfirm}>
                {deleting ? "Deleting…" : "✓ Yes, delete"}
              </button>
              <button className="fd-btn-secondary flex-1" disabled={deleting} onClick={onDeleteCancel}>
                ✗ Cancel
              </button>
            </div>
            {deleteError && <p className="mt-2 text-xs text-metric-red">{deleteError}</p>}
          </div>
        )}
      </div>
    </div>
  );
}

function MiniMetric({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div className="text-[10px] font-medium uppercase tracking-wide text-text-muted">{label}</div>
      <div className="text-base font-bold text-text-primary">{value}</div>
    </div>
  );
}

// ── Recent activities ─────────────────────────────────────────────────────────
function RecentActivities({ activities }: { activities: Activity[] }) {
  if (activities.length === 0) return null;
  const recent = activities.slice(0, 9);
  return (
    <div>
      <h3 className="mb-3 text-lg font-semibold text-text-primary">Recent Activities</h3>
      <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
        {recent.map((a) => {
          const sport = sportOf(a);
          const d = dayStr(a);
          const dateLabel = d
            ? new Date(d).toLocaleDateString(undefined, { day: "2-digit", month: "short", year: "numeric" })
            : "";
          const distKm = a.distance_km ?? 0;
          const tMin = Math.round((a.moving_time_hours ?? 0) * 60);
          const elev = Math.round(a.elevation_gain_m ?? 0);
          const spd = a.avg_speed_kmh ?? 0;
          return (
            <div key={a.id} className="fd-card fd-card-hover p-4">
              <div className="font-semibold text-text-primary">
                {activityIcon(sport)} {a.name}
              </div>
              <p className="text-xs text-text-muted">
                {sport} · {dateLabel}
              </p>
              <div className="mt-2 grid grid-cols-2 gap-2">
                <MiniMetric label="Distance" value={`${distKm} km`} />
                <MiniMetric label="Time" value={`${tMin} min`} />
                {elev > 0 && <MiniMetric label="Elevation" value={`${elev} m`} />}
                {elev > 0 && spd > 0 && (
                  <MiniMetric
                    label={sport === "Run" || sport === "Hike" || sport === "Walk" ? "Pace" : "Speed"}
                    value={sport === "Run" || sport === "Hike" || sport === "Walk" ? paceStr(spd) : `${spd} km/h`}
                  />
                )}
              </div>
            </div>
          );
        })}
      </div>
      <div className="my-5 h-px bg-border" />
    </div>
  );
}

// ── Training charts ─────────────────────────────────────────────────────────
function TrainingCharts({ activities, periodDays }: { activities: Activity[]; periodDays: number }) {
  // Typed activities only (non-empty sport)
  const typed = useMemo(
    () => activities.filter((a) => sportOf(a) && sportOf(a) !== "Unknown" && sportOf(a).trim()),
    [activities],
  );

  // Adaptive aggregation: day for <=30d, week for <=180d, month longer
  let aggCol: "day" | "week" | "month";
  let aggLabel: string;
  if (periodDays > 0 && periodDays <= 30) {
    aggCol = "day";
    aggLabel = "Day";
  } else if (periodDays > 0 && periodDays <= 180) {
    aggCol = "week";
    aggLabel = "Week";
  } else {
    aggCol = "month";
    aggLabel = "Month";
  }

  // Parse activities into rows with dates
  const rows = useMemo(
    () =>
      typed
        .map((a) => {
          const ds = dayStr(a);
          const dt = ds ? new Date(ds) : null;
          return {
            id: a.id,
            type: sportOf(a),
            dt,
            distance_km: a.distance_km ?? 0,
            moving_time_min: Math.round((a.moving_time_hours ?? 0) * 60 * 10) / 10,
            elevation_m: Math.round(a.elevation_gain_m ?? 0),
            avg_speed_kmh: a.avg_speed_kmh ?? 0,
            year: dt && !Number.isNaN(dt.getTime()) ? dt.getUTCFullYear() : null,
          };
        })
        .filter((r) => r.dt && !Number.isNaN(r.dt.getTime())),
    [typed],
  );

  // Distance per period (bar, ACCENT orange)
  const distAgg = aggregate(rows, aggCol, "distance_km");
  // Training time per period (area)
  const timeAgg = aggregate(rows, aggCol, "moving_time_min");
  const timeHours = timeAgg.map((d) => ({ key: d.key, value: Math.round((d.value / 60) * 100) / 100 }));
  // Elevation per period (bar)
  const elevAgg = aggregate(rows, aggCol, "elevation_m");

  // Sport breakdown
  const sportCounts = useMemo(() => {
    const m = new Map<string, number>();
    for (const r of rows) m.set(r.type, (m.get(r.type) ?? 0) + 1);
    return [...m.entries()];
  }, [rows]);
  const nSportTypes = sportCounts.length;

  // Single-sport diagnostic (last 50)
  const singleSport = nSportTypes === 1 ? sportCounts[0]?.[0] ?? "" : "";
  const last50 = useMemo(() => {
    return [...rows].sort((a, b) => (a.dt as Date).getTime() - (b.dt as Date).getTime()).slice(-50);
  }, [rows]);

  if (activities.length === 0) return null;

  return (
    <div>
      <h3 className="mb-3 text-lg font-semibold text-text-primary">Training Overview</h3>

      <div className="grid grid-cols-1 gap-6 md:grid-cols-2">
        {/* Distance per period */}
        <div>
          <p className="fd-label mb-1">Distance per {aggLabel}</p>
          <PlotlyChart
            data={[
              {
                type: "bar",
                x: distAgg.map((d) => d.key),
                y: distAgg.map((d) => d.value),
                marker: { color: ACCENT, line: { width: 0 } },
              } as Data,
            ]}
            layout={{ yaxis: { ticksuffix: " km" } }}
            height={260}
          />
        </div>

        {/* Sport breakdown OR single-sport diagnostic */}
        <div>
          {nSportTypes > 1 ? (
            <>
              <p className="fd-label mb-1">Sport Breakdown</p>
              <PlotlyChart
                data={[
                  {
                    type: "pie",
                    values: sportCounts.map((s) => s[1]),
                    labels: sportCounts.map((s) => s[0]),
                    hole: 0.5,
                    marker: { colors: CHART_COLORS },
                    textposition: "inside",
                    textinfo: "percent+label",
                    textfont: { size: 11 },
                  } as unknown as Data,
                ]}
                height={260}
              />
            </>
          ) : (
            <SingleSportChart sport={singleSport} rows={last50} />
          )}
        </div>

        {/* Training time per period (area) */}
        <div>
          <p className="fd-label mb-1">Training Time per {aggLabel}</p>
          <PlotlyChart
            data={[
              {
                type: "scatter",
                mode: "lines",
                x: timeHours.map((d) => d.key),
                y: timeHours.map((d) => d.value),
                fill: "tozeroy",
                line: { color: ACCENT, width: 2 },
              } as Data,
            ]}
            layout={{ yaxis: { ticksuffix: " h" } }}
            height={260}
          />
        </div>

        {/* Year-over-year (long periods) OR elevation per period */}
        <div>
          {periodDays === 0 || periodDays > 90 ? (
            <YearOverYearChart rows={rows} />
          ) : (
            <>
              <p className="fd-label mb-1">Elevation per {aggLabel}</p>
              <PlotlyChart
                data={[
                  {
                    type: "bar",
                    x: elevAgg.map((d) => d.key),
                    y: elevAgg.map((d) => d.value),
                    marker: { color: C_AMBER, line: { width: 0 } },
                  } as Data,
                ]}
                layout={{ yaxis: { ticksuffix: " m" } }}
                height={260}
              />
            </>
          )}
        </div>
      </div>
    </div>
  );
}

interface ChartRow {
  id: number;
  type: string;
  dt: Date | null;
  distance_km: number;
  moving_time_min: number;
  elevation_m: number;
  avg_speed_kmh: number;
  year: number | null;
}

function aggregate(rows: ChartRow[], col: "day" | "week" | "month", field: "distance_km" | "moving_time_min" | "elevation_m") {
  const m = new Map<string, number>();
  for (const r of rows) {
    if (!r.dt) continue;
    const k = bucketKey(r.dt, col);
    m.set(k, (m.get(k) ?? 0) + (r[field] as number));
  }
  return [...m.entries()]
    .map(([key, value]) => ({ key, value: Math.round(value * 10) / 10 }))
    .sort((a, b) => a.key.localeCompare(b.key));
}

function SingleSportChart({ sport, rows }: { sport: string; rows: ChartRow[] }) {
  // Mirrors ui/dashboard.py: pick the most diagnostic per-activity metric.
  const runningLike = ["Run", "TrailRun", "VirtualRun", "Hike", "Walk"].includes(sport);
  const cyclingLike = ["Ride", "MountainBikeRide", "GravelRide", "EBikeRide", "VirtualRide"].includes(sport);

  const xs = rows.map((r) => r.dt as Date);
  // pace_min_per_km = 60 / avg_speed_kmh
  const paceRows = rows.filter((r) => r.avg_speed_kmh > 0);
  const hasPace = runningLike && paceRows.length > 0;
  const hasSpeed = cyclingLike && rows.some((r) => r.avg_speed_kmh > 0);
  const hasElev = rows.some((r) => r.elevation_m > 0);

  // Bubble size scaled from distance (px.scatter size=distance_km).
  const sizeOf = (r: ChartRow) => Math.max(6, Math.min(28, r.distance_km / 2 + 6));

  if (hasPace) {
    return (
      <ScatterPanel
        label="Pace per Activity"
        x={xs}
        y={rows.map((r) => (r.avg_speed_kmh > 0 ? Math.round((60 / r.avg_speed_kmh) * 100) / 100 : null))}
        sizes={rows.map(sizeOf)}
        ticksuffix=" min/km"
      />
    );
  }
  if (hasSpeed) {
    return (
      <ScatterPanel
        label="Speed per Activity"
        x={xs}
        y={rows.map((r) => r.avg_speed_kmh || null)}
        sizes={rows.map(sizeOf)}
        ticksuffix=" km/h"
      />
    );
  }
  if (hasElev) {
    return (
      <>
        <p className="fd-label mb-1">Elevation per Activity</p>
        <PlotlyChart
          data={[
            {
              type: "bar",
              x: xs,
              y: rows.map((r) => r.elevation_m),
              marker: { color: CHART_COLORS[2], line: { width: 0 } },
            } as Data,
          ]}
          layout={{ yaxis: { ticksuffix: " m" } }}
          height={260}
        />
      </>
    );
  }
  return (
    <>
      <p className="fd-label mb-1">Distance per Activity</p>
      <PlotlyChart
        data={[
          {
            type: "bar",
            x: xs,
            y: rows.map((r) => r.distance_km),
            marker: { color: ACCENT, line: { width: 0 } },
          } as Data,
        ]}
        layout={{ yaxis: { ticksuffix: " km" } }}
        height={260}
      />
    </>
  );
}

function ScatterPanel({
  label,
  x,
  y,
  sizes,
  ticksuffix,
}: {
  label: string;
  x: Date[];
  y: (number | null)[];
  sizes: number[];
  ticksuffix: string;
}) {
  return (
    <>
      <p className="fd-label mb-1">{label}</p>
      <PlotlyChart
        data={[
          {
            type: "scatter",
            mode: "markers",
            x,
            y,
            marker: { color: ACCENT, size: sizes, line: { width: 0 } },
          } as Data,
        ]}
        layout={{ yaxis: { ticksuffix } }}
        height={260}
      />
    </>
  );
}

function YearOverYearChart({ rows }: { rows: ChartRow[] }) {
  // Stacked bar: year × sport → distance
  const years = useMemo(() => {
    const set = new Set<number>();
    for (const r of rows) if (r.year != null) set.add(r.year);
    return [...set].sort();
  }, [rows]);
  const sports = useMemo(() => {
    const set = new Set<string>();
    for (const r of rows) set.add(r.type);
    return [...set];
  }, [rows]);

  const traces: Data[] = sports.map((sp, idx) => {
    const yByYear = new Map<number, number>();
    for (const r of rows) {
      if (r.type === sp && r.year != null) yByYear.set(r.year, (yByYear.get(r.year) ?? 0) + r.distance_km);
    }
    return {
      type: "bar",
      name: sp,
      x: years.map((y) => String(y)),
      y: years.map((y) => Math.round((yByYear.get(y) ?? 0) * 10) / 10),
      marker: { color: CHART_COLORS[idx % CHART_COLORS.length], line: { width: 0 } },
    } as Data;
  });

  return (
    <>
      <p className="fd-label mb-1">Year-over-Year Distance</p>
      <PlotlyChart data={traces} layout={{ barmode: "stack", yaxis: { ticksuffix: " km" } }} height={260} />
    </>
  );
}

// ── Official stats ────────────────────────────────────────────────────────────
function TabBtn({ active, onClick, children }: { active: boolean; onClick: () => void; children: ReactNode }) {
  return (
    <button onClick={onClick} className={`fd-tab ${active ? "fd-tab-active" : ""}`}>
      {children}
    </button>
  );
}

function OfficialStatsTab({ stats, tab }: { stats: OfficialStats; tab: "ytd" | "lfw" | "all" }) {
  const period: PeriodStats | undefined =
    tab === "ytd" ? stats.year_to_date : tab === "lfw" ? stats.last_4_weeks : stats.all_time;

  const rows = [
    { sport: "Run", t: period?.run },
    { sport: "Ride", t: period?.ride },
    { sport: "Swim", t: period?.swim },
  ].filter((r) => r.t && r.t.count > 0);

  return (
    <div>
      {rows.length > 0 ? (
        <div className="overflow-x-auto rounded-card border border-border">
          <table className="w-full min-w-[34rem] text-sm">
            <thead>
              <tr className="bg-bg-surface text-text-muted">
                <th className="px-4 py-2 text-left font-medium">Sport</th>
                <th className="px-4 py-2 text-right font-medium">Activities</th>
                <th className="px-4 py-2 text-right font-medium">Distance (km)</th>
                <th className="px-4 py-2 text-right font-medium">Time (h)</th>
                <th className="px-4 py-2 text-right font-medium">Elevation (m)</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.sport} className="border-t border-border text-text-primary">
                  <td className="px-4 py-2">{r.sport}</td>
                  <td className="px-4 py-2 text-right">{r.t!.count}</td>
                  <td className="px-4 py-2 text-right">{r.t!.distance_km}</td>
                  <td className="px-4 py-2 text-right">{r.t!.moving_time_hours}</td>
                  <td className="px-4 py-2 text-right">{r.t!.elevation_gain_m}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <p className="text-sm text-text-muted">No data recorded yet.</p>
      )}

      {/* All-time: biggest ride & climb */}
      {tab === "all" && (stats.biggest_ride_distance_km || stats.biggest_climb_elevation_gain_m) ? (
        <div className="mt-3 grid grid-cols-2 gap-3">
          <MetricCard label="Biggest Ride" value={`${stats.biggest_ride_distance_km ?? 0} km`} />
          <MetricCard
            label="Biggest Climb"
            value={`${Math.round(stats.biggest_climb_elevation_gain_m ?? 0)} m`}
          />
        </div>
      ) : null}
    </div>
  );
}
