// Coach tab — the structured race-goal journey (design: docs artifact
// "Coach-Tab — Design-Entwurf"). Everything numeric on this page comes from the
// athlete server's deterministic math (Riegel prognosis, zone bands, ramp-capped
// week volumes); the coach agent only fills workouts. Three states:
//   1. no race goal   → goal capture form
//   2. goal, no plan  → hero tiles + "Plan erstellen" (polls while generating)
//   3. plan           → hero + timeline band + this-week cards + volume chart + zones
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, CalendarDays, Flag, Loader2, Sparkles } from "lucide-react";
import { useMemo, useState } from "react";
import { Link } from "react-router-dom";

import { PageHeader } from "../components/PageHeader";
import { Spinner } from "../components/Spinner";
import type { AthleteOverview, PlanWeek, TimelineEvent } from "../lib/api";
import { generatePlan, getAthleteOverview, setRaceGoal } from "../lib/api";

// Zone ramp — sequential warm ramp on the app's secondary (orange) accent,
// light→dark = easy→hard; matches the artifact's z1–z5 scale.
const ZONE_COLORS: Record<string, string> = {
  Z1: "#FFD9B8", Z2: "#FDBA74", Z3: "#FB923C", Z4: "#F97316", Z5: "#B45309",
};
const PHASE_COLORS: Record<string, string> = {
  base: "#FDBA74", build: "#FB923C", peak: "#F97316", taper: "#FFD9B8",
};
const PHASE_LABEL: Record<string, string> = {
  base: "Basis", build: "Aufbau", peak: "Peak", taper: "Taper",
};

export function Coach() {
  const qc = useQueryClient();
  const q = useQuery({
    queryKey: ["athlete-overview"],
    queryFn: getAthleteOverview,
    refetchInterval: (query) =>
      query.state.data?.plan_generation === "running" ? 4000 : false,
  });

  const gen = useMutation({
    mutationFn: generatePlan,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["athlete-overview"] }),
  });

  if (q.isLoading) {
    return (
      <div className="flex h-64 items-center justify-center">
        <Spinner />
      </div>
    );
  }
  if (q.isError || !q.data) {
    return (
      <div className="rounded-xl border border-border bg-bg-card p-6 text-text-muted">
        Athleten-Daten nicht erreichbar — läuft der athlete-Server (:8109)?
      </div>
    );
  }
  const ov = q.data;
  const race = ov.profile?.race;

  return (
    <div className="space-y-5">
      <PageHeader
        title="Coach"
        subtitle={race ? `Dein Weg: ${race.name}` : "Dein strukturiertes Trainingsziel"}
      />
      {!race ? (
        <GoalForm onSaved={() => qc.invalidateQueries({ queryKey: ["athlete-overview"] })} />
      ) : (
        <>
          <HeroTiles ov={ov} />
          {ov.plan ? (
            <>
              <TimelineBand ov={ov} />
              <ThisWeek plan={ov.plan} />
              <div className="grid gap-4 lg:grid-cols-5">
                <VolumeChart weeks={ov.plan.weeks} currentWeek={ov.plan.current_week ?? null} />
                <ZonesTable ov={ov} />
              </div>
            </>
          ) : (
            <GenerateCard ov={ov} onGenerate={() => gen.mutate()} busy={gen.isPending} />
          )}
        </>
      )}
    </div>
  );
}

// ── 1. goal capture ───────────────────────────────────────────────────────────

function GoalForm({ onSaved }: { onSaved: () => void }) {
  const [form, setForm] = useState({
    race_name: "", race_date: "", distance_km: "", target_time: "", weekly_sessions: "4",
  });
  const [error, setError] = useState<string | null>(null);
  const save = useMutation({
    mutationFn: () =>
      setRaceGoal({
        race_name: form.race_name,
        race_date: form.race_date,
        distance_km: parseFloat(form.distance_km),
        target_time: form.target_time || undefined,
        weekly_sessions: parseInt(form.weekly_sessions, 10) || 4,
      }),
    onSuccess: onSaved,
    onError: (e: Error) => setError(e.message),
  });
  const set = (k: keyof typeof form) => (e: React.ChangeEvent<HTMLInputElement>) =>
    setForm((f) => ({ ...f, [k]: e.target.value }));
  const valid = form.race_name && form.race_date && parseFloat(form.distance_km) > 0;

  return (
    <div className="max-w-xl rounded-xl border border-border bg-bg-card p-6">
      <div className="mb-1 flex items-center gap-2 text-text-primary">
        <Flag size={18} className="text-secondary" />
        <h2 className="text-sm font-semibold">Wettkampfziel festlegen</h2>
      </div>
      <p className="mb-5 text-xs text-text-muted">
        Datum, Distanz und Zielzeit — daraus baut der Coach deinen periodisierten Plan.
        Motivations-Ziele in Freitext bleiben auf dem Dashboard; hier geht es um den Wettkampf.
      </p>
      <div className="grid gap-3 sm:grid-cols-2">
        <label className="text-xs text-text-muted">
          Wettkampf
          <input value={form.race_name} onChange={set("race_name")} placeholder="Baden-Marathon Halbmarathon"
            className="mt-1 w-full rounded-lg border border-border bg-bg-app px-3 py-2 text-sm text-text-primary outline-none focus:border-accent" />
        </label>
        <label className="text-xs text-text-muted">
          Datum
          <input type="date" value={form.race_date} onChange={set("race_date")}
            className="mt-1 w-full rounded-lg border border-border bg-bg-app px-3 py-2 text-sm text-text-primary outline-none focus:border-accent" />
        </label>
        <label className="text-xs text-text-muted">
          Distanz (km)
          <input type="number" step="0.1" min="1" value={form.distance_km} onChange={set("distance_km")} placeholder="21.1"
            className="mt-1 w-full rounded-lg border border-border bg-bg-app px-3 py-2 text-sm text-text-primary outline-none focus:border-accent" />
        </label>
        <label className="text-xs text-text-muted">
          Zielzeit (optional)
          <input value={form.target_time} onChange={set("target_time")} placeholder="1:45:00"
            className="mt-1 w-full rounded-lg border border-border bg-bg-app px-3 py-2 text-sm text-text-primary outline-none focus:border-accent" />
        </label>
        <label className="text-xs text-text-muted">
          Einheiten pro Woche
          <input type="number" min="1" max="14" value={form.weekly_sessions} onChange={set("weekly_sessions")}
            className="mt-1 w-full rounded-lg border border-border bg-bg-app px-3 py-2 text-sm text-text-primary outline-none focus:border-accent" />
        </label>
      </div>
      {error && <p className="mt-3 text-xs text-metric-red">{error}</p>}
      <button type="button" disabled={!valid || save.isPending} onClick={() => save.mutate()}
        className="mt-5 rounded-lg bg-accent px-4 py-2 text-sm font-semibold text-bg-app hover:bg-accent-hover disabled:opacity-40">
        {save.isPending ? "Speichern…" : "Ziel speichern"}
      </button>
    </div>
  );
}

// ── 2. hero tiles ─────────────────────────────────────────────────────────────

function HeroTiles({ ov }: { ov: AthleteOverview }) {
  const race = ov.profile.race!;
  const plan = ov.plan;
  const prog = ov.prognosis;
  const weekLabel = plan?.current_week
    ? `Woche ${plan.current_week} von ${plan.n_weeks ?? plan.weeks.length}`
    : plan ? `${plan.n_weeks ?? plan.weeks.length} Wochen geplant` : "noch kein Plan";
  const thisWeek = plan?.weeks.find((w) => w.week === plan.current_week);

  return (
    <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
      <Tile label="Countdown"
        big={ov.days_to_race != null ? `${ov.days_to_race}` : "—"} small="Tage"
        hint={`${fmtDate(race.date)} · ${weekLabel}`} />
      <Tile label="Ziel & Prognose"
        big={race.target_time ?? "—"} small="Ziel"
        hint={prog
          ? `Prognose ${prog.predicted_time} · ${prog.on_track == null ? prog.basis : prog.on_track ? "auf Kurs" : "Tempo fehlt noch"}`
          : "Prognose folgt mit den Zonen (echtes Rennergebnis nötig)"}
        hintClass={prog?.on_track === false ? "text-metric-amber" : prog?.on_track ? "text-metric-green" : undefined} />
      <Tile label="Distanz" big={`${race.distance_km}`} small="km"
        hint={ov.zones?.pace ? `Schwellen-Pace ${ov.zones.pace.threshold_pace}` : "Zonen noch nicht berechnet"} />
      <Tile label="Diese Woche"
        big={thisWeek ? `${thisWeek.workouts.length}` : "—"} small={thisWeek ? `Einheiten · ${thisWeek.target_km} km` : ""}
        hint={thisWeek
          ? `${PHASE_LABEL[thisWeek.phase]}-Phase${thisWeek.cutback ? " · Entlastung" : ""}`
          : plan
            ? `Plan startet am ${fmtDate(plan.weeks[0].start_date)}`
            : "Plan erstellen, um zu starten"} />
    </div>
  );
}

function Tile({ label, big, small, hint, hintClass }: {
  label: string; big: string; small?: string; hint?: string; hintClass?: string;
}) {
  return (
    <div className="rounded-xl border border-border bg-bg-card px-4 py-3.5">
      <div className="text-[10.5px] font-semibold uppercase tracking-wider text-text-faint">{label}</div>
      <div className="mt-1 text-2xl font-bold tabular-nums text-text-primary">
        {big} {small && <span className="text-sm font-medium text-text-muted">{small}</span>}
      </div>
      {hint && <div className={`mt-0.5 text-[11.5px] ${hintClass ?? "text-text-muted"}`}>{hint}</div>}
    </div>
  );
}

// ── 3. generate card ─────────────────────────────────────────────────────────

function GenerateCard({ ov, onGenerate, busy }: {
  ov: AthleteOverview; onGenerate: () => void; busy: boolean;
}) {
  const running = ov.plan_generation === "running" || busy;
  const failed = ov.plan_generation?.startsWith("error");
  return (
    <div className="rounded-xl border border-border bg-bg-card p-6">
      {running ? (
        <div className="flex items-center gap-3 text-text-muted">
          <Loader2 size={18} className="animate-spin text-accent" />
          <div>
            <div className="text-sm font-medium text-text-primary">Der Coach erstellt deinen Plan…</div>
            <div className="text-xs">Holt echte Werte aus Strava/Garmin, berechnet Zonen und füllt die Wochen — dauert 1–3 Minuten.</div>
          </div>
        </div>
      ) : (
        <>
          {failed && (
            <p className="mb-3 flex items-start gap-2 text-xs text-metric-amber">
              <AlertTriangle size={14} className="mt-0.5 shrink-0" />
              Letzter Versuch fehlgeschlagen: {ov.plan_generation?.slice(7)}
            </p>
          )}
          <div className="mb-1 flex items-center gap-2 text-text-primary">
            <Sparkles size={18} className="text-accent" />
            <h2 className="text-sm font-semibold">Trainingsplan erstellen</h2>
          </div>
          <p className="mb-4 text-xs text-text-muted">
            Periodisierung (Basis → Aufbau → Peak → Taper) und Wochenvolumen werden deterministisch
            berechnet (Rampe ≤ 8 %/Woche); der Coach füllt die Einheiten aus deinen echten Zonen
            und begründet jede Wahl.
          </p>
          <button type="button" onClick={onGenerate}
            className="rounded-lg bg-accent px-4 py-2 text-sm font-semibold text-bg-app hover:bg-accent-hover">
            Plan generieren
          </button>
        </>
      )}
    </div>
  );
}

// ── 4. timeline band ─────────────────────────────────────────────────────────

function TimelineBand({ ov }: { ov: AthleteOverview }) {
  const plan = ov.plan!;
  const race = ov.profile.race!;
  const span = useMemo(() => {
    const start = new Date(plan.weeks[0].start_date).getTime();
    const end = new Date(race.date).getTime() + 86400000;
    const pct = (iso: string) =>
      Math.max(0, Math.min(100, ((new Date(iso).getTime() - start) / (end - start)) * 100));
    return { start, end, pct };
  }, [plan, race.date]);

  // contiguous phase segments
  const segments: { phase: string; from: number; to: number }[] = [];
  for (const w of plan.weeks) {
    const from = span.pct(w.start_date);
    const to = span.pct(new Date(new Date(w.start_date).getTime() + 7 * 86400000).toISOString());
    const last = segments[segments.length - 1];
    if (last && last.phase === w.phase) last.to = to;
    else segments.push({ phase: w.phase, from, to });
  }
  const todayPct = span.pct(new Date().toISOString());
  const injuries = ov.timeline.filter((e) => e.type === "injury" || e.type === "illness");

  return (
    <div className="rounded-xl border border-border bg-bg-card px-5 py-4">
      <div className="mb-3 flex items-baseline justify-between">
        <h2 className="text-sm font-semibold text-text-primary">Deine Timeline</h2>
        <span className="text-[10.5px] font-semibold uppercase tracking-wider text-text-faint">
          bis {fmtDate(race.date)}
        </span>
      </div>
      <div className="relative h-16">
        <div className="absolute inset-x-0 top-5 flex h-6 gap-0.5 overflow-hidden rounded-md">
          {segments.map((s) => (
            <div key={`${s.phase}-${s.from}`} className="relative flex items-center justify-center"
              style={{ width: `${s.to - s.from}%`, background: PHASE_COLORS[s.phase] ?? "#334155" }}>
              <span className="truncate px-1 text-[10px] font-semibold text-bg-app">
                {PHASE_LABEL[s.phase] ?? s.phase}
              </span>
            </div>
          ))}
        </div>
        {/* injury/illness overlays — hatched + labelled, never colour alone */}
        {injuries.map((e: TimelineEvent) => {
          const from = span.pct(e.start_date);
          const to = e.end_date ? span.pct(e.end_date) : Math.min(from + 4, 100);
          if (to <= 0 || from >= 100) return null;
          return (
            <div key={e.id} title={`${e.title} · ${e.start_date}–${e.end_date ?? "offen"}`}
              className="absolute top-4 h-8 rounded border border-metric-red/60"
              style={{
                left: `${from}%`, width: `${Math.max(to - from, 1)}%`,
                background: "repeating-linear-gradient(135deg, rgba(239,68,68,.5) 0 4px, rgba(239,68,68,.15) 4px 8px)",
              }}>
              <span className="absolute -top-4 whitespace-nowrap text-[10px] text-metric-red">⚠ {e.title}</span>
            </div>
          );
        })}
        {todayPct > 0 && todayPct < 100 && (
          <div className="absolute bottom-1 top-3 w-0.5 rounded bg-text-primary" style={{ left: `${todayPct}%` }}>
            <span className="absolute -top-3 -translate-x-1/2 text-[10px] font-bold text-text-primary">Heute</span>
          </div>
        )}
        <div className="absolute right-0 top-2 text-[15px]" title={race.name}>🏁</div>
      </div>
      <div className="mt-1 flex flex-wrap gap-4 text-[11px] text-text-muted">
        {Object.entries(PHASE_LABEL).map(([k, label]) => (
          <span key={k}>
            <span className="mr-1.5 inline-block h-2.5 w-2.5 rounded-sm align-[-1px]" style={{ background: PHASE_COLORS[k] }} />
            {label}
          </span>
        ))}
      </div>
    </div>
  );
}

// ── 5. this week ─────────────────────────────────────────────────────────────

function ThisWeek({ plan }: { plan: NonNullable<AthleteOverview["plan"]> }) {
  const week: PlanWeek | undefined =
    plan.weeks.find((w) => w.week === plan.current_week) ?? plan.weeks[0];
  if (!week) return null;
  const upcoming = plan.current_week == null;
  return (
    <section>
      <div className="mb-3 flex items-baseline justify-between">
        <h2 className="text-sm font-semibold text-text-primary">
          {upcoming ? "Erste Woche" : "Diese Woche"} · {PHASE_LABEL[week.phase]}-Phase, Woche {week.week}
          {week.cutback && <span className="ml-2 text-xs font-medium text-metric-amber">Entlastungswoche</span>}
        </h2>
        <span className="text-[10.5px] font-semibold uppercase tracking-wider text-text-faint">
          Ziel {week.target_km} km
        </span>
      </div>
      <div className="grid gap-3 md:grid-cols-2">
        {week.workouts.map((wo, i) => (
          <div key={`${wo.day}-${i}`} className="flex flex-col gap-2 rounded-xl border border-border bg-bg-card px-4 py-3.5">
            <div className="flex items-center justify-between">
              <span className="text-[11px] font-bold uppercase tracking-wider text-text-faint">{wo.day}</span>
              <span className="rounded-md px-2 py-0.5 text-[11px] font-bold text-bg-app"
                style={{ background: ZONE_COLORS[wo.zone?.toUpperCase()] ?? "#64748B" }}>
                {wo.zone}
              </span>
            </div>
            <h3 className="text-[14.5px] font-semibold text-text-primary">{wo.title}</h3>
            <div className="text-xs text-text-muted">
              {[wo.distance_km && `${wo.distance_km} km`, wo.duration_min && `${wo.duration_min} min`,
                wo.pace_range, wo.hr_range && `${wo.hr_range} bpm`, wo.structure]
                .filter(Boolean).join(" · ")}
            </div>
            {wo.why && (
              <p className="border-l-2 border-border pl-2.5 text-[11.5px] text-text-muted">
                {wo.why}
                {wo.source && <span className="ml-1.5 text-metric-cyan" title="Quelle aus der Literatur-Bibliothek">({wo.source})</span>}
              </p>
            )}
            <div className="mt-0.5 flex gap-2">
              <Link to="/chat"
                className="rounded-md border border-border bg-bg-app px-2.5 py-1 text-[11.5px] font-semibold text-text-muted hover:border-accent hover:text-text-primary">
                Strecke planen
              </Link>
              <Link to="/chat"
                className="flex items-center gap-1 rounded-md border border-border bg-bg-app px-2.5 py-1 text-[11.5px] font-semibold text-text-muted hover:border-accent hover:text-text-primary">
                <CalendarDays size={12} /> In Kalender
              </Link>
            </div>
          </div>
        ))}
        {!week.workouts.length && (
          <div className="rounded-xl border border-border bg-bg-card p-4 text-xs text-text-muted">
            Keine Einheiten in dieser Woche hinterlegt.
          </div>
        )}
      </div>
    </section>
  );
}

// ── 6. volume chart (plan targets; Ist-Abgleich folgt) ───────────────────────

function VolumeChart({ weeks, currentWeek }: { weeks: PlanWeek[]; currentWeek: number | null }) {
  const max = Math.max(...weeks.map((w) => w.target_km), 1);
  return (
    <div className="rounded-xl border border-border bg-bg-card px-5 py-4 lg:col-span-3">
      <div className="mb-3 flex items-baseline justify-between">
        <h2 className="text-sm font-semibold text-text-primary">Wochenvolumen · Plan</h2>
        <span className="text-[10.5px] text-text-faint">Ist-Abgleich mit Strava folgt</span>
      </div>
      <div className="flex h-40 items-end gap-1.5">
        {weeks.map((w) => (
          <div key={w.week} className="group relative flex-1"
            title={`W${w.week} · ${PHASE_LABEL[w.phase]}${w.cutback ? " (Entlastung)" : ""} · ${w.target_km} km`}>
            <div className={`w-full rounded-t transition-all ${w.week === currentWeek ? "ring-1 ring-text-primary" : ""}`}
              style={{
                height: `${(w.target_km / max) * 152}px`,
                background: PHASE_COLORS[w.phase] ?? "#334155",
                opacity: w.cutback ? 0.55 : 1,
              }} />
            <div className="mt-1 text-center text-[9.5px] text-text-faint">W{w.week}</div>
          </div>
        ))}
      </div>
    </div>
  );
}

// ── 7. zones table ────────────────────────────────────────────────────────────

function ZonesTable({ ov }: { ov: AthleteOverview }) {
  const hr = ov.zones?.hr?.bands_bpm;
  const pace = ov.zones?.pace?.bands_pace;
  const order = ["Z1", "Z2", "Z3", "Z4", "Z5"];
  const names: Record<string, string> = {
    Z1: "Regeneration", Z2: "Grundlage", Z3: "Tempo", Z4: "Schwelle", Z5: "VO₂max",
  };
  return (
    <div className="rounded-xl border border-border bg-bg-card px-5 py-4 lg:col-span-2">
      <div className="mb-3 flex items-baseline justify-between">
        <h2 className="text-sm font-semibold text-text-primary">Deine Zonen</h2>
        <span className="text-[10.5px] font-semibold uppercase tracking-wider text-text-faint">
          {hr || pace ? "berechnet, nicht geschätzt" : "noch nicht berechnet"}
        </span>
      </div>
      {hr || pace ? (
        <table className="w-full text-xs">
          <thead>
            <tr className="text-left text-[10.5px] uppercase tracking-wider text-text-faint">
              <th className="border-b border-border px-1.5 py-1 font-semibold">Zone</th>
              <th className="border-b border-border px-1.5 py-1 font-semibold">Herzfrequenz</th>
              <th className="border-b border-border px-1.5 py-1 font-semibold">Pace</th>
            </tr>
          </thead>
          <tbody>
            {order.map((z) => (
              <tr key={z}>
                <td className="border-b border-border px-1.5 py-1.5 font-bold text-text-primary">
                  <span className="mr-2 inline-block h-2.5 w-2.5 rounded-sm align-[-1px]" style={{ background: ZONE_COLORS[z] }} />
                  {z} {names[z]}
                </td>
                <td className="border-b border-border px-1.5 py-1.5 tabular-nums text-text-muted">
                  {hr?.[z] ? `${hr[z][0]}–${hr[z][1]} bpm` : "—"}
                </td>
                <td className="border-b border-border px-1.5 py-1.5 tabular-nums text-text-muted">
                  {pace?.[z] ? `${pace[z][1].replace("/km", "")}–${pace[z][0]}` : "—"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : (
        <p className="text-xs text-text-muted">
          Der Coach berechnet die Zonen beim Plan-Erstellen aus deinen echten Garmin-/Strava-Werten.
        </p>
      )}
      {(ov.zones?.hr?.basis || ov.zones?.pace?.basis) && (
        <p className="mt-2 text-[10.5px] text-text-faint">
          Basis: {[ov.zones.hr?.basis, ov.zones.pace?.basis].filter(Boolean).join(" · ")}
        </p>
      )}
    </div>
  );
}

function fmtDate(iso: string): string {
  try {
    return new Date(iso).toLocaleDateString("de-DE", { weekday: "short", day: "numeric", month: "short", year: "numeric" });
  } catch {
    return iso;
  }
}
