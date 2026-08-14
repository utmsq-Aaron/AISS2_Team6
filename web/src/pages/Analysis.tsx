import { useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import type { Data, Layout } from "plotly.js";

import {
  CollapsibleSection,
  HowTo,
  PctBar,
  TrendPill,
} from "../components/analysis/AnalysisBits";
import { ActivityMapSection } from "../components/analysis/ActivityMapSection";
import { AnalysisOverview } from "../components/analysis/AnalysisOverview";
import { OfficialStatsSection } from "../components/analysis/OfficialStatsSection";
import { PlanVsActualSection } from "../components/analysis/PlanVsActualSection";
import { TrainingVolumeSection } from "../components/analysis/TrainingVolumeSection";
import { ExplainButton } from "../components/ExplainButton";
import { MetricCard } from "../components/MetricCard";
import { PageHeader } from "../components/PageHeader";
import { PeriodSelector } from "../components/PeriodSelector";
import { PlotlyChart } from "../components/PlotlyChart";
import { Spinner, ErrorBox, EmptyState } from "../components/Spinner";
import { callTool } from "../lib/api";
import {
  PERIOD_DAYS,
  dayStr,
  sportOf,
  type Activity,
  type AthleteResult,
  type ActivitiesResult as StravaActivitiesResult,
  type Period,
} from "../lib/stravaTypes";
import { useUiStore } from "../store/uiStore";
import {
  ACCENT, C_BLUE, C_GREEN, C_GREEN_DARK, C_ORANGE, C_RED, TEXT_MUTED,
} from "../theme/tokens";

// ── tool result shapes (confirmed via live curl against :8000) ──────────────────

interface TLWeek {
  week_start: string;
  total_load: number;
  avg_atl: number;
  avg_ctl: number;
  avg_tsb: number;
}
interface TrainingLoad {
  current?: { atl?: number; ctl?: number; tsb?: number; form?: string };
  weeks?: TLWeek[];
  error?: string;
}

interface TrendPoint {
  date: string;
  name: string;
  distance_km: number | null;
  pace_min_per_km: number | null;
  pace_display?: string | null;
  avg_hr: number | null;
  elevation_m: number | null;
  elevation_per_km: number | null;
}
interface TrendHighlight {
  name: string;
  date: string;
  pace: string;
}
interface PerformanceTrends {
  sport_type?: string;
  activity_count?: number;
  date_range?: { from?: string; to?: string };
  trends?: { pace?: string; heart_rate?: string };
  averages?: {
    pace_min_per_km?: number;
    avg_hr_bpm?: number;
    distance_km?: number;
    elevation_per_km?: number;
  };
  highlights?: { fastest?: TrendHighlight; slowest?: TrendHighlight };
  series?: TrendPoint[];
  error?: string;
}

interface ActivitySummary {
  id: number;
  name: string;
  date?: string;
  start_date?: string;
  distance_km?: number;
  distance?: number;
  sport_type?: string;
}
interface ActivitiesResult {
  total_count?: number;
  activities?: ActivitySummary[];
  error?: string;
}

interface MetricComparison {
  baseline_mean: number;
  baseline_std: number;
  target: number;
  difficulty_percentile: number;
  z_score: number;
}
interface Comparison {
  activity?: {
    id: number;
    name?: string;
    date?: string;
    sport_type?: string;
    distance_km?: number;
    elevation_m?: number;
    pace_display?: string;
    avg_hr?: number;
  };
  baseline_activity_count?: number | string;
  comparisons?: Record<string, MetricComparison | null>;
  overall_difficulty_percentile?: number | null;
  assessment?: string;
  error?: string;
}

// ── helper for cross-cutting tool-error messaging (mirrors _show_tool_error) ────
function toolErrorMessage(error: string, tool: string): string {
  if (/unknown tool/i.test(error)) {
    return `Tool "${tool}" not found — the Strava MCP server is running an older version. Go to Settings → Developer → Restart MCP Servers and try again.`;
  }
  return error;
}

// ════════════════════════════════════════════════════════════════════════════════
// Section 1 — Training Load
// ════════════════════════════════════════════════════════════════════════════════

// The load window follows the page period — the section used to carry its own
// weeks picker, which quietly contradicted the selector at the top of the page.
// Floor of 6 weeks: CTL is a 42-day average, so a shorter window would show a
// "fitness" number that hasn't had time to mean anything. "All time" → 52.
function loadWeeks(periodDays: number): number {
  if (periodDays <= 0) return 52;
  return Math.min(52, Math.max(6, Math.ceil(periodDays / 7)));
}

function TrainingLoadSection({
  refreshVersion,
  periodDays,
}: {
  refreshVersion: number;
  periodDays: number;
}) {
  const weeks = loadWeeks(periodDays);
  const [view, setView] = useState<"bar" | "atl">("bar");

  const { data, isLoading, error } = useQuery({
    queryKey: ["analysis", "training_load", weeks, refreshVersion],
    queryFn: () => callTool<TrainingLoad>("strava__get_training_load", { weeks }),
  });

  return (
    <>
      <p className="mb-3 text-sm text-text-muted">
        Three numbers: how tired you are, how fit you are, and the balance between them.
      </p>

      <HowTo title="💡 How to read Fatigue · Fitness · Form">
        <p>
          <strong>Fatigue</strong> <em>(ATL, last 7 days)</em> — how hard you've trained
          this week. High = tired legs, but adaptation under way.
        </p>
        <p>
          <strong>Fitness</strong> <em>(CTL, last 42 days)</em> — your base, built slowly.
          It only rises through consistent weeks.
        </p>
        <p>
          <strong>Form</strong> <em>(TSB = Fitness − Fatigue)</em> — positive: rested and
          ready to go hard; slightly negative: productive training fatigue; below about
          −30: back off, that's overtraining territory.
        </p>
      </HowTo>

      <p className="fd-label mb-4">
        Window: last {weeks} weeks — follows the period selected in Overview
        {weeks === 6 ? " (6-week minimum, the CTL window)" : ""}.
      </p>

      {isLoading && <Spinner label="Loading training data…" />}
      {error && <ErrorBox message={String(error)} />}
      {data?.error && (
        <ErrorBox message={toolErrorMessage(data.error, "get_training_load")} />
      )}

      {data && !data.error && (() => {
        const cur = data.current ?? {};
        const atl = cur.atl ?? 0;
        const ctl = cur.ctl ?? 0;
        const tsb = cur.tsb ?? 0;
        const form = cur.form ?? "";
        const tsbSign = tsb >= 0 ? "+" : "";

        const weeksRows = data.weeks ?? [];
        const df = weeksRows.slice(-Math.min(weeksRows.length, 20));
        const xs = df.map((w) => w.week_start);

        return (
          <>
            <div className="mb-4 grid grid-cols-1 gap-3 sm:grid-cols-3">
              <MetricCard label="Fatigue" value={atl.toFixed(0)} sub="last 7 days (ATL)" />
              <MetricCard label="Fitness" value={ctl.toFixed(0)} sub="base over 42 days (CTL)" />
              <MetricCard
                label="Form"
                value={`${tsbSign}${tsb.toFixed(0)}`}
                delta={`${tsbSign}${tsb.toFixed(0)}`}
                deltaColor={tsb >= 0 ? "green" : "red"}
                sub="Fitness − Fatigue (TSB)"
              />
            </div>

            <div className="mb-4 flex items-center justify-between gap-3 rounded-lg border border-metric-indigo/40 bg-metric-indigo/10 px-4 py-3 text-sm text-text-primary">
              <span><strong>Current form:</strong> {form}</span>
              <ExplainButton
                title="Training load — fatigue, fitness and form"
                data={{
                  fatigue_atl_7d: atl, fitness_ctl_42d: ctl, form_tsb: tsb,
                  form_label: form,
                  recent_weeks: weeksRows.slice(-8).map((w) => ({
                    week_start: w.week_start, total_load: w.total_load,
                    fatigue: w.avg_atl, fitness: w.avg_ctl, form: w.avg_tsb,
                  })),
                }}
              />
            </div>

            {df.length === 0 ? null : (
              <>
                <div className="mb-3">
                  <PeriodSelector
                    options={["📊 Weekly Load", "📈 Fatigue / Fitness Trend"] as const}
                    value={view === "bar" ? "📊 Weekly Load" : "📈 Fatigue / Fitness Trend"}
                    onChange={(v) => setView(v === "📊 Weekly Load" ? "bar" : "atl")}
                  />
                </div>

                {view === "bar" ? (
                  <PlotlyChart
                    height={280}
                    data={[
                      {
                        type: "bar",
                        x: xs,
                        y: df.map((w) => w.total_load),
                        marker: { color: ACCENT },
                        name: "Training load",
                      } as Data,
                    ]}
                    layout={
                      {
                        xaxis: { title: { text: "Week" } },
                        yaxis: { title: { text: "Load (a.u.)" } },
                      } as Partial<Layout>
                    }
                  />
                ) : (
                  <PlotlyChart
                    height={300}
                    data={[
                      {
                        type: "scatter",
                        mode: "lines",
                        x: xs,
                        y: df.map((w) => w.avg_atl),
                        name: "Fatigue (ATL, 7d)",
                        line: { color: C_RED, width: 2 },
                      } as Data,
                      {
                        type: "scatter",
                        mode: "lines",
                        x: xs,
                        y: df.map((w) => w.avg_ctl),
                        name: "Fitness (CTL, 42d)",
                        line: { color: C_BLUE, width: 2 },
                      } as Data,
                      {
                        type: "bar",
                        x: xs,
                        y: df.map((w) => w.avg_tsb),
                        name: "Form (TSB)",
                        marker: {
                          color: df.map((w) => (w.avg_tsb >= 0 ? C_GREEN : C_ORANGE)),
                        },
                        opacity: 0.5,
                      } as Data,
                    ]}
                    layout={{ barmode: "overlay" } as Partial<Layout>}
                  />
                )}
              </>
            )}
          </>
        );
      })()}
    </>
  );
}

// ════════════════════════════════════════════════════════════════════════════════
// Section 2 — Performance Trend
// ════════════════════════════════════════════════════════════════════════════════

const TREND_SPORTS = [
  "Run",
  "Ride",
  "Hike",
  "Walk",
  "TrailRun",
  "MountainBikeRide",
  "Swim",
  "WeightTraining",
] as const;

type TrendTab = "pace" | "hr" | "dist" | "elev";

function PerformanceTrendSection({
  refreshVersion,
  activities,
  period,
  activitiesReady,
}: {
  refreshVersion: number;
  activities: Activity[];
  period: Period;
  /** False while the shared activity query is loading or has failed — until then
   *  an empty `activities` says nothing, and "not enough data" would be a lie. */
  activitiesReady: boolean;
}) {
  const [sport, setSport] = useState<string>("Run");
  const [tab, setTab] = useState<TrendTab>("pace");

  // The window is the page period, not a free-floating count: the tool takes a
  // `limit` of most-recent same-sport activities, and since the period is always
  // trailing from now, "the N in the period" and "the last N" are the same set.
  // This replaced a standalone "Activities: N" slider that ignored the selector
  // at the top of the page. Capped so "All time" can't ask for a huge analysis.
  const inPeriod = useMemo(
    () => activities.filter((a) => sportOf(a) === sport).length,
    [activities, sport],
  );
  const limit = Math.min(inPeriod, 200);
  const tooFew = activitiesReady && limit < 3; // regression over 1–2 points says nothing

  const { data, isLoading, error } = useQuery({
    queryKey: ["analysis", "trends", sport, limit, refreshVersion],
    enabled: activitiesReady && !tooFew,
    queryFn: () =>
      callTool<PerformanceTrends>("strava__analyze_performance_trends", {
        sport_type: sport,
        limit,
      }),
  });

  return (
    <>
      <p className="mb-3 text-sm text-text-muted">
        Shows how your key metrics have evolved across your last N activities. Use this to
        spot genuine improvement — or catch early signs of overtraining.
      </p>

      <HowTo title="💡 How to read the charts">
        <p>
          <strong>Pace</strong> — lower numbers (min/km) mean <em>faster</em>. The Y-axis
          is inverted so improvement always looks like a line going <em>up</em>. A dotted
          line marks your average.
        </p>
        <p>
          <strong>Heart Rate</strong> — a <em>declining</em> average HR at the same effort
          is a classic sign of improving aerobic efficiency.
        </p>
        <p>
          <strong>Distance</strong> — how far each activity was. <strong>Elevation/km</strong>{" "}
          — vertical gain per kilometre; a pace trend that coincides with rising
          elevation/km is likely terrain-driven, not fitness-driven.
        </p>
        <p>🟠 Improving · ⚪ Stable · 🔴 Declining — linear regression over the window.</p>
      </HowTo>

      <div className="mb-4 flex flex-wrap items-end gap-4">
        <div>
          <label htmlFor="trend-sport" className="fd-label mb-1 block">
            Sport type
          </label>
          <select
            id="trend-sport"
            className="fd-input"
            value={sport}
            onChange={(e) => setSport(e.target.value)}
          >
            {TREND_SPORTS.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
        </div>
        <p className="fd-label pb-2">
          {limit} {sport} activit{limit === 1 ? "y" : "ies"} in the last {period.toLowerCase()}
          {" "}— follows the period selected in Overview
        </p>
      </div>

      {!activitiesReady && <Spinner label="Loading activities…" />}
      {tooFew && (
        <EmptyState
          message={`Not enough ${sport} activities in the last ${period.toLowerCase()} to show a trend — pick a longer period in Overview.`}
        />
      )}
      {isLoading && <Spinner label={`Analysing ${sport} activities…`} />}
      {error && <ErrorBox message={String(error)} />}
      {data?.error && (
        <ErrorBox message={toolErrorMessage(data.error, "analyze_performance_trends")} />
      )}
      {data && !data.error && !data.series?.length && (
        <EmptyState message={`No ${sport} activities found.`} />
      )}

      {data && !data.error && !!data.series?.length && (() => {
        const series = data.series!;
        const trends = data.trends ?? {};
        const avgs = data.averages ?? {};
        const hi = data.highlights ?? {};
        const dr = data.date_range ?? {};
        const n = data.activity_count ?? 0;

        const dates = series.map((s) => s.date);
        const names = series.map((s) => s.name);

        // Pace
        const paceIdx = series.map((_, i) => i).filter((i) => series[i].pace_min_per_km != null);
        const avgP = avgs.pace_min_per_km;
        // HR
        const hrIdx = series.map((_, i) => i).filter((i) => series[i].avg_hr != null);
        const avgHr = avgs.avg_hr_bpm;
        // Distance
        const distIdx = series.map((_, i) => i).filter((i) => series[i].distance_km != null);
        const avgD = avgs.distance_km;
        // Elevation/km
        const elevIdx = series.map((_, i) => i).filter((i) => series[i].elevation_per_km != null);
        const avgE = avgs.elevation_per_km;

        function hline(y: number, label: string): Partial<Layout> {
          return {
            shapes: [
              {
                type: "line",
                xref: "paper",
                x0: 0,
                x1: 1,
                yref: "y",
                y0: y,
                y1: y,
                line: { color: TEXT_MUTED, width: 1, dash: "dot" },
              },
            ],
            annotations: [
              {
                xref: "paper",
                x: 1,
                yref: "y",
                y,
                text: label,
                showarrow: false,
                font: { color: TEXT_MUTED, size: 10 },
                xanchor: "right",
                yanchor: "bottom",
              },
            ],
          } as Partial<Layout>;
        }

        return (
          <>
            <div className="mb-4 grid grid-cols-1 items-center gap-3 sm:grid-cols-3">
              <div className="text-sm text-text-muted">
                Pace: <TrendPill direction={trends.pace ?? "insufficient data"} />
              </div>
              <div className="text-sm text-text-muted">
                HR: <TrendPill direction={trends.heart_rate ?? "insufficient data"} />
              </div>
              <div className="flex items-center justify-between gap-2 text-xs text-text-muted">
                <span>{n} activities · {dr.from ?? ""} – {dr.to ?? ""}</span>
                <ExplainButton
                  title={`Performance trend — last ${n} ${sport} activities`}
                  data={{
                    sport, activity_count: n, date_range: dr,
                    trends, averages: avgs, highlights: hi,
                  }}
                />
              </div>
            </div>

            <div className="mb-3">
              <PeriodSelector
                options={["⏱️ Pace", "❤️ Heart Rate", "📏 Distance", "⛰️ Elevation/km"] as const}
                value={
                  tab === "pace"
                    ? "⏱️ Pace"
                    : tab === "hr"
                      ? "❤️ Heart Rate"
                      : tab === "dist"
                        ? "📏 Distance"
                        : "⛰️ Elevation/km"
                }
                onChange={(v) =>
                  setTab(
                    v === "⏱️ Pace"
                      ? "pace"
                      : v === "❤️ Heart Rate"
                        ? "hr"
                        : v === "📏 Distance"
                          ? "dist"
                          : "elev",
                  )
                }
              />
            </div>

            {tab === "pace" &&
              (paceIdx.length === 0 ? (
                <EmptyState message="No pace data available." />
              ) : (
                <>
                  <PlotlyChart
                    height={300}
                    data={[
                      {
                        type: "scatter",
                        mode: "lines+markers",
                        x: paceIdx.map((i) => dates[i]),
                        y: paceIdx.map((i) => series[i].pace_min_per_km as number),
                        name: "Pace",
                        line: { color: ACCENT, width: 2 },
                        marker: { size: 5 },
                        text: paceIdx.map((i) => names[i]),
                        hovertemplate: "%{text}<br>%{y:.2f} min/km<extra></extra>",
                      } as Data,
                    ]}
                    layout={
                      {
                        yaxis: { title: { text: "min/km" }, autorange: "reversed" },
                        ...(avgP ? hline(avgP, `avg ${avgP.toFixed(2)}`) : {}),
                      } as Partial<Layout>
                    }
                  />
                  <div className="mt-1 text-xs text-text-muted">
                    ⬆ Y-axis inverted — higher = faster
                  </div>
                  {(hi.fastest || hi.slowest) && (
                    <div className="mt-3 grid grid-cols-1 gap-3 sm:grid-cols-2">
                      {hi.fastest && (
                        <div className="rounded-lg border border-metric-green/40 bg-metric-green/10 px-3 py-2 text-sm text-text-primary">
                          🏆 Fastest: <strong>{hi.fastest.pace}</strong> — {hi.fastest.name}{" "}
                          ({hi.fastest.date})
                        </div>
                      )}
                      {hi.slowest && (
                        <div className="rounded-lg border border-metric-amber/40 bg-metric-amber/10 px-3 py-2 text-sm text-text-primary">
                          🐢 Slowest: <strong>{hi.slowest.pace}</strong> — {hi.slowest.name}{" "}
                          ({hi.slowest.date})
                        </div>
                      )}
                    </div>
                  )}
                </>
              ))}

            {tab === "hr" &&
              (hrIdx.length === 0 ? (
                <EmptyState message="No HR data (no heart rate monitor used?)" />
              ) : (
                <PlotlyChart
                  height={300}
                  data={[
                    {
                      type: "scatter",
                      mode: "lines+markers",
                      x: hrIdx.map((i) => dates[i]),
                      y: hrIdx.map((i) => series[i].avg_hr as number),
                      name: "Ø HR",
                      line: { color: C_RED, width: 2 },
                      marker: { size: 5 },
                      text: hrIdx.map((i) => names[i]),
                      hovertemplate: "%{text}<br>%{y:.0f} bpm<extra></extra>",
                    } as Data,
                  ]}
                  layout={
                    {
                      yaxis: { title: { text: "bpm" } },
                      ...(avgHr ? hline(avgHr, `avg ${avgHr.toFixed(0)} bpm`) : {}),
                    } as Partial<Layout>
                  }
                />
              ))}

            {tab === "dist" && (
              <PlotlyChart
                height={300}
                data={[
                  {
                    type: "bar",
                    x: distIdx.map((i) => dates[i]),
                    y: distIdx.map((i) => series[i].distance_km as number),
                    name: "Distance",
                    marker: { color: C_BLUE },
                    text: distIdx.map((i) => names[i]),
                    hovertemplate: "%{text}<br>%{y:.1f} km<extra></extra>",
                  } as Data,
                ]}
                layout={
                  {
                    yaxis: { title: { text: "km" } },
                    ...(avgD ? hline(avgD, `avg ${avgD.toFixed(1)} km`) : {}),
                  } as Partial<Layout>
                }
              />
            )}

            {tab === "elev" &&
              (elevIdx.length === 0 ? (
                <EmptyState message="No elevation data available." />
              ) : (
                <PlotlyChart
                  height={300}
                  data={[
                    {
                      type: "bar",
                      x: elevIdx.map((i) => dates[i]),
                      y: elevIdx.map((i) => series[i].elevation_per_km as number),
                      name: "m/km",
                      marker: { color: C_GREEN },
                      text: elevIdx.map((i) => names[i]),
                      hovertemplate: "%{text}<br>%{y:.1f} m/km<extra></extra>",
                    } as Data,
                  ]}
                  layout={
                    {
                      yaxis: { title: { text: "m/km" } },
                      ...(avgE ? hline(avgE, `avg ${avgE.toFixed(1)} m/km`) : {}),
                    } as Partial<Layout>
                  }
                />
              ))}
          </>
        );
      })()}
    </>
  );
}

// ════════════════════════════════════════════════════════════════════════════════
// Section 3 — Activity vs. Personal Baseline
// ════════════════════════════════════════════════════════════════════════════════

function actLabel(a: ActivitySummary): string {
  const date = (a.date || a.start_date || "").slice(0, 10);
  const dist = a.distance_km ?? Math.round(((a.distance ?? 0) / 1000) * 10) / 10;
  return `${a.name ?? "?"}  ·  ${date}  ·  ${dist} km`;
}

const METRIC_LABELS: Record<string, { label: string; unit: string }> = {
  distance_km: { label: "📏 Distance", unit: "km" },
  elevation_m: { label: "⛰️ Elevation", unit: "m" },
  elevation_per_km: { label: "📐 Elevation/km", unit: "m/km" },
  pace_min_per_km: { label: "⏱️ Pace", unit: "min/km" },
  avg_hr_bpm: { label: "❤️ Heart rate", unit: "bpm" },
};

const ASSESSMENT_ICONS: Record<string, string> = {
  "one of your hardest": "🔥",
  "harder than usual": "💪",
  typical: "👌",
  "easier than usual": "😌",
  "one of your easiest": "🛋️",
};
const ASSESSMENT_COLORS: Record<string, string> = {
  "one of your hardest": C_RED,
  "harder than usual": C_ORANGE,
  typical: TEXT_MUTED,
  "easier than usual": C_GREEN,
  "one of your easiest": C_GREEN_DARK,
};
const ASSESSMENT_LABELS: Record<string, string> = {
  "one of your hardest": "One of your hardest",
  "harder than usual": "Harder than usual",
  typical: "Typical effort",
  "easier than usual": "Easier than usual",
  "one of your easiest": "One of your easiest",
};

function ComparisonSection({ refreshVersion }: { refreshVersion: number }) {
  const [search, setSearch] = useState("");
  const [baselineN, setBaselineN] = useState(30);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [result, setResult] = useState<Comparison | null>(null);
  const [comparing, setComparing] = useState(false);

  // Load up to 500 recent activities for client-side search (mirrors _load_all_activities).
  const { data: allActs } = useQuery({
    queryKey: ["analysis", "all_activities", refreshVersion],
    queryFn: async () => {
      const raw = await callTool<ActivitiesResult | ActivitySummary[]>(
        "strava__get_activities",
        { limit: 500 },
      );
      if (Array.isArray(raw)) return raw;
      if (raw && !("error" in raw && raw.error)) return raw.activities ?? [];
      return [] as ActivitySummary[];
    },
  });

  const keyword = search.trim().toLowerCase();
  const matches = useMemo(() => {
    if (!keyword) return [];
    return (allActs ?? []).filter((a) => (a.name ?? "").toLowerCase().includes(keyword));
  }, [allActs, keyword]);

  // Resolve the active selection: single match auto-selects; multiple uses the dropdown.
  const effectiveId =
    matches.length === 1 ? Number(matches[0].id) : selectedId ?? null;

  async function handleCompare() {
    if (effectiveId == null) return;
    setComparing(true);
    try {
      const r = await callTool<Comparison>("strava__compare_activity_to_baseline", {
        activity_id: effectiveId,
        baseline_count: baselineN,
      });
      setResult(r);
    } catch (e) {
      setResult({ error: e instanceof Error ? e.message : String(e) });
    } finally {
      setComparing(false);
    }
  }

  return (
    <>
      <p className="mb-3 text-sm text-text-muted">
        Pick any activity and compare it against your recent history of the same sport. Was
        today's run actually hard — or just felt that way?
      </p>

      <HowTo title="💡 How to use this">
        <p>
          <strong>Search</strong> for an activity by name (partial match works — try 'run',
          'trail', 'wandern'). If multiple results come up, pick one from the drop-down. Then
          hit <strong>Compare</strong>.
        </p>
        <p>
          <strong>Baseline size</strong> — how many of your most recent same-sport activities
          are used as the reference. 30 is a good default.
        </p>
        <p>
          <strong>Percentile bars</strong> — each metric is ranked against the baseline. A bar
          at 80% means this activity was harder than 80% of those baseline runs.
        </p>
        <p>
          🟢 0–25% easier than usual · ⚪ 25–50% typical · 🟠 50–75% harder than usual · 🔴
          75–100% one of your hardest.
        </p>
      </HowTo>

      <div className="mb-3 flex flex-wrap items-end gap-4">
        <div className="flex-1 min-w-[240px]">
          <label htmlFor="cmp-search" className="fd-label mb-1 block">
            Search activity
          </label>
          <input
            id="cmp-search"
            className="fd-input w-full"
            placeholder="e.g. 'wandern', 'morning run', 'trail'"
            value={search}
            onChange={(e) => {
              setSearch(e.target.value);
              setSelectedId(null);
              setResult(null);
            }}
          />
        </div>
        <div>
          <label htmlFor="cmp-baseline" className="fd-label mb-1 block">
            Baseline size
          </label>
          <input
            id="cmp-baseline"
            type="number"
            className="fd-input w-28"
            min={5}
            max={100}
            value={baselineN}
            onChange={(e) =>
              setBaselineN(Math.min(100, Math.max(5, Number(e.target.value) || 30)))
            }
          />
        </div>
      </div>

      {!keyword && (
        <div className="text-xs text-text-muted">
          Enter an activity name to search, then click <strong>Compare</strong>.
        </div>
      )}

      {keyword && matches.length === 0 && (
        <div className="text-xs text-text-muted">No activities found matching this search.</div>
      )}

      {keyword && matches.length === 1 && (
        <div className="mb-3 text-xs text-text-muted">Found: {actLabel(matches[0])}</div>
      )}

      {keyword && matches.length > 1 && (
        <div className="mb-3">
          <label htmlFor="cmp-select" className="fd-label mb-1 block">
            {matches.length} matching activities — select one:
          </label>
          <select
            id="cmp-select"
            className="fd-input w-full max-w-xl"
            value={selectedId ?? ""}
            onChange={(e) => {
              setSelectedId(Number(e.target.value));
              setResult(null);
            }}
          >
            <option value="" disabled>
              Select an activity…
            </option>
            {matches.map((a) => (
              <option key={a.id} value={a.id}>
                {actLabel(a)}
              </option>
            ))}
          </select>
        </div>
      )}

      {keyword && matches.length > 0 && (
        <button
          type="button"
          className="fd-btn-primary"
          disabled={effectiveId == null || comparing}
          onClick={handleCompare}
        >
          {comparing ? "Comparing…" : "Compare"}
        </button>
      )}

      {result?.error && (
        <div className="mt-4">
          <ErrorBox
            message={toolErrorMessage(result.error, "compare_activity_to_baseline")}
          />
        </div>
      )}

      {result && !result.error && (() => {
        const act = result.activity ?? ({} as NonNullable<Comparison["activity"]>);
        const assessment = result.assessment ?? "";
        const overallPct = result.overall_difficulty_percentile;
        const comparisons = result.comparisons ?? {};
        const nBase = result.baseline_activity_count ?? "?";

        const icon = ASSESSMENT_ICONS[assessment] ?? "📊";
        const color = ASSESSMENT_COLORS[assessment] ?? TEXT_MUTED;
        const label = ASSESSMENT_LABELS[assessment] ?? assessment;
        const sport = act.sport_type ?? "";

        const headerParts: string[] = [
          `${(act.distance_km ?? 0).toFixed(1)} km`,
          `${(act.elevation_m ?? 0).toFixed(0)} m elevation`,
        ];
        if (act.pace_display) headerParts.push(`${act.pace_display} /km`);
        if (act.avg_hr) headerParts.push(`❤️ ${act.avg_hr.toFixed(0)} bpm`);

        return (
          <div className="mt-4">
            <div className="flex items-center justify-between gap-2 text-sm text-text-primary">
              <span>
                <strong>{act.name ?? ""}</strong>
                {act.date ? ` · ${act.date}` : ""} · {headerParts.join(" · ")}
              </span>
              <ExplainButton
                title={`How "${act.name ?? "this activity"}" compares to my baseline`}
                data={{
                  activity: act, assessment,
                  overall_difficulty_percentile: overallPct,
                  baseline_activity_count: nBase, comparisons,
                }}
              />
            </div>

            {overallPct != null && (
              <div
                className="my-3 rounded-r-md py-2.5 pl-4 pr-4"
                style={{ background: `${color}18`, borderLeft: `3px solid ${color}` }}
              >
                <span className="text-2xl align-middle">{icon}</span>{" "}
                <strong style={{ color }} className="align-middle text-[1.05rem]">
                  {label}
                </strong>
                <span className="align-middle text-[0.85rem] text-text-muted">
                  {" "}
                  — harder than{" "}
                  <strong className="text-text-primary">{overallPct}%</strong> of your last{" "}
                  {nBase} {sport} activities
                </span>
              </div>
            )}

            {Object.keys(comparisons).length > 0 && (
              <>
                <div className="mb-1 text-xs text-text-muted">
                  Difficulty percentile per metric (how many of your baseline activities were
                  easier):
                </div>
                {Object.entries(comparisons).map(([key, cdata]) => {
                  if (!cdata) return null;
                  const meta = METRIC_LABELS[key] ?? { label: key, unit: "" };
                  return (
                    <PctBar
                      key={key}
                      pct={cdata.difficulty_percentile ?? 0}
                      value={(cdata.target ?? 0).toFixed(1)}
                      mean={(cdata.baseline_mean ?? 0).toFixed(1)}
                      unit={meta.unit}
                      label={meta.label}
                    />
                  );
                })}
              </>
            )}
          </div>
        );
      })()}
    </>
  );
}

// ════════════════════════════════════════════════════════════════════════════════
// Entry point
// ════════════════════════════════════════════════════════════════════════════════

export function Analysis() {
  const refreshVersion = useUiStore((s) => s.refreshVersion);

  // Shared period + activities/athlete queries feed overview, volume, map & stats.
  const [period, setPeriod] = useState<Period>("30 days");
  const loadDays = PERIOD_DAYS[period];

  const [open, setOpen] = useState({
    overview: true,
    plan: false,
    map: false,
    volume: false,
    stats: false,
    load: false,
    trend: false,
    cmp: false,
  });

  // Same query key as the Dashboard signals row → no duplicate fetch.
  const activitiesQ = useQuery({
    queryKey: ["activities", loadDays, refreshVersion],
    queryFn: () => {
      const args: Record<string, unknown> =
        loadDays > 0
          ? {
              limit: Math.min(loadDays * 3, 400),
              start_date: new Date(Date.now() - loadDays * 86400000)
                .toISOString()
                .slice(0, 10),
            }
          : { limit: 500 };
      return callTool<StravaActivitiesResult>("strava__get_activities", args);
    },
  });

  const athleteQ = useQuery({
    queryKey: ["athlete", refreshVersion],
    queryFn: () => callTool<AthleteResult>("strava__get_athlete_profile", {}),
  });

  // Memoised so the `?? []` fallback keeps its identity across renders.
  const allActivities = useMemo(() => activitiesQ.data?.activities ?? [], [activitiesQ.data]);
  const actError = activitiesQ.data?.error;

  // Period cutoff (search lived on Dashboard; not relocated). The cutoff is
  // relative to "now" on purpose — a period filter that froze at mount would
  // drift over a long-lived tab.
  const activities = useMemo(() => {
    if (loadDays <= 0) return allActivities;
    // Reading the clock here is the point: "last 30 days" has to mean 30 days
    // from now, not from whenever the tab happened to be opened.
    // eslint-disable-next-line react-hooks/purity
    const cutoff = new Date(Date.now() - loadDays * 86400000).toISOString().slice(0, 10);
    return allActivities.filter((a) => dayStr(a) >= cutoff);
  }, [allActivities, loadDays]);

  const stats = athleteQ.data?.official_stats ?? {};

  return (
    <div>
      <PageHeader
        title="Analysis"
        subtitle="Dig deeper into your training data — activity map, training volume, load, trends, and how any single workout stacks up against your personal history."
      />

      {actError && (
        <div className="mb-4">
          <ErrorBox message={`Strava activities error: ${actError}`} />
        </div>
      )}

      <CollapsibleSection
        title="📊 Overview"
        open={open.overview}
        onToggle={() => setOpen((o) => ({ ...o, overview: !o.overview }))}
      >
        {activitiesQ.isLoading ? (
          <Spinner label="Loading Strava data…" />
        ) : (
          <AnalysisOverview activities={activities} period={period} onPeriodChange={setPeriod} />
        )}
      </CollapsibleSection>

      <CollapsibleSection
        title="🎯 Plan vs. Actual"
        open={open.plan}
        onToggle={() => setOpen((o) => ({ ...o, plan: !o.plan }))}
      >
        <PlanVsActualSection />
      </CollapsibleSection>

      <CollapsibleSection
        title="🗺️ Activity Map"
        open={open.map}
        onToggle={() => setOpen((o) => ({ ...o, map: !o.map }))}
      >
        {activitiesQ.isLoading ? (
          <Spinner label="Loading activities…" />
        ) : (
          <ActivityMapSection activities={activities} />
        )}
      </CollapsibleSection>

      <CollapsibleSection
        title="📉 Training Volume"
        open={open.volume}
        onToggle={() => setOpen((o) => ({ ...o, volume: !o.volume }))}
      >
        {activitiesQ.isLoading ? (
          <Spinner label="Loading activities…" />
        ) : activities.length === 0 ? (
          <EmptyState message="No activities in this period." />
        ) : (
          <TrainingVolumeSection activities={activities} periodDays={loadDays} />
        )}
      </CollapsibleSection>

      <CollapsibleSection
        title="🏅 Official Strava Stats"
        open={open.stats}
        onToggle={() => setOpen((o) => ({ ...o, stats: !o.stats }))}
      >
        {athleteQ.isLoading ? (
          <Spinner label="Loading stats…" />
        ) : (
          <OfficialStatsSection stats={stats} />
        )}
      </CollapsibleSection>

      <CollapsibleSection
        title="🏋️ Training Load"
        open={open.load}
        onToggle={() => setOpen((o) => ({ ...o, load: !o.load }))}
      >
        <TrainingLoadSection refreshVersion={refreshVersion} periodDays={loadDays} />
      </CollapsibleSection>

      <CollapsibleSection
        title="📈 Performance Trend"
        open={open.trend}
        onToggle={() => setOpen((o) => ({ ...o, trend: !o.trend }))}
      >
        <PerformanceTrendSection
          refreshVersion={refreshVersion}
          activities={activities}
          period={period}
          activitiesReady={activitiesQ.isSuccess && !actError}
        />
      </CollapsibleSection>

      <CollapsibleSection
        title="🔍 Activity vs. Personal Baseline"
        open={open.cmp}
        onToggle={() => setOpen((o) => ({ ...o, cmp: !o.cmp }))}
      >
        <ComparisonSection refreshVersion={refreshVersion} />
      </CollapsibleSection>
    </div>
  );
}
