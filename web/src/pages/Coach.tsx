// Coach tab — the structured race-goal journey. Everything numeric on this page
// comes from the athlete server's deterministic, corpus-grounded math (%HFmax +
// Karvonen zone bands, benchmark prognosis, ramp-capped week volumes — see
// docs/trainingsregeln.md); the coach agent only fills workouts. Three states:
//   1. no race goal   → goal capture form
//   2. goal, no plan  → hero tiles + "Plan erstellen" (polls while generating)
//   3. plan           → hero + timeline band + this-week cards + volume chart + zones
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, CalendarDays, Flag, Loader2, Sparkles } from "lucide-react";
import { useMemo, useState } from "react";
import { Link } from "react-router-dom";

import { InfoHint } from "../components/InfoHint";
import { PageHeader } from "../components/PageHeader";
import { Spinner } from "../components/Spinner";
import type { AthleteOverview, PlanWeek, TimelineEvent } from "../lib/api";
import { generatePlan, getAthleteOverview, setRaceGoal } from "../lib/api";

// Zone ramp — warm ramp on the secondary (orange) accent, light→dark = easy→hard.
// German training bands ReKom/GA1/GA2/WSA (%HFmax; docs/trainingsregeln.md).
const ZONE_COLORS: Record<string, string> = {
  REKOM: "#FFD9B8", GA1: "#FDBA74", GA12: "#FB923C", GA2: "#FB923C", WSA: "#B45309",
};
const ZONE_NAME: Record<string, string> = {
  REKOM: "Regeneration", GA1: "Grundlage 1", GA2: "Grundlage 2", WSA: "Wettkampftempo",
};
const ZONE_ORDER = ["ReKom", "GA1", "GA2", "WSA"];
// Legacy Z1–Z5 plans map onto the German bands, so old and new plans read alike.
const Z_ALIAS: Record<string, string> = { Z1: "ReKom", Z2: "GA1", Z3: "GA2", Z4: "WSA", Z5: "WSA", GA12: "GA2" };
const DE_LABEL: Record<string, string> = { REKOM: "ReKom", GA1: "GA1", GA2: "GA2", WSA: "WSA" };
const zoneKey = (z?: string) => (z ?? "").replace(/[\s/_-]/g, "").toUpperCase();
// Canonical German zone label for any input (aliases legacy Z1–Z5 → ReKom/GA1/GA2/WSA).
const canonZone = (z?: string): string => {
  const k = zoneKey(z);
  return Z_ALIAS[k] ?? DE_LABEL[k] ?? (z ?? "");
};
const zoneColor = (z?: string) => ZONE_COLORS[zoneKey(canonZone(z))] ?? "#64748B";
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
      <div className="fd-card p-6 text-text-muted">
        Athleten-Daten nicht erreichbar — läuft der athlete-Server (:8109)?
      </div>
    );
  }
  const ov = q.data;
  const race = ov.profile?.race;

  return (
    <div className="space-y-5">
      {!race ? (
        <>
          <PageHeader title="Coach" subtitle="Dein strukturiertes Trainingsziel" />
          <GoalForm onSaved={() => qc.invalidateQueries({ queryKey: ["athlete-overview"] })} />
        </>
      ) : (
        <>
          {/* Roter Faden: Ziel & Status → was diese Woche ansteht → Details darunter. */}
          <Hero ov={ov} />
          {ov.plan ? (
            <>
              <ThisWeek plan={ov.plan} />
              <TimelineBand ov={ov} />
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
    <div className="max-w-xl fd-card p-6">
      <div className="mb-1 flex items-center gap-2 text-text-primary">
        <Flag size={18} className="text-secondary" />
        <h2 className="text-sm font-semibold">Wettkampfziel festlegen</h2>
      </div>
      <p className="mb-5 flex items-center gap-1.5 text-xs text-text-muted">
        Datum, Distanz und Zielzeit — daraus baut der Coach deinen Plan.
        <InfoHint text="Für einen konkreten Wettkampf mit Datum. Freie Motivations-Ziele bleiben auf dem Dashboard." />
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

// ── 2. hero — the thesis at a glance: which race, how far off, on track? ───────

function Hero({ ov }: { ov: AthleteOverview }) {
  const race = ov.profile.race!;
  const plan = ov.plan;
  const prog = prognosisShort(ov.prognosis);
  const onTrack = ov.prognosis?.on_track;
  const thisWeek = plan?.weeks.find((w) => w.week === plan.current_week);
  const weekLabel = plan?.current_week
    ? `Woche ${plan.current_week}/${plan.n_weeks ?? plan.weeks.length}`
    : plan ? `${plan.n_weeks ?? plan.weeks.length} Wochen` : "kein Plan";
  const phaseLabel = thisWeek ? `${PHASE_LABEL[thisWeek.phase]}-Phase` : fmtDate(race.date);
  const chipClass =
    onTrack === false ? "border-metric-amber/40 bg-metric-amber/10 text-metric-amber"
      : onTrack ? "border-metric-green/40 bg-metric-green/10 text-metric-green"
        : "border-border bg-bg-surface text-text-muted";

  return (
    <div className="fd-card px-5 py-4 sm:px-6 sm:py-5">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="fd-label">Coach · Dein Weg</div>
          <h1 className="mt-0.5 truncate text-xl font-bold text-text-primary sm:text-2xl">{race.name}</h1>
        </div>
        <span className={`flex shrink-0 items-center gap-1 rounded-full border px-2.5 py-1 text-xs font-semibold ${chipClass}`}>
          {prog.text}
          {prog.info && <InfoHint text={prog.info} label="Prognose-Detail" />}
        </span>
      </div>
      <div className="mt-4 flex flex-wrap items-end gap-x-7 gap-y-3">
        <Stat big={ov.days_to_race != null ? `${ov.days_to_race}` : "—"} unit="Tage" label="bis zum Wettkampf" primary />
        <Stat big={weekLabel} label={phaseLabel} />
        <Stat big={race.target_time ?? "—"} label="Zielzeit" />
        <Stat big={`${race.distance_km} km`} label="Distanz" muted />
      </div>
    </div>
  );
}

function Stat({ big, unit, label, primary, muted }: {
  big: string; unit?: string; label: string; primary?: boolean; muted?: boolean;
}) {
  return (
    <div>
      <div className={`font-bold leading-none tabular-nums ${
        primary ? "text-3xl text-text-primary sm:text-4xl"
          : muted ? "text-lg text-text-muted" : "text-2xl text-text-primary"}`}>
        {big}
        {unit && <span className="ml-1 text-sm font-medium text-text-muted">{unit}</span>}
      </div>
      <div className="mt-1 text-[11px] text-text-faint">{label}</div>
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
    <div className="fd-card p-6">
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
          <p className="mb-4 flex items-center gap-1.5 text-xs text-text-muted">
            Periodisierter Plan aus deinen echten Werten — Basis → Aufbau → Peak → Taper.
            <InfoHint text="Wochenvolumen und Rampe (≤ 8 %/Woche) werden deterministisch berechnet; der Coach füllt die Einheiten aus deinen Zonen und begründet jede Wahl." />
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
    <div className="fd-card px-5 py-4">
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
          <div key={`${wo.day}-${i}`} className="flex flex-col gap-2 fd-card px-4 py-3.5">
            <div className="flex items-center justify-between">
              <span className="text-[11px] font-bold uppercase tracking-wider text-text-faint">{wo.day}</span>
              <span className="rounded-md px-2 py-0.5 text-[11px] font-bold text-bg-app"
                style={{ background: zoneColor(wo.zone) }}>
                {canonZone(wo.zone)}
              </span>
            </div>
            <h3 className="text-[14.5px] font-semibold text-text-primary">{wo.title}</h3>
            <div className="text-xs text-text-muted">
              {[wo.distance_km && `${wo.distance_km} km`, wo.duration_min && `${wo.duration_min} min`,
                wo.pace_range, wo.hr_range && `${wo.hr_range} bpm`, wo.structure]
                .filter(Boolean).join(" · ")}
            </div>
            {wo.why && (
              <p className="border-l-2 border-border pl-2.5 text-[11.5px] text-text-muted">{wo.why}</p>
            )}
            {wo.source && <SourceReveal source={wo.source} />}
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
          <div className="fd-card p-4 text-xs text-text-muted">
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
    <div className="fd-card px-5 py-4 lg:col-span-3">
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
  return (
    <div className="fd-card px-5 py-4 lg:col-span-2">
      <div className="mb-3 flex items-baseline justify-between">
        <span className="flex items-center gap-1.5">
          <h2 className="text-sm font-semibold text-text-primary">Deine Zonen</h2>
          {(ov.zones?.hr?.basis || ov.zones?.pace?.basis) && (
            <InfoHint
              label="Berechnungsbasis"
              text={`Basis: ${[ov.zones.hr?.basis, ov.zones.pace?.basis].filter(Boolean).join(" · ")}`}
            />
          )}
        </span>
        <span className="fd-label">
          {hr || pace ? "berechnet, nicht geschätzt" : "noch nicht berechnet"}
        </span>
      </div>
      {hr || pace ? (
        <table className="w-full text-xs">
          <thead>
            <tr className="fd-label text-left">
              <th className="border-b border-border px-1.5 py-1">Bereich</th>
              <th className="border-b border-border px-1.5 py-1">HF (%HFmax)</th>
              <th className="border-b border-border px-1.5 py-1">Pace</th>
            </tr>
          </thead>
          <tbody>
            {ZONE_ORDER.map((z) => (
              <tr key={z}>
                <td className="border-b border-border px-1.5 py-1.5 font-bold text-text-primary">
                  <span className="mr-2 inline-block h-2.5 w-2.5 rounded-sm align-[-1px]" style={{ background: zoneColor(z) }} />
                  {z} <span className="font-normal text-text-muted">{ZONE_NAME[zoneKey(z)]}</span>
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
          Zonen werden beim Plan-Erstellen aus deinen echten Garmin-/Strava-Werten berechnet.
        </p>
      )}
    </div>
  );
}

// Source is opt-in: show a quiet toggle, reveal the literature source on request.
function SourceReveal({ source }: { source: string }) {
  const [open, setOpen] = useState(false);
  if (open) return <p className="text-[11px] text-text-faint">Quelle: {source}</p>;
  return (
    <button
      type="button"
      onClick={() => setOpen(true)}
      className="self-start text-[11px] font-medium text-text-faint transition-colors hover:text-text-muted"
    >
      Quelle anzeigen
    </button>
  );
}

// Prognosis: a SHORT status for the tile, with the full detail behind an ⓘ.
function prognosisShort(prog?: AthleteOverview["prognosis"]): { text: string; info?: string } {
  if (!prog)
    return { text: "Prognose offen", info: "Für eine Prognose einen Referenzlauf nahe der Zieldistanz aufnehmen." };
  if (prog.note) return { text: "Benchmark nötig", info: prog.note };
  if (prog.required_pace)
    return {
      text: prog.on_track ? "auf Kurs" : "Tempo fehlt noch",
      info: `Benchmark ${prog.benchmark_pace ?? "—"} · Zieltempo ${prog.required_pace}`,
    };
  return { text: prog.basis ?? "—" };
}

function fmtDate(iso: string): string {
  try {
    return new Date(iso).toLocaleDateString("de-DE", { weekday: "short", day: "numeric", month: "short", year: "numeric" });
  } catch {
    return iso;
  }
}
