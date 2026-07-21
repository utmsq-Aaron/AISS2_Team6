// Goal Cockpit — the Dashboard's training-vs-goal card. Everything shown here is
// deterministic server output (athlete overview) plus this week's real Strava km;
// no LLM in the loop. Renders: goal + countdown, the plan's phase band with
// milestone dots and a today marker, this week's actual-vs-target volume, the
// next milestone, and the prognosis facts (required vs. benchmark pace).
import { useMemo } from "react";
import { Link } from "react-router-dom";

import type { AthleteOverview } from "../../lib/api";
import { InfoHint } from "../InfoHint";

// Same phase ramp as the Coach tab (light→dark = easy→hard, single warm hue).
const PHASE_COLORS: Record<string, string> = {
  base: "#FDBA74", build: "#FB923C", peak: "#F97316", taper: "#FFD9B8",
};
const PHASE_LABEL: Record<string, string> = {
  base: "Base", build: "Build", peak: "Peak", taper: "Taper",
};
const SPORT_ICON: Record<string, string> = { run: "🏃", ride: "🚴" };

// Minimal activity shape shared with the Dashboard's existing activities query.
interface ActLike {
  date?: string; start_date?: string;
  distance_km?: number;
  type?: string; sport_type?: string;
}

function actDate(a: ActLike): string {
  return a.date || (a.start_date || "").slice(0, 10) || "";
}

// Goal-sport matcher: "run" covers Run/TrailRun/VirtualRun, "ride" covers
// Ride/…BikeRide/VirtualRide — never Walk/Hike (they don't count toward a race plan).
function matchesSport(a: ActLike, sport: "run" | "ride"): boolean {
  const t = (a.sport_type || a.type || "").toLowerCase();
  return sport === "run" ? t.includes("run") : t.includes("ride") || t.includes("bike");
}

function mondayOf(d: Date): string {
  const x = new Date(d);
  x.setDate(x.getDate() - ((x.getDay() + 6) % 7));
  return x.toISOString().slice(0, 10);
}

export function GoalCockpit({ ov, acts }: { ov?: AthleteOverview; acts: ActLike[] }) {
  const race = ov?.profile?.race;
  const plan = ov?.plan ?? null;
  const sport = (race?.sport ?? "run") as "run" | "ride";

  const thisWeekActual = useMemo(() => {
    const monday = mondayOf(new Date());
    let km = 0;
    for (const a of acts) {
      if (actDate(a) >= monday && matchesSport(a, sport)) km += a.distance_km ?? 0;
    }
    return Math.round(km * 10) / 10;
  }, [acts, sport]);

  if (!ov) return null;
  if (!race) {
    return (
      <section className="fd-card flex items-center justify-between px-5 py-4">
        <div>
          <div className="fd-label">Your goal</div>
          <p className="mt-1 text-sm text-text-muted">
            No race goal yet — tell the coach what you're training for and it builds your plan around it.
          </p>
        </div>
        <Link to="/coach" className="fd-btn-primary shrink-0 text-xs">Set a goal</Link>
      </section>
    );
  }

  const weeks = plan?.weeks ?? [];
  const currentWeek = plan?.current_week ?? null;
  const target = weeks.find((w) => w.week === currentWeek)?.target_km ?? null;
  const pct = target ? Math.min(100, Math.round((thisWeekActual / target) * 100)) : null;
  const milestones = (ov.profile.races ?? []).filter((r) => !r.is_main);
  const nextMilestone = milestones
    .filter((m) => m.status !== "achieved" && (m.days_to_race ?? -1) >= 0)
    .sort((a, b) => (a.days_to_race ?? 0) - (b.days_to_race ?? 0))[0];
  const prog = ov.prognosis;

  return (
    <section className="fd-card px-5 py-4">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex items-center gap-1">
            <span className="fd-label">Goal</span>
            <InfoHint text="Your main race goal and how training tracks toward it — managed in the Coach tab." />
          </div>
          <div className="mt-0.5 truncate text-base font-bold text-text-primary">
            {SPORT_ICON[sport]} {race.name}
          </div>
        </div>
        <div className="shrink-0 text-right">
          <div className="text-2xl font-bold leading-none tabular-nums text-text-primary">
            {ov.days_to_race ?? "—"}<span className="ml-1 text-sm font-medium text-text-muted">days</span>
          </div>
          <div className="mt-0.5 text-[11px] text-text-faint">to race day</div>
        </div>
      </div>

      {/* Plan phase band — today marker + milestone dots (labels via tooltip). */}
      {weeks.length > 0 && <PhaseBand ov={ov} />}

      <div className="mt-3 grid gap-3 sm:grid-cols-3">
        <div>
          <div className="fd-label">This week</div>
          {target != null ? (
            <>
              <div className="mt-1 text-sm font-semibold tabular-nums text-text-primary">
                {thisWeekActual} / {target} km
              </div>
              <div className="mt-1.5 h-1.5 overflow-hidden rounded-full bg-bg-surface">
                <div className="h-full rounded-full bg-accent" style={{ width: `${pct}%` }} />
              </div>
            </>
          ) : (
            <div className="mt-1 text-xs text-text-muted">
              {plan ? "No target this week" : <>No plan yet — <Link to="/coach" className="text-accent hover:underline">generate one</Link></>}
            </div>
          )}
        </div>
        <div>
          <div className="fd-label">Next milestone</div>
          {nextMilestone ? (
            <div className="mt-1 text-xs text-text-primary">
              <span className="font-semibold">{nextMilestone.name}</span>
              <span className="text-text-muted"> · in {nextMilestone.days_to_race} days</span>
            </div>
          ) : (
            <div className="mt-1 text-xs text-text-muted">None coming up</div>
          )}
        </div>
        <div>
          <div className="flex items-center gap-1">
            <span className="fd-label">Forecast</span>
            {prog?.basis && <InfoHint text={prog.basis} label="How this is computed" />}
          </div>
          <div className="mt-1 text-xs text-text-primary">
            {prog?.benchmark_pace && prog?.required_pace ? (
              <>
                <span className={`font-semibold ${prog.on_track ? "text-metric-green" : "text-metric-amber"}`}>
                  {prog.on_track ? "On track" : "Off pace"}
                </span>
                <span className="text-text-muted"> · needs {prog.required_pace}, benchmark {prog.benchmark_pace}</span>
              </>
            ) : prog?.required_pace ? (
              <span className="text-text-muted">Needs {prog.required_pace} — log a benchmark race to compare</span>
            ) : (
              <span className="text-text-muted">Open — needs a benchmark race near {race.distance_km} km</span>
            )}
          </div>
        </div>
      </div>
    </section>
  );
}

function PhaseBand({ ov }: { ov: AthleteOverview }) {
  const plan = ov.plan!;
  const race = ov.profile.race!;
  const span = useMemo(() => {
    const start = new Date(plan.weeks[0].start_date).getTime();
    const end = new Date(race.date).getTime() + 86400000;
    const pct = (iso: string) =>
      Math.max(0, Math.min(100, ((new Date(iso).getTime() - start) / (end - start)) * 100));
    return { pct };
  }, [plan, race.date]);

  const segments: { phase: string; from: number; to: number }[] = [];
  for (const w of plan.weeks) {
    const from = span.pct(w.start_date);
    const to = span.pct(new Date(new Date(w.start_date).getTime() + 7 * 86400000).toISOString());
    const last = segments[segments.length - 1];
    if (last && last.phase === w.phase) last.to = to;
    else segments.push({ phase: w.phase, from, to });
  }
  const todayPct = span.pct(new Date().toISOString());
  const milestones = (ov.profile.races ?? []).filter((r) => !r.is_main);

  return (
    <div className="relative mt-3 h-6">
      <div className="absolute inset-x-0 top-1.5 flex h-3 gap-px overflow-hidden rounded-full">
        {segments.map((s) => (
          <div key={`${s.phase}-${s.from}`} title={`${PHASE_LABEL[s.phase] ?? s.phase} phase`}
            style={{ width: `${s.to - s.from}%`, background: PHASE_COLORS[s.phase] ?? "#334155" }} />
        ))}
      </div>
      {milestones.map((m) => {
        const pct = span.pct(m.date);
        if (pct <= 0 || pct >= 100) return null;
        const achieved = m.status === "achieved";
        return (
          <div key={m.id}
            title={`${m.name}${achieved ? " ✓" : ""}`}
            className={`absolute top-2 h-2 w-2 -translate-x-1/2 rounded-full border-2 border-bg-card ${
              achieved ? "bg-metric-green" : "bg-text-primary"}`}
            style={{ left: `${pct}%` }} />
        );
      })}
      {todayPct > 0 && todayPct < 100 && (
        <div className="absolute top-0 h-6 w-0.5 -translate-x-1/2 rounded bg-text-primary"
          title="Today" style={{ left: `${todayPct}%` }} />
      )}
      <div className="absolute -right-1 -top-0.5 text-[13px]" title={race.name}>🏁</div>
    </div>
  );
}
