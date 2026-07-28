// Dashboard — a READ-ONLY cockpit. Nothing is created or edited here; goals are
// managed in the Coach tab and only mirrored here. Groups: a greeting, an at-a-
// glance row (recovery, weather, last session with a colour verdict, streak), a
// recent-activity calendar, a read-only goals strip, and the deeper Analysis
// (merged in — the old Analysis tab is gone).

import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";

import { GoalCockpit } from "../components/dashboard/GoalCockpit";
import { PageHeader } from "../components/PageHeader";
import { Spinner } from "../components/Spinner";
import { callTool, getAthleteOverview } from "../lib/api";
import { useAvatarUrl, useProfile } from "../lib/profileHooks";
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
  pace_display?: string; avg_heart_rate?: number | null; duration_min?: number;
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

      {/* ── Recent activity calendar ── */}
      <RecentCalendar acts={acts} loading={actsQ.isLoading} />

      {/* ── Deeper analysis (merged in — no separate tab) ── */}
      <div className="border-t border-border pt-2">
        <Analysis />
      </div>
    </div>
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

// ── Recent-activity calendar (last 3 weeks) ───────────────────────────────────
function RecentCalendar({ acts, loading }: { acts: Act[]; loading: boolean }) {
  const days = useMemo(() => {
    const byDay: Record<string, Act[]> = {};
    for (const a of acts) {
      const d = actDate(a);
      if (d) (byDay[d] ||= []).push(a);
    }
    const out: { iso: string; date: Date; acts: Act[] }[] = [];
    const today = new Date();
    for (let i = 20; i >= 0; i--) {
      const d = new Date(today); d.setDate(today.getDate() - i);
      const iso = d.toISOString().slice(0, 10);
      out.push({ iso, date: d, acts: byDay[iso] ?? [] });
    }
    return out;
  }, [acts]);

  return (
    <section className="fd-card px-5 py-4">
      <div className="mb-3 flex items-baseline justify-between">
        <h2 className="text-sm font-semibold text-text-primary">Last 3 weeks</h2>
        <span className="fd-label">{acts.length} activities</span>
      </div>
      {loading ? (
        <Spinner label="Loading activities…" />
      ) : (
        <div className="grid grid-cols-7 gap-1.5">
          {days.map(({ iso, date, acts: da }) => {
            const active = da.length > 0;
            const label = date.toLocaleDateString("en-GB", { weekday: "short", day: "numeric" });
            return (
              <div key={iso}
                title={active ? `${label}: ${da.map((a) => a.name).join(", ")}` : label}
                className={`flex aspect-square flex-col items-center justify-center rounded-lg border text-[10px] ${
                  active ? "border-accent/40 bg-accent/10 text-accent" : "border-border bg-bg-surface/40 text-text-faint"
                }`}>
                <span>{date.getDate()}</span>
                {active && <span className="mt-0.5 text-[13px] leading-none">{activityIcon(da[0].sport_type || da[0].type)}</span>}
              </div>
            );
          })}
        </div>
      )}
    </section>
  );
}
