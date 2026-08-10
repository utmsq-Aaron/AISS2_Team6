// Dashboard — a READ-ONLY cockpit. Nothing is created or edited here; goals are
// managed in the Coach tab and only mirrored here.
//
// The page narrows from "right now" to "over time", and that order is deliberate:
//   1. greeting + at-a-glance tiles (recovery, weather, last session, streak)
//   2. the goals strip, mirrored from Coach
//   3. Recent trainings — individual sessions, their route/streams and the
//      per-activity actions (the 3D flythrough). Uses its own unfiltered list.
//      A 3-week day-tile calendar used to sit above this; it was dropped because
//      it answered a strictly weaker version of the same question.
//   4. Analysis — everything time-series, scoped by ITS OWN period selector.
// Keeping the period selector below step 3 matters: it scopes the charts, and
// when it also scoped the session list a 30-day default showed nothing at all to
// anyone who last trained five weeks ago.

import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";

import { ActivityAnalysis } from "../components/dashboard/ActivityAnalysis";
import { GoalCockpit } from "../components/dashboard/GoalCockpit";
import FlythroughModal from "../components/FlythroughModal";
import { PageHeader } from "../components/PageHeader";
import { Spinner } from "../components/Spinner";
import { callTool, getAthleteOverview } from "../lib/api";
import { useAvatarUrl, useProfile } from "../lib/profileHooks";
import { useRevealOnExpand } from "../lib/revealOnExpand";
import type { AthleteProfile, AthleteResult } from "../lib/stravaTypes";
import { useUiStore } from "../store/uiStore";
import { activityIcon } from "../theme/tokens";
import { Analysis } from "./Analysis";

const KARLSRUHE = { lat: 49.0069, lon: 8.4037 };

const WMO: Record<number, string> = {
  0: "☀️ Clear", 1: "🌤️ Mainly clear", 2: "⛅ Partly cloudy", 3: "☁️ Overcast",
  45: "🌫️ Fog", 48: "🌫️ Fog", 51: "🌦️ Light drizzle", 53: "🌦️ Drizzle", 55: "🌧️ Drizzle",
  61: "🌧️ Light rain", 63: "🌧️ Rain", 65: "🌧️ Heavy rain", 71: "🌨️ Light snow",
  73: "🌨️ Snow", 75: "❄️ Heavy snow", 80: "🌦️ Showers", 81: "🌧️ Showers",
  82: "⛈️ Violent showers", 95: "⛈️ Thunderstorm", 96: "⛈️ Thunderstorm", 99: "⛈️ Thunderstorm",
};

interface CurrentWeather {
  location: string; temperature_c: number; wind_speed_kmh: number;
  weather_code: number; weather_condition: string;
}
interface DailyHealth {
  resting_hr: number | null; avg_stress: number | null;
  body_battery_now: number | null; steps: number | null;
}
interface Act {
  id: number; name: string; type?: string; sport_type?: string;
  date?: string; start_date?: string; distance_km?: number;
  // Strava reports moving time in hours; there is no duration_min on this shape.
  pace_display?: string; avg_heart_rate?: number | null; moving_time_hours?: number;
  elevation_gain_m?: number;
  // Present whenever the activity has a GPS track — gates the 3D flythrough.
  map_polyline?: string;
}
type ZoneBands = Record<string, [number, number]>;

const C = { green: "#10B981", amber: "#F59E0B", red: "#EF4444", muted: "#64748B" };

// Classify an average HR into a German zone → a plain-language verdict + colour.
function verdictFor(hr: number | null | undefined, bands?: ZoneBands): { label: string; color: string } {
  if (!hr || !bands) return { label: "Logged", color: C.muted };
  const order = ["ReKom", "GA1", "GA2", "WSA"] as const;
  let zone: string | null = null;
  for (const z of order) {
    const b = bands[z];
    if (b && hr >= b[0] && hr < b[1]) { zone = z; break; }
  }
  if (!zone && bands.WSA && hr >= bands.WSA[1]) zone = "WSA";
  if (!zone && bands.ReKom && hr < bands.ReKom[0]) zone = "ReKom";
  if (zone === "ReKom") return { label: "Easy / recovery", color: C.green };
  if (zone === "GA1") return { label: "Endurance", color: C.green };
  if (zone === "GA2") return { label: "Tempo", color: C.amber };
  if (zone === "WSA") return { label: "Hard", color: C.red };
  return { label: "Logged", color: C.muted };
}

function actDate(a: Act): string {
  return a.date || (a.start_date || "").slice(0, 10) || "";
}

// Consecutive-day streak (up to today) + active days in the last 7.
function streakOf(acts: Act[]): { streak: number; last7: number } {
  const days = new Set(acts.map(actDate).filter(Boolean));
  const today = new Date();
  const iso = (d: Date) => d.toISOString().slice(0, 10);
  let streak = 0;
  for (let i = 0; i < 60; i++) {
    const d = new Date(today); d.setDate(today.getDate() - i);
    if (days.has(iso(d))) streak++;
    else if (i > 0) break;            // today may be a rest day; don't break on i===0
    else continue;
  }
  let last7 = 0;
  for (let i = 0; i < 7; i++) {
    const d = new Date(today); d.setDate(today.getDate() - i);
    if (days.has(iso(d))) last7++;
  }
  return { streak, last7 };
}

export function Dashboard() {
  const refreshVersion = useUiStore((s) => s.refreshVersion);

  const athleteQ = useQuery({
    queryKey: ["athlete", refreshVersion],
    queryFn: () => callTool<AthleteResult>("strava__get_athlete_profile", {}),
  });
  const stravaProfile = athleteQ.data?.profile ?? {};
  const coords = {
    lat: stravaProfile.lat ?? KARLSRUHE.lat,
    lon: stravaProfile.lon ?? KARLSRUHE.lon,
  };

  const profileQuery = useProfile();
  const avatarUrl = useAvatarUrl(Boolean(profileQuery.data?.has_avatar));

  const weatherQ = useQuery({
    queryKey: ["dash-weather", refreshVersion, coords.lat, coords.lon],
    queryFn: () => callTool<CurrentWeather>("weather__get_current_weather", coords),
  });
  const healthQ = useQuery({
    queryKey: ["dash-health", refreshVersion],
    queryFn: () => callTool<DailyHealth>("garmin__get_garmin_daily_health", {}),
  });
  const actsQ = useQuery({
    queryKey: ["dash-acts", refreshVersion],
    queryFn: () => callTool<{ activities: Act[] }>("strava__get_activities", { limit: 30 }),
  });
  const zonesQ = useQuery({ queryKey: ["athlete-overview"], queryFn: getAthleteOverview });

  // Memoised so the `?? []` fallback keeps its identity — a fresh [] on every
  // render would defeat the useMemo below.
  const acts = useMemo(() => actsQ.data?.activities ?? [], [actsQ.data]);
  const last = acts[0];
  const bands = zonesQ.data?.zones?.hr?.bands_bpm as ZoneBands | undefined;
  const { streak, last7 } = useMemo(() => streakOf(acts), [acts]);

  const bb = healthQ.data?.body_battery_now;
  const bbColor = bb == null ? C.muted : bb >= 60 ? C.green : bb >= 30 ? C.amber : C.red;

  return (
    <div className="space-y-6">
      <Greeting profile={stravaProfile} name={profileQuery.data?.name} avatarUrl={avatarUrl} loading={athleteQ.isLoading} />

      {/* ── At a glance ── */}
      <section className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Tile label="Recovery"
          value={bb == null ? "—" : String(bb)} unit={bb == null ? "" : "/100"}
          sub={healthQ.data?.resting_hr ? `Resting HR ${healthQ.data.resting_hr} bpm` : "Body Battery"}
          dot={bbColor} />
        <Tile label={`Weather ${weatherQ.data?.location ?? ""}`.trim()}
          value={weatherQ.isLoading ? "…" : weatherQ.data ? (WMO[weatherQ.data.weather_code] ?? "🌡️") : "—"}
          sub={weatherQ.data ? `${weatherQ.data.temperature_c} °C · ${weatherQ.data.wind_speed_kmh} km/h wind` : "unavailable"} />
        <LastSessionTile act={last} bands={bands} loading={actsQ.isLoading} />
        <Tile label="Streak"
          value={streak > 0 ? String(streak) : "0"} unit={streak === 1 ? "day" : "days"}
          sub={`${last7} active day${last7 === 1 ? "" : "s"} this week`}
          dot={streak >= 2 ? C.green : streak === 1 ? C.amber : C.muted} />
      </section>

      {/* ── Goal cockpit — training vs. goal at a glance ── */}
      <GoalCockpit ov={zonesQ.data} acts={acts} />

      {/* ── Individual sessions + their per-activity actions ── */}
      <RecentTrainings acts={acts} loading={actsQ.isLoading} />

      {/* ── Deeper analysis: everything below is scoped by its own period selector ── */}
      <div className="border-t border-border pt-2">
        <Analysis />
      </div>
    </div>
  );
}

// ── Recent trainings ──────────────────────────────────────────────────────────
// The per-session entry point: the last few workouts with their numbers and the
// per-activity actions (today: the 3D flythrough). Deliberately NOT tied to the
// Analysis period selector below — that selector scopes the time-series charts,
// and letting it also hide individual sessions meant a 30-day default could show
// an empty list to anyone who last trained five weeks ago.
function RecentTrainings({ acts, loading }: { acts: Act[]; loading: boolean }) {
  const [expanded, setExpanded] = useState(false);
  const [flythroughFor, setFlythroughFor] = useState<Act | null>(null);
  // Which row has its route map + stream analysis open (null = none). One at a
  // time: each open map mounts a MapLibre canvas, and stacking them is wasteful.
  const [openId, setOpenId] = useState<number | null>(null);

  const shown = expanded ? acts : acts.slice(0, 5);
  const more = acts.length - 5;

  return (
    <section className="fd-card px-5 py-4">
      {flythroughFor && (
        <FlythroughModal
          activityId={flythroughFor.id}
          activityName={flythroughFor.name}
          onClose={() => setFlythroughFor(null)}
        />
      )}

      <div className="mb-3 flex items-baseline justify-between">
        <h2 className="text-sm font-semibold text-text-primary">Recent trainings</h2>
        <span className="fd-label">last {shown.length}</span>
      </div>

      {loading ? (
        <Spinner label="Loading activities…" />
      ) : acts.length === 0 ? (
        <p className="text-sm text-text-muted">No activities found.</p>
      ) : (
        <>
          <ul className="space-y-2">
            {shown.map((a) => (
              <TrainingRow
                key={a.id}
                act={a}
                open={openId === a.id}
                onToggle={() => setOpenId((id) => (id === a.id ? null : a.id))}
                onFlythrough={() => setFlythroughFor(a)}
              />
            ))}
          </ul>

          {more > 0 && (
            <button
              type="button"
              className="fd-btn-ghost mt-3 w-full text-xs"
              onClick={() => setExpanded((e) => !e)}
              aria-expanded={expanded}
            >
              {expanded ? "Show less" : `Show ${more} more`}
            </button>
          )}
        </>
      )}
    </section>
  );
}

// One session: the summary line, and — while open — its route, streams and the
// flythrough. Its own component so it can hold the reveal-on-expand ref; hooks
// can't live inside the .map() above.
function TrainingRow({
  act,
  open,
  onToggle,
  onFlythrough,
}: {
  act: Act;
  open: boolean;
  onToggle: () => void;
  onFlythrough: () => void;
}) {
  // The whole <li> is revealed, not just the panel: scrolling the panel flush
  // would push the row's own title out of view.
  const rowRef = useRevealOnExpand<HTMLLIElement>(open);

  const d = actDate(act);
  const dateLabel = d
    ? new Date(d).toLocaleDateString("en-GB", { day: "2-digit", month: "short", year: "numeric" })
    : "";
  const bits = [
    act.distance_km ? `${act.distance_km} km` : null,
    act.moving_time_hours ? `${Math.round(act.moving_time_hours * 60)} min` : null,
    act.pace_display || null,
    act.avg_heart_rate ? `${Math.round(act.avg_heart_rate)} bpm` : null,
    act.elevation_gain_m ? `${Math.round(act.elevation_gain_m)} m ↑` : null,
  ].filter(Boolean);

  return (
    <li
      ref={rowRef}
      className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-border bg-bg-surface/40 px-3 py-2 transition-colors hover:border-accent/50"
    >
      {/* The whole row toggles the detail panel — a small "Details" button
          beside a large inert row is the less obvious target. */}
      <button
        type="button"
        className="flex min-w-0 flex-1 items-center gap-2 text-left"
        onClick={onToggle}
        aria-expanded={open}
        aria-label={`Show route, heart rate, pace and elevation for ${act.name}`}
      >
        <span aria-hidden="true" className="shrink-0 text-xs text-text-muted">
          {open ? "▲" : "▼"}
        </span>
        <span className="min-w-0">
          <span className="block truncate text-sm font-medium text-text-primary">
            {activityIcon(act.sport_type || act.type)} {act.name}
          </span>
          <span className="block text-xs text-text-muted">
            {[dateLabel, ...bits].filter(Boolean).join(" · ")}
          </span>
        </span>
      </button>

      {/* Route map (with the HR / pace / cadence / elevation overlay switcher)
          plus the per-stream charts — mounted only while open so we don't pay
          for a MapLibre canvas per row. The flythrough sits at the top, right
          above the map it animates: it's an optional extra, so it stays inside
          the panel, but below the charts nobody would connect it to the route. */}
      {open && (
        <div className="w-full border-t border-border pt-3">
          <div className="mb-3 flex justify-end">
            {act.map_polyline ? (
              <button
                type="button"
                className="fd-btn-ghost text-xs"
                onClick={onFlythrough}
                aria-label={`Watch a 3D flythrough of ${act.name}`}
              >
                🎥 3D Flythrough
              </button>
            ) : (
              <span className="text-xs text-text-faint" title="No GPS track recorded">
                no GPS track — no flythrough
              </span>
            )}
          </div>
          <ActivityAnalysis activityId={act.id} elevationGainM={act.elevation_gain_m} />
        </div>
      )}
    </li>
  );
}

// ── Greeting ──────────────────────────────────────────────────────────────────
function Greeting({ profile, name, avatarUrl, loading }: {
  profile: AthleteProfile; name?: string; avatarUrl?: string | null; loading: boolean;
}) {
  if (loading) return <Spinner label="Loading…" />;
  const stravaName = profile.name || `${profile.firstname || ""} ${profile.lastname || ""}`.trim();
  const display = name || stravaName || "Athlete";
  const first = display.split(" ")[0];
  const stravaUrl = profile.profile_url || profile.profile || "";
  const url = avatarUrl || (stravaUrl.startsWith("http") ? stravaUrl : "");
  return (
    <div className="flex items-center gap-4">
      {url ? (
        <img src={url} alt={display} className="h-14 w-14 rounded-full border border-border object-cover" />
      ) : (
        <div className="flex h-14 w-14 items-center justify-center rounded-full border border-border bg-bg-surface text-xl font-bold text-text-muted">
          {first.charAt(0).toUpperCase()}
        </div>
      )}
      <PageHeader title={`Hi, ${first}`} subtitle="Here's your training at a glance." />
    </div>
  );
}

// ── Generic stat tile ─────────────────────────────────────────────────────────
function Tile({ label, value, unit, sub, dot }: {
  label: string; value: string; unit?: string; sub?: string; dot?: string;
}) {
  return (
    <div className="fd-card px-4 py-3.5">
      <div className="flex items-center gap-1.5">
        {dot && <span className="inline-block h-2 w-2 rounded-full" style={{ background: dot }} />}
        <div className="fd-label truncate">{label}</div>
      </div>
      <div className="mt-1 text-2xl font-bold tabular-nums text-text-primary">
        {value}{unit && <span className="ml-1 text-sm font-medium text-text-muted">{unit}</span>}
      </div>
      {sub && <div className="mt-0.5 truncate text-[11.5px] text-text-muted">{sub}</div>}
    </div>
  );
}

function LastSessionTile({ act, bands, loading }: { act?: Act; bands?: ZoneBands; loading: boolean }) {
  if (loading) return <Tile label="Last session" value="…" />;
  if (!act) return <Tile label="Last session" value="—" sub="No activities yet" />;
  const v = verdictFor(act.avg_heart_rate, bands);
  const sport = act.sport_type || act.type || "";
  const bits = [act.distance_km ? `${act.distance_km} km` : null, act.pace_display].filter(Boolean).join(" · ");
  return (
    <div className="fd-card px-4 py-3.5">
      <div className="flex items-center gap-1.5">
        <span className="inline-block h-2 w-2 rounded-full" style={{ background: v.color }} />
        <div className="fd-label truncate">Last session</div>
      </div>
      <div className="mt-1 truncate text-base font-bold text-text-primary">
        {activityIcon(sport)} {act.name}
      </div>
      <div className="mt-0.5 truncate text-[11.5px] text-text-muted">
        {bits}{bits && " · "}<span style={{ color: v.color }}>{v.label}</span>
      </div>
    </div>
  );
}
