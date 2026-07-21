// Coach tab — the structured race-goal journey. Everything numeric on this page
// comes from the athlete server's deterministic, corpus-grounded math (%HFmax +
// Karvonen zone bands, benchmark prognosis, ramp-capped week volumes — see
// docs/trainingsregeln.md); the coach agent only fills workouts. Three states:
//   1. no race goal   → goal capture form
//   2. goal, no plan  → hero tiles + "Plan erstellen" (polls while generating)
//   3. plan           → hero + timeline band + this-week cards + volume chart + zones
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, CalendarDays, Flag, Loader2, Pencil, Sparkles } from "lucide-react";
import { useMemo, useState } from "react";
import { Link } from "react-router-dom";

import { GoalsSection } from "../components/goal/GoalsSection";
import { InfoHint } from "../components/InfoHint";
import { PageHeader } from "../components/PageHeader";
import { Spinner } from "../components/Spinner";
import type { AthleteOverview, PlanWeek, RaceGoal, TimelineEvent } from "../lib/api";
import {
  addMilestone, deleteMilestone, generatePlan, getAthleteOverview, setRaceGoal,
  updateMilestoneStatus,
} from "../lib/api";

// Zone ramp — warm ramp on the secondary (orange) accent, light→dark = easy→hard.
// German training bands ReKom/GA1/GA2/WSA (%HFmax; docs/trainingsregeln.md).
const ZONE_COLORS: Record<string, string> = {
  REKOM: "#FFD9B8", GA1: "#FDBA74", GA12: "#FB923C", GA2: "#FB923C", WSA: "#B45309",
};
const ZONE_NAME: Record<string, string> = {
  REKOM: "Recovery", GA1: "Base 1", GA2: "Base 2", WSA: "Race pace",
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
  base: "Base", build: "Build", peak: "Peak", taper: "Taper",
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
        Athlete data unavailable — is the athlete server (:8109) running?
      </div>
    );
  }
  const ov = q.data;
  const race = ov.profile?.race;

  return (
    <div className="space-y-5">
      {!race ? (
        <>
          <PageHeader title="Coach" subtitle="Your structured race goal" />
          <GoalForm onSaved={() => qc.invalidateQueries({ queryKey: ["athlete-overview"] })} />
        </>
      ) : (
        <>
          {/* Through-line: goal & status first, then this week's action, then detail. */}
          <Hero ov={ov} />
          <RaceMilestones ov={ov} />
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

      <div className="h-px bg-border" />
      <GoalsSection />
    </div>
  );
}

// ── 1. goal capture ───────────────────────────────────────────────────────────

function GoalForm({ initial, weeklySessions, onSaved, onCancel }: {
  initial?: RaceGoal; weeklySessions?: number; onSaved: () => void; onCancel?: () => void;
}) {
  const isEdit = !!initial;
  const [form, setForm] = useState({
    race_name: initial?.name ?? "",
    race_date: initial?.date ?? "",
    distance_km: initial ? String(initial.distance_km) : "",
    target_time: initial?.target_time ?? "",
    weekly_sessions: String(weeklySessions ?? 4),
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
        <h2 className="text-sm font-semibold">{isEdit ? "Edit your race goal" : "Set your race goal"}</h2>
      </div>
      <p className="mb-5 flex items-center gap-1.5 text-xs text-text-muted">
        Date, distance and target time — the coach builds your plan from these.
        <InfoHint text="For a concrete race with a date. Free-form motivational goals live in your goals below." />
      </p>
      <div className="grid gap-3 sm:grid-cols-2">
        <label className="text-xs text-text-muted">
          Race
          <input value={form.race_name} onChange={set("race_name")} placeholder="Baden-Marathon Half Marathon"
            className="mt-1 w-full rounded-lg border border-border bg-bg-app px-3 py-2 text-sm text-text-primary outline-none focus:border-accent" />
        </label>
        <label className="text-xs text-text-muted">
          Date
          <input type="date" value={form.race_date} onChange={set("race_date")}
            className="mt-1 w-full rounded-lg border border-border bg-bg-app px-3 py-2 text-sm text-text-primary outline-none focus:border-accent" />
        </label>
        <label className="text-xs text-text-muted">
          Distance (km)
          <input type="number" step="0.1" min="1" value={form.distance_km} onChange={set("distance_km")} placeholder="21.1"
            className="mt-1 w-full rounded-lg border border-border bg-bg-app px-3 py-2 text-sm text-text-primary outline-none focus:border-accent" />
        </label>
        <label className="text-xs text-text-muted">
          Target time (optional)
          <input value={form.target_time} onChange={set("target_time")} placeholder="1:45:00"
            className="mt-1 w-full rounded-lg border border-border bg-bg-app px-3 py-2 text-sm text-text-primary outline-none focus:border-accent" />
        </label>
        <label className="text-xs text-text-muted">
          Sessions per week
          <input type="number" min="1" max="14" value={form.weekly_sessions} onChange={set("weekly_sessions")}
            className="mt-1 w-full rounded-lg border border-border bg-bg-app px-3 py-2 text-sm text-text-primary outline-none focus:border-accent" />
        </label>
      </div>
      {isEdit && (
        <p className="mt-3 flex items-start gap-1.5 text-xs text-metric-amber">
          <AlertTriangle size={13} className="mt-0.5 shrink-0" />
          Saving will clear your current plan — you'll need to generate a new one.
        </p>
      )}
      {error && <p className="mt-3 text-xs text-metric-red">{error}</p>}
      <div className="mt-5 flex gap-2">
        <button type="button" disabled={!valid || save.isPending} onClick={() => save.mutate()}
          className="rounded-lg bg-accent px-4 py-2 text-sm font-semibold text-bg-app hover:bg-accent-hover disabled:opacity-40">
          {save.isPending ? "Saving…" : isEdit ? "Save changes" : "Save goal"}
        </button>
        {onCancel && (
          <button type="button" onClick={onCancel} className="fd-btn-secondary text-sm">
            Cancel
          </button>
        )}
      </div>
    </div>
  );
}

// ── 2. hero — the thesis at a glance: which race, how far off, on track? ───────

function Hero({ ov }: { ov: AthleteOverview }) {
  const qc = useQueryClient();
  const [editing, setEditing] = useState(false);
  const race = ov.profile.race!;
  const plan = ov.plan;
  const prog = prognosisShort(ov.prognosis);
  const onTrack = ov.prognosis?.on_track;
  const thisWeek = plan?.weeks.find((w) => w.week === plan.current_week);
  const weekLabel = plan?.current_week
    ? `Week ${plan.current_week}/${plan.n_weeks ?? plan.weeks.length}`
    : plan ? `${plan.n_weeks ?? plan.weeks.length} weeks` : "no plan";
  const phaseLabel = thisWeek ? `${PHASE_LABEL[thisWeek.phase]} phase` : fmtDate(race.date);
  const chipClass =
    onTrack === false ? "border-metric-amber/40 bg-metric-amber/10 text-metric-amber"
      : onTrack ? "border-metric-green/40 bg-metric-green/10 text-metric-green"
        : "border-border bg-bg-surface text-text-muted";

  if (editing) {
    return (
      <GoalForm
        initial={race}
        weeklySessions={ov.profile.weekly_sessions}
        onSaved={() => { setEditing(false); qc.invalidateQueries({ queryKey: ["athlete-overview"] }); }}
        onCancel={() => setEditing(false)}
      />
    );
  }

  return (
    <div className="fd-card px-5 py-4 sm:px-6 sm:py-5">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex items-center gap-1">
            <span className="fd-label">Main goal</span>
            <InfoHint text="The one race that drives your training plan. Add milestones below to mark progress on the way there." />
          </div>
          <div className="mt-0.5 flex items-center gap-1.5">
            <h1 className="truncate text-xl font-bold text-text-primary sm:text-2xl">{race.name}</h1>
            <button type="button" onClick={() => setEditing(true)} title="Edit main goal" aria-label="Edit main goal"
              className="flex h-6 w-6 shrink-0 items-center justify-center rounded-md text-text-faint hover:bg-bg-surface hover:text-text-primary">
              <Pencil size={13} strokeWidth={2} />
            </button>
          </div>
        </div>
        <span className={`flex shrink-0 items-center gap-1 rounded-full border px-2.5 py-1 text-xs font-semibold ${chipClass}`}>
          {prog.text}
          {prog.info && <InfoHint text={prog.info} label="Forecast detail" />}
        </span>
      </div>
      <div className="mt-4 flex flex-wrap items-end gap-x-7 gap-y-3">
        <Stat big={ov.days_to_race != null ? `${ov.days_to_race}` : "—"} unit="days" label="to race day" primary />
        <Stat big={weekLabel} label={phaseLabel} />
        <Stat big={race.target_time ?? "—"} label="Target time" />
        <Stat big={`${race.distance_km} km`} label="Distance" muted />
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

// ── 2b. milestone races — hierarchical goals (a tune-up race before the main one) ──
// Milestones are checkpoints on the way to the main goal above — either a real
// tune-up/minor race or a non-race training checkpoint (e.g. "first 15 km long
// run"). They never change the plan's volumes; the coach plans gently around a
// race-kind one. The coach also creates some of these itself while building a
// plan, to make a distant main goal feel closer. Collapsed "+ Add milestone"
// form, expands on click.

const KIND_ICON: Record<string, string> = { race: "🚩", checkpoint: "🎯" };

function RaceMilestones({ ov }: { ov: AthleteOverview }) {
  const qc = useQueryClient();
  const [showForm, setShowForm] = useState(false);
  const milestones = (ov.profile.races ?? []).filter((r) => !r.is_main);
  const invalidate = () => qc.invalidateQueries({ queryKey: ["athlete-overview"] });

  const del = useMutation({
    mutationFn: (id: string) => deleteMilestone(id),
    onSuccess: invalidate,
  });
  const toggleStatus = useMutation({
    mutationFn: ({ id, status }: { id: string; status: "pending" | "achieved" }) =>
      updateMilestoneStatus(id, status),
    onSuccess: invalidate,
  });

  return (
    <div className="fd-card px-5 py-4">
      <div className="mb-3 flex items-baseline justify-between">
        <div className="flex items-center gap-1">
          <h2 className="text-sm font-semibold text-text-primary">Milestones</h2>
          <InfoHint text="Checkpoints on the way to your main goal — a tune-up race or a training checkpoint. They don't change your plan's volume; your coach adds some of these automatically to keep the goal feeling achievable." />
        </div>
        <button type="button" onClick={() => setShowForm((s) => !s)}
          className="fd-btn-ghost text-xs">
          {showForm ? "Close" : "+ Add milestone"}
        </button>
      </div>

      {milestones.length === 0 && !showForm && (
        <p className="text-xs text-text-muted">
          No milestones yet — your coach will add some when it builds your plan, or add one yourself
          (e.g. a tune-up half marathon, or "first 15 km long run").
        </p>
      )}

      {milestones.length > 0 && (
        <div className="mb-3 space-y-2">
          {milestones.map((m) => {
            const achieved = m.status === "achieved";
            return (
              <div key={m.id} className={`flex items-center justify-between gap-3 rounded-lg border px-3 py-2 ${
                achieved ? "border-metric-green/30 bg-metric-green/5" : "border-border bg-bg-surface"}`}>
                <div className="min-w-0">
                  <div className={`truncate text-sm font-medium ${achieved ? "text-text-muted line-through" : "text-text-primary"}`}>
                    {KIND_ICON[m.kind ?? "checkpoint"]} {m.name}
                    {m.source === "coach" && <span className="ml-1.5 text-[10px] font-semibold text-accent">COACH PICK</span>}
                  </div>
                  <div className="text-[11px] text-text-muted">
                    {fmtDate(m.date)}{m.distance_km ? ` · ${m.distance_km} km` : ""}
                    {m.days_to_race != null && ` · ${m.days_to_race >= 0 ? `${m.days_to_race} days away` : "past"}`}
                  </div>
                  {m.note && <p className="mt-0.5 text-[11px] text-text-faint">{m.note}</p>}
                </div>
                <div className="flex shrink-0 items-center gap-2">
                  <button type="button"
                    onClick={() => toggleStatus.mutate({ id: m.id!, status: achieved ? "pending" : "achieved" })}
                    disabled={toggleStatus.isPending}
                    className={`text-xs font-medium ${achieved ? "text-metric-green" : "text-text-faint hover:text-metric-green"}`}>
                    {achieved ? "✓ Achieved" : "Mark achieved"}
                  </button>
                  <button type="button" onClick={() => del.mutate(m.id!)} disabled={del.isPending}
                    className="text-xs font-medium text-text-faint hover:text-metric-red">
                    Remove
                  </button>
                </div>
              </div>
            );
          })}
        </div>
      )}

      {showForm && (
        <MilestoneForm onSaved={() => { setShowForm(false); invalidate(); }} />
      )}
    </div>
  );
}

function MilestoneForm({ onSaved }: { onSaved: () => void }) {
  const [form, setForm] = useState({ title: "", target_date: "", distance_km: "", target_time: "", note: "" });
  const [kind, setKind] = useState<"race" | "checkpoint">("checkpoint");
  const [error, setError] = useState<string | null>(null);
  const save = useMutation({
    mutationFn: () =>
      addMilestone({
        title: form.title, target_date: form.target_date, kind,
        distance_km: form.distance_km ? parseFloat(form.distance_km) : undefined,
        target_time: form.target_time || undefined, note: form.note || undefined,
      }),
    onSuccess: onSaved,
    onError: (e: Error) => setError(e.message),
  });
  const set = (k: keyof typeof form) => (e: React.ChangeEvent<HTMLInputElement>) =>
    setForm((f) => ({ ...f, [k]: e.target.value }));
  const valid = form.title && form.target_date && (kind === "checkpoint" || parseFloat(form.distance_km) > 0);

  return (
    <div className="rounded-lg border border-dashed border-border bg-bg-surface/40 p-3">
      <div className="mb-2 inline-flex rounded-lg border border-border bg-bg-surface p-0.5">
        {(["checkpoint", "race"] as const).map((k) => (
          <button key={k} type="button" onClick={() => setKind(k)}
            className={`rounded-md px-3 py-1 text-xs font-semibold transition-colors ${
              kind === k ? "bg-accent text-bg-app" : "text-text-muted hover:text-text-primary"}`}>
            {k === "race" ? "Race" : "Checkpoint"}
          </button>
        ))}
      </div>
      <div className="grid gap-2 sm:grid-cols-2">
        <input value={form.title} onChange={set("title")}
          placeholder={kind === "race" ? "Race name" : "e.g. First 15 km long run"}
          className="fd-input text-sm sm:col-span-2" />
        <input type="date" value={form.target_date} onChange={set("target_date")}
          className="fd-input text-sm" />
        <input type="number" step="0.1" min="0.1" value={form.distance_km} onChange={set("distance_km")}
          placeholder={kind === "race" ? "Distance (km)" : "Distance (optional)"} className="fd-input text-sm" />
        {kind === "race" && (
          <input value={form.target_time} onChange={set("target_time")} placeholder="Target time (optional)"
            className="fd-input text-sm" />
        )}
        <input value={form.note} onChange={set("note")} placeholder="Why this matters (optional)"
          className="fd-input text-sm sm:col-span-2" />
      </div>
      {error && <p className="mt-2 text-xs text-metric-red">{error}</p>}
      <button type="button" disabled={!valid || save.isPending} onClick={() => save.mutate()}
        className="fd-btn-primary mt-3 text-xs disabled:opacity-40">
        {save.isPending ? "Saving…" : "Add milestone"}
      </button>
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
            <div className="text-sm font-medium text-text-primary">The coach is building your plan…</div>
            <div className="text-xs">Fetching real values from Strava/Garmin, computing zones and filling the weeks — takes 1–3 minutes.</div>
          </div>
        </div>
      ) : (
        <>
          {failed && (
            <p className="mb-3 flex items-start gap-2 text-xs text-metric-amber">
              <AlertTriangle size={14} className="mt-0.5 shrink-0" />
              Last attempt failed: {ov.plan_generation?.slice(7)}
            </p>
          )}
          <div className="mb-1 flex items-center gap-2 text-text-primary">
            <Sparkles size={18} className="text-accent" />
            <h2 className="text-sm font-semibold">Generate training plan</h2>
          </div>
          <p className="mb-4 flex items-center gap-1.5 text-xs text-text-muted">
            A periodised plan from your real data — Base → Build → Peak → Taper.
            <InfoHint text="Weekly volume and ramp (≤ 8 %/week) are computed deterministically; the coach fills the sessions from your zones and justifies each choice." />
          </p>
          <button type="button" onClick={onGenerate}
            className="rounded-lg bg-accent px-4 py-2 text-sm font-semibold text-bg-app hover:bg-accent-hover">
            Generate plan
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
  const milestones = (ov.profile.races ?? []).filter((r) => !r.is_main);

  return (
    <div className="fd-card px-5 py-4">
      <div className="mb-3 flex items-baseline justify-between">
        <h2 className="text-sm font-semibold text-text-primary">Your timeline</h2>
        <span className="text-[10.5px] font-semibold uppercase tracking-wider text-text-faint">
          until {fmtDate(race.date)}
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
            <div key={e.id} title={`${e.title} · ${e.start_date}–${e.end_date ?? "open"}`}
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
            <span className="absolute -top-3 -translate-x-1/2 text-[10px] font-bold text-text-primary">Today</span>
          </div>
        )}
        <div className="absolute right-0 top-2 text-[15px]" title={race.name}>🏁</div>
        {/* milestones along the way to the main goal — race or checkpoint */}
        {milestones.map((m) => {
          const pct = span.pct(m.date);
          if (pct <= 0 || pct >= 100) return null;
          return (
            <div key={m.id} title={`${m.name} · ${fmtDate(m.date)}${m.distance_km ? ` · ${m.distance_km} km` : ""}`}
              className="absolute top-0 -translate-x-1/2 text-[13px]" style={{ left: `${pct}%` }}>
              {KIND_ICON[m.kind ?? "checkpoint"]}
            </div>
          );
        })}
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
          {upcoming ? "First week" : "This week"} · {PHASE_LABEL[week.phase]} phase, week {week.week}
          {week.cutback && <span className="ml-2 text-xs font-medium text-metric-amber">Cutback week</span>}
        </h2>
        <span className="text-[10.5px] font-semibold uppercase tracking-wider text-text-faint">
          Target {week.target_km} km
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
                Plan route
              </Link>
              <Link to="/chat"
                className="flex items-center gap-1 rounded-md border border-border bg-bg-app px-2.5 py-1 text-[11.5px] font-semibold text-text-muted hover:border-accent hover:text-text-primary">
                <CalendarDays size={12} /> To calendar
              </Link>
            </div>
          </div>
        ))}
        {!week.workouts.length && (
          <div className="fd-card p-4 text-xs text-text-muted">
            No workouts set for this week.
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
        <h2 className="text-sm font-semibold text-text-primary">Weekly volume · plan</h2>
        <span className="text-[10.5px] text-text-faint">Actual vs. Strava coming soon</span>
      </div>
      <div className="flex h-40 items-end gap-1.5">
        {weeks.map((w) => (
          <div key={w.week} className="group relative flex-1"
            title={`W${w.week} · ${PHASE_LABEL[w.phase]}${w.cutback ? " (cutback)" : ""} · ${w.target_km} km`}>
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
          <h2 className="text-sm font-semibold text-text-primary">Your zones</h2>
          {(ov.zones?.hr?.basis || ov.zones?.pace?.basis) && (
            <InfoHint
              label="Berechnungsbasis"
              text={`Computed from: ${[ov.zones.hr?.basis, ov.zones.pace?.basis].filter(Boolean).join(" · ")}`}
            />
          )}
        </span>
        <span className="fd-label">
          {hr || pace ? "computed, not estimated" : "not computed yet"}
        </span>
      </div>
      {hr || pace ? (
        <table className="w-full text-xs">
          <thead>
            <tr className="fd-label text-left">
              <th className="border-b border-border px-1.5 py-1">Zone</th>
              <th className="border-b border-border px-1.5 py-1">HR (%HFmax)</th>
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
          Zones are computed from your real Garmin/Strava values when the plan is built.
        </p>
      )}
    </div>
  );
}

// Source is opt-in: show a quiet toggle, reveal the literature source on request.
function SourceReveal({ source }: { source: string }) {
  const [open, setOpen] = useState(false);
  if (open) return <p className="text-[11px] text-text-faint">Source: {source}</p>;
  return (
    <button
      type="button"
      onClick={() => setOpen(true)}
      className="self-start text-[11px] font-medium text-text-faint transition-colors hover:text-text-muted"
    >
      Show source
    </button>
  );
}

// Prognosis: a SHORT status for the tile, with the full detail behind an ⓘ.
function prognosisShort(prog?: AthleteOverview["prognosis"]): { text: string; info?: string } {
  if (!prog)
    return { text: "Forecast open", info: "Log a reference run near the goal distance to get a forecast." };
  if (prog.note) return { text: "Benchmark needed", info: prog.note };
  if (prog.required_pace)
    return {
      text: prog.on_track ? "on track" : "off pace",
      info: `Benchmark ${prog.benchmark_pace ?? "—"} · target pace ${prog.required_pace}`,
    };
  return { text: prog.basis ?? "—" };
}

function fmtDate(iso: string): string {
  try {
    return new Date(iso).toLocaleDateString("en-GB", { weekday: "short", day: "numeric", month: "short", year: "numeric" });
  } catch {
    return iso;
  }
}
