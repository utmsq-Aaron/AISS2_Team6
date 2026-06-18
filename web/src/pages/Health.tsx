import { useState } from "react";
import type { ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import type { Data, Layout } from "plotly.js";

import { MetricCard } from "../components/MetricCard";
import { PageHeader } from "../components/PageHeader";
import { PeriodSelector } from "../components/PeriodSelector";
import { PlotlyChart } from "../components/PlotlyChart";
import { SectionLabel } from "../components/Card";
import { Spinner, ErrorBox, EmptyState } from "../components/Spinner";
import { callTool } from "../lib/api";
import { useUiStore } from "../store/uiStore";
import {
  ACCENT,
  C_AMBER,
  C_CYAN,
  C_GREEN,
  C_ROSE,
  TEXT_MUTED,
  TEXT_PRIMARY,
  BORDER,
  BG_CARD,
} from "../theme/tokens";

// ── Sleep stage colours (health.py) ────────────────────────────────────────────
const C_SLEEP_DEEP = "#1E40AF";
const C_SLEEP_REM = "#7C3AED";
const C_SLEEP_LIGHT = "#60A5FA";
const C_SLEEP_AWAKE = "#ED79D5";

// ── Sleep quality thresholds (health.py) ────────────────────────────────────────
const SLEEP_TOTAL_GOOD_H = 7;
const SLEEP_TOTAL_GREAT_H = 8;
const SLEEP_DEEP_GOOD_PCT = 13;
const SLEEP_REM_GOOD_PCT = 15;

// ── Activity goals (health.py) ──────────────────────────────────────────────────
const DAILY_STEPS_GOAL = 10_000;
const WEEKLY_INTENSITY_GOAL_MIN = 150;

// ── Period options (health.py _PERIODS) ─────────────────────────────────────────
const PERIODS = {
  "7 days": 7,
  "14 days": 14,
  "30 days": 30,
  "3 months": 90,
  "6 months": 180,
  "1 year": 365,
  "2 years": 730,
  "3 years": 1095,
} as const;
type PeriodKey = keyof typeof PERIODS;
const PERIOD_KEYS = Object.keys(PERIODS) as PeriodKey[];

// ── Tool result shapes (confirmed via live FastAPI) ─────────────────────────────
interface DailyHealth {
  date?: string;
  steps?: number | null;
  distance_m?: number | null;
  active_calories?: number | null;
  total_calories?: number | null;
  resting_hr?: number | null;
  min_hr?: number | null;
  max_hr?: number | null;
  avg_stress?: number | null;
  max_stress?: number | null;
  stress_qualifier?: string | null;
  intensity_minutes?: number | null;
  moderate_intensity_min?: number | null;
  vigorous_intensity_min?: number | null;
  floors_climbed?: number | null;
  body_battery_now?: number | null;
  body_battery_max?: number | null;
  body_battery_min?: number | null;
  error?: string;
}

interface RacePredictions {
  "5k"?: string | null;
  "10k"?: string | null;
  half_marathon?: string | null;
  marathon?: string | null;
}
interface TrainingMetrics {
  date?: string;
  training_status?: string | null;
  training_load_7d?: number | null;
  training_load_28d?: number | null;
  vo2max_running?: number | null;
  vo2max_cycling?: number | null;
  training_readiness_score?: number | null;
  race_predictions?: RacePredictions | null;
  error?: string;
}

interface HrvStatus {
  date?: string;
  last_night_hrv?: number | null;
  baseline_low?: number | null;
  baseline_balanced_low?: number | null;
  baseline_balanced_high?: number | null;
  status?: string | null;
  feedback?: string | null;
  error?: string;
}

interface TrendRow {
  date: string;
  resting_hr?: number | null;
  max_hr?: number | null;
  steps?: number | null;
  avg_stress?: number | null;
  intensity_min?: number | null;
  active_cal?: number | null;
  total_cal?: number | null;
  total_sleep_h?: number | null;
  deep_h?: number | null;
  light_h?: number | null;
  rem_h?: number | null;
  awake_h?: number | null;
  sleep_score?: number | null;
  body_battery_high?: number | null;
  body_battery_low?: number | null;
}
interface WellnessTrends {
  days?: number;
  summary?: Record<string, number | null>;
  trend?: TrendRow[];
  error?: string;
}

// ── Helpers ──────────────────────────────────────────────────────────────────
const num = (v: unknown): number => (typeof v === "number" && isFinite(v) ? v : 0);
const hasValues = (rows: TrendRow[], key: keyof TrendRow): boolean =>
  rows.some((r) => r[key] != null);
const titleCase = (s: string): string =>
  s
    .replace(/_/g, " ")
    .replace(/\w\S*/g, (w) => w.charAt(0).toUpperCase() + w.slice(1).toLowerCase());

function Divider() {
  return <div className="my-6 border-t border-border" />;
}
function Caption({ children }: { children: ReactNode }) {
  return <p className="text-xs text-text-muted">{children}</p>;
}

// ── Rich sleep hover (ported from health.py _sleep_hover) ────────────────────────
function sleepHover(row: TrendRow): string {
  const deepH = num(row.deep_h);
  const lightH = num(row.light_h);
  const remH = num(row.rem_h);
  const awakeH = num(row.awake_h);
  const score = row.sleep_score;
  const total = deepH + lightH + remH + awakeH;
  if (total <= 0) return "<b>No sleep data recorded</b>";

  const deepPct = (deepH / total) * 100;
  const remPct = (remH / total) * 100;
  const lightPct = (lightH / total) * 100;
  const awakePct = (awakeH / total) * 100;

  const durLabel = (h: number): [string, string] => {
    if (h >= SLEEP_TOTAL_GREAT_H) return ["Great", "#10b981"];
    if (h >= SLEEP_TOTAL_GOOD_H) return ["Good", "#60A5FA"];
    if (h >= 6) return ["A bit short", "#f59e0b"];
    return ["Too short", "#FB7185"];
  };
  const deepLabel = (pct: number): [string, string] => {
    if (pct >= 20) return ["Excellent", "#10b981"];
    if (pct >= SLEEP_DEEP_GOOD_PCT) return ["Good", "#60A5FA"];
    if (pct >= 7) return ["Low", "#f59e0b"];
    return ["Very low", "#FB7185"];
  };
  const remLabel = (pct: number): [string, string] => {
    if (pct >= 22) return ["Excellent", "#10b981"];
    if (pct >= SLEEP_REM_GOOD_PCT) return ["Good", "#60A5FA"];
    if (pct >= 9) return ["Low", "#f59e0b"];
    return ["Very low", "#FB7185"];
  };
  const awakeLabel = (h: number): [string, string] => {
    if (h <= 0.25) return ["Minimal", "#10b981"];
    if (h <= 0.5) return ["Normal", "#60A5FA"];
    if (h <= 1.0) return ["Elevated", "#f59e0b"];
    return ["Disruptive", "#FB7185"];
  };
  const tag = (label: string, color: string) =>
    `<span style="color:${color};font-weight:600">${label}</span>`;
  const rowLine = (
    dotColor: string,
    label: string,
    hours: number,
    pct: number | null,
    tagHtml = "",
  ) => {
    const dot = `<span style="color:${dotColor}">●</span>`;
    const pctStr = pct != null ? ` (${pct.toFixed(0)}%)` : "";
    return `${dot} <b>${label}</b>  ${hours.toFixed(1)} h${pctStr}  ${tagHtml}`;
  };

  const [durLbl, durCol] = durLabel(total);
  const [deepLbl, deepCol] = deepLabel(deepPct);
  const [remLbl, remCol] = remLabel(remPct);
  const [awakeLbl, awakeCol] = awakeLabel(awakeH);

  const deepOk = deepPct >= SLEEP_DEEP_GOOD_PCT;
  const remOk = remPct >= SLEEP_REM_GOOD_PCT;
  const awakeOk = awakeH <= 0.5;
  const durOk = total >= SLEEP_TOTAL_GOOD_H;

  let insight: string;
  if (!durOk && total < 5) {
    insight =
      "Very short night — your body barely had time to complete full sleep cycles. Even one extra hour makes a noticeable difference.";
  } else if (deepPct >= 20 && remOk) {
    insight =
      "You got excellent deep and REM sleep tonight — your body repaired itself and your brain processed the day. This is what recovery looks like.";
  } else if (deepPct >= 20) {
    insight =
      "Strong deep sleep — your immune system and muscles got a solid repair session. A little more REM would round things out for mental recovery too.";
  } else if (remPct >= 22 && !deepOk) {
    insight =
      "Good brain recovery tonight, but your body missed out on enough deep sleep. Deep sleep is where physical repair and immune strengthening happen.";
  } else if (!deepOk && !remOk) {
    insight =
      "Both deep and REM sleep were on the lower side. A consistent wind-down routine — no screens, dim lights, cool room — can push you into deeper stages sooner.";
  } else if (!awakeOk) {
    insight =
      "Frequent wake-ups broke your sleep into fragments. Continuity matters — each interruption cuts short a recovery cycle. Check room temperature, hydration, and stress levels.";
  } else if (deepOk && remOk) {
    insight =
      "Solid sleep overall — good balance of physical and mental recovery. Keep the same sleep schedule to lock in these results.";
  } else {
    insight =
      "Decent night. Deep sleep supports your immune system and muscles; REM looks after memory and mood. Aim to improve whichever is lower.";
  }

  const sep = "─".repeat(30);
  const header = score ? `<b>Sleep Score: ${Math.round(score)}</b>` : "<b>Sleep breakdown</b>";
  const lines = [
    header,
    sep,
    `<span style="color:#9BA3C8">  Total    ${total.toFixed(1)} h  </span>${tag(durLbl, durCol)}`,
    rowLine(C_SLEEP_DEEP, "Deep ", deepH, deepPct, tag(deepLbl, deepCol)),
    rowLine(C_SLEEP_REM, "REM  ", remH, remPct, tag(remLbl, remCol)),
    rowLine(C_SLEEP_LIGHT, "Light", lightH, lightPct),
    rowLine(C_SLEEP_AWAKE, "Awake", awakeH, awakePct, tag(awakeLbl, awakeCol)),
    sep,
    `<i>${insight}</i>`,
  ];
  return lines.join("<br>");
}

// ── Chart builders (ported from health.py) ──────────────────────────────────────
interface ChartSpec {
  data: Data[];
  layout: Partial<Layout>;
}

function sleepStagesChart(rows: TrendRow[]): ChartSpec | null {
  const stageKeys: (keyof TrendRow)[] = ["deep_h", "light_h", "rem_h", "awake_h"];
  if (!stageKeys.some((k) => hasValues(rows, k))) return null;
  const df = rows.filter((r) => r.total_sleep_h != null);
  if (df.length === 0) return null;

  const dates = df.map((r) => r.date);
  const hover = df.map(sleepHover);
  const hasScore = df.some((r) => r.sleep_score != null);

  const bar = (key: keyof TrendRow, color: string, name: string): Data => ({
    type: "bar",
    x: dates,
    y: df.map((r) => num(r[key])),
    name,
    marker: { color, line: { width: 0 } },
    customdata: hover,
    hovertemplate: "<b>%{x}</b><br>%{customdata}<extra></extra>",
  });

  const data: Data[] = [
    bar("deep_h", C_SLEEP_DEEP, "Deep"),
    bar("rem_h", C_SLEEP_REM, "REM"),
    bar("light_h", C_SLEEP_LIGHT, "Light"),
    bar("awake_h", C_SLEEP_AWAKE, "Awake"),
  ];

  if (hasScore) {
    data.push({
      type: "scatter",
      x: dates,
      y: df.map((r) => r.sleep_score ?? null),
      name: "Score",
      mode: "lines+markers",
      yaxis: "y2",
      line: { color: C_AMBER, width: 2, shape: "spline" },
      marker: { size: 5, color: C_AMBER },
      hovertemplate: "<b>%{x}</b><br>Sleep Score: %{y}<extra></extra>",
    });
  }

  const layout: Partial<Layout> = {
    barmode: "stack",
    hovermode: "closest",
    yaxis: { ticksuffix: " h" },
    shapes: [
      {
        type: "line",
        xref: "paper",
        x0: 0,
        x1: 1,
        yref: "y",
        y0: 8,
        y1: 8,
        line: { dash: "dot", color: TEXT_MUTED, width: 1 },
      },
    ],
    annotations: [
      {
        xref: "paper",
        x: 0,
        yref: "y",
        y: 8,
        text: "8 h",
        showarrow: false,
        xanchor: "left",
        yanchor: "bottom",
        font: { color: TEXT_MUTED, size: 10 },
      },
    ],
  };
  if (hasScore) {
    layout.yaxis2 = {
      range: [0, 100],
      title: { text: "Score" },
      overlaying: "y",
      side: "right",
      showgrid: false,
      color: TEXT_MUTED,
      tickfont: { size: 10, color: TEXT_MUTED },
    };
  }
  return { data, layout };
}

function bodyBatteryChart(rows: TrendRow[]): ChartSpec | null {
  if (!hasValues(rows, "body_battery_high")) return null;
  const df = rows.filter((r) => r.body_battery_high != null);
  if (df.length === 0) return null;
  const dates = df.map((r) => r.date);

  const data: Data[] = [
    {
      type: "scatter",
      x: dates,
      y: df.map((r) => r.body_battery_high ?? null),
      name: "Peak",
      mode: "lines",
      line: { color: C_GREEN, width: 2.5, shape: "spline" },
      hovertemplate: "<b>%{x}</b><br>Peak: %{y}%<extra></extra>",
    },
  ];
  if (df.some((r) => r.body_battery_low != null)) {
    data.push({
      type: "scatter",
      x: dates,
      y: df.map((r) => r.body_battery_low ?? null),
      name: "Low",
      mode: "lines",
      line: { color: C_ROSE, width: 1.5, shape: "spline" },
      fill: "tonexty",
      fillcolor: "rgba(34,197,94,0.12)",
      hovertemplate: "<b>%{x}</b><br>Low: %{y}%<extra></extra>",
    });
  }
  return { data, layout: { yaxis: { range: [0, 100], ticksuffix: "%" }, hovermode: "x unified" } };
}

function hrChart(rows: TrendRow[]): ChartSpec | null {
  const hasResting = hasValues(rows, "resting_hr");
  const hasMax = hasValues(rows, "max_hr");
  if (!hasResting && !hasMax) return null;

  const data: Data[] = [];
  const shapes: NonNullable<Layout["shapes"]> = [];
  const annotations: NonNullable<Layout["annotations"]> = [];

  if (hasResting) {
    const rest = rows.filter((r) => r.resting_hr != null);
    data.push({
      type: "scatter",
      x: rest.map((r) => r.date),
      y: rest.map((r) => r.resting_hr ?? null),
      name: "Resting HR",
      mode: "lines+markers",
      line: { color: C_ROSE, width: 2.5, shape: "spline" },
      fill: "tozeroy",
      fillcolor: "rgba(251,113,133,0.10)",
      marker: { size: 5, color: C_ROSE, line: { color: BG_CARD, width: 1.5 } },
      hovertemplate: "<b>%{x}</b><br>Resting HR: %{y} bpm<extra></extra>",
    });
    const vals = rest.map((r) => num(r.resting_hr));
    const avg = vals.reduce((a, b) => a + b, 0) / vals.length;
    shapes.push({
      type: "line",
      xref: "paper",
      x0: 0,
      x1: 1,
      yref: "y",
      y0: avg,
      y1: avg,
      line: { dash: "dot", color: TEXT_MUTED, width: 1 },
    });
    annotations.push({
      xref: "paper",
      x: 1,
      yref: "y",
      y: avg,
      text: `avg ${avg.toFixed(0)} bpm`,
      showarrow: false,
      xanchor: "right",
      yanchor: "bottom",
      font: { color: TEXT_MUTED, size: 10 },
    });
  }
  if (hasMax) {
    const mx = rows.filter((r) => r.max_hr != null);
    data.push({
      type: "scatter",
      x: mx.map((r) => r.date),
      y: mx.map((r) => r.max_hr ?? null),
      name: "High HR",
      mode: "lines+markers",
      line: { color: C_CYAN, width: 2.0, shape: "spline" },
      marker: { size: 4, color: C_CYAN, line: { color: BG_CARD, width: 1.2 } },
      hovertemplate: "<b>%{x}</b><br>High HR: %{y}<extra></extra>",
    });
  }
  return { data, layout: { yaxis: { ticksuffix: " bpm" }, shapes, annotations } };
}

function stepsChart(rows: TrendRow[]): ChartSpec | null {
  if (!hasValues(rows, "steps")) return null;
  const df = rows.filter((r) => r.steps != null);
  if (df.length === 0) return null;
  const colors = df.map((r) => (num(r.steps) >= DAILY_STEPS_GOAL ? C_GREEN : C_CYAN));
  return {
    data: [
      {
        type: "bar",
        x: df.map((r) => r.date),
        y: df.map((r) => r.steps ?? null),
        marker: { color: colors, line: { width: 0 } },
        hovertemplate: "<b>%{x}</b><br>Steps: %{y:,}<extra></extra>",
      },
    ],
    layout: {
      shapes: [
        {
          type: "line",
          xref: "paper",
          x0: 0,
          x1: 1,
          yref: "y",
          y0: DAILY_STEPS_GOAL,
          y1: DAILY_STEPS_GOAL,
          line: { dash: "dot", color: TEXT_MUTED, width: 1 },
        },
      ],
      annotations: [
        {
          xref: "paper",
          x: 1,
          yref: "y",
          y: DAILY_STEPS_GOAL,
          text: `${DAILY_STEPS_GOAL.toLocaleString()} goal`,
          showarrow: false,
          xanchor: "right",
          yanchor: "bottom",
          font: { color: TEXT_MUTED, size: 10 },
        },
      ],
    },
  };
}

function stressChart(rows: TrendRow[]): ChartSpec | null {
  if (!hasValues(rows, "avg_stress")) return null;
  const df = rows.filter((r) => r.avg_stress != null);
  if (df.length === 0) return null;
  const stressColor = (v: number): string => {
    if (v < 26) return "rgba(34,197,94,0.7)";
    if (v < 51) return "rgba(252,211,77,0.7)";
    if (v < 76) return "rgba(251,113,133,0.7)";
    return "rgba(220,38,38,0.7)";
  };
  const colors = df.map((r) => stressColor(num(r.avg_stress)));
  const zone = (
    y0: number,
    y1: number,
    color: string,
  ): NonNullable<Layout["shapes"]>[number] => ({
    type: "rect",
    xref: "paper",
    x0: 0,
    x1: 1,
    yref: "y",
    y0,
    y1,
    fillcolor: color,
    line: { width: 0 },
    layer: "below",
  });
  return {
    data: [
      {
        type: "bar",
        x: df.map((r) => r.date),
        y: df.map((r) => r.avg_stress ?? null),
        marker: { color: colors, line: { width: 0 } },
        hovertemplate: "<b>%{x}</b><br>Stress: %{y:.0f}<extra></extra>",
      },
    ],
    layout: {
      yaxis: { range: [0, 100] },
      shapes: [
        zone(0, 25, "rgba(34,197,94,0.04)"),
        zone(25, 50, "rgba(252,211,77,0.04)"),
        zone(50, 75, "rgba(251,113,133,0.04)"),
        zone(75, 100, "rgba(220,38,38,0.04)"),
      ],
    },
  };
}

function intensityChart(rows: TrendRow[]): ChartSpec | null {
  if (!hasValues(rows, "intensity_min")) return null;
  // Fill missing days with 0 (mirrors health.py fillna(0)).
  return {
    data: [
      {
        type: "bar",
        x: rows.map((r) => r.date),
        y: rows.map((r) => num(r.intensity_min)),
        name: "Total",
        marker: { color: C_CYAN, line: { width: 0 } },
        hovertemplate: "<b>%{x}</b><br>Intensity: %{y:.0f} min<extra></extra>",
      },
    ],
    layout: {
      yaxis: { ticksuffix: " min" },
      shapes: [
        {
          type: "line",
          xref: "paper",
          x0: 0,
          x1: 1,
          yref: "y",
          y0: WEEKLY_INTENSITY_GOAL_MIN / 7,
          y1: WEEKLY_INTENSITY_GOAL_MIN / 7,
          line: { dash: "dot", color: TEXT_MUTED, width: 1 },
        },
      ],
      annotations: [
        {
          xref: "paper",
          x: 1,
          yref: "y",
          y: WEEKLY_INTENSITY_GOAL_MIN / 7,
          text: `~${Math.floor(WEEKLY_INTENSITY_GOAL_MIN / 7)} min/day goal`,
          showarrow: false,
          xanchor: "right",
          yanchor: "bottom",
          font: { color: TEXT_MUTED, size: 10 },
        },
      ],
    },
  };
}

function caloriesChart(rows: TrendRow[]): ChartSpec | null {
  const hasTotal = hasValues(rows, "total_cal");
  const hasActive = hasValues(rows, "active_cal");
  if (!hasTotal && !hasActive) return null;
  const data: Data[] = [];
  if (hasTotal) {
    const t = rows.filter((r) => r.total_cal != null);
    data.push({
      type: "bar",
      x: t.map((r) => r.date),
      y: t.map((r) => r.total_cal ?? null),
      name: "Total",
      marker: { color: C_AMBER, line: { width: 0 } },
      hovertemplate: "<b>%{x}</b><br>Total: %{y:,} kcal<extra></extra>",
    });
  }
  if (hasActive) {
    const a = rows.filter((r) => r.active_cal != null);
    data.push({
      type: "bar",
      x: a.map((r) => r.date),
      y: a.map((r) => r.active_cal ?? null),
      name: "Active",
      marker: { color: ACCENT, line: { width: 0 } },
      hovertemplate: "<b>%{x}</b><br>Active: %{y:,} kcal<extra></extra>",
    });
  }
  return { data, layout: { barmode: "group", yaxis: { ticksuffix: " kcal" } } };
}

function hrvGauge(hrv: HrvStatus): ChartSpec | null {
  const val = hrv.last_night_hrv;
  if (!val) return null;
  const lo = hrv.baseline_balanced_low ?? 0;
  const hi = hrv.baseline_balanced_high ?? 0;
  const maxVal = Math.max(120, val + 20);
  return {
    data: [
      {
        type: "indicator",
        mode: "gauge+number",
        value: val,
        number: { suffix: " ms", font: { color: TEXT_PRIMARY, size: 32 } },
        gauge: {
          axis: { range: [0, maxVal], tickcolor: TEXT_MUTED, tickfont: { size: 9 } },
          bar: { color: C_AMBER, thickness: 0.3 },
          bgcolor: "rgba(0,0,0,0)",
          bordercolor: BORDER,
          steps: [
            { range: [0, lo], color: "rgba(251,113,133,0.18)" },
            { range: [lo, hi], color: "rgba(252,211,77,0.18)" },
            { range: [hi, maxVal], color: "rgba(34,197,94,0.18)" },
          ],
        },
      } as unknown as Data,
    ],
    layout: {
      paper_bgcolor: "rgba(0,0,0,0)",
      margin: { l: 16, r: 16, t: 10, b: 0 },
      font: { color: TEXT_MUTED, size: 11 },
    },
  };
}

// ── Two-column grid wrapper (mirrors st.columns(2)) ──────────────────────────────
function Cols2({ children }: { children: ReactNode }) {
  return <div className="grid grid-cols-1 gap-4 md:grid-cols-2">{children}</div>;
}

// ── Main render ──────────────────────────────────────────────────────────────────
export function Health() {
  const [period, setPeriod] = useState<PeriodKey>("14 days");
  const days = PERIODS[period];
  const refreshVersion = useUiStore((s) => s.refreshVersion);

  const todayQ = useQuery({
    queryKey: ["garmin", "daily_health", refreshVersion],
    queryFn: () => callTool<DailyHealth>("garmin__get_garmin_daily_health", {}),
  });
  const metricsQ = useQuery({
    queryKey: ["garmin", "training_metrics", refreshVersion],
    queryFn: () => callTool<TrainingMetrics>("garmin__get_garmin_training_metrics", {}),
  });
  const hrvQ = useQuery({
    queryKey: ["garmin", "hrv_status", refreshVersion],
    queryFn: () => callTool<HrvStatus>("garmin__get_garmin_hrv_status", {}),
  });
  const wellnessQ = useQuery({
    queryKey: ["garmin", "wellness_trends", days, refreshVersion],
    queryFn: () => callTool<WellnessTrends>("garmin__get_garmin_wellness_trends", { days }),
  });

  // Strip error-only responses so downstream sees clean dicts (mirrors health.py).
  const today = todayQ.data && !todayQ.data.error ? todayQ.data : ({} as DailyHealth);
  const metrics =
    metricsQ.data && !metricsQ.data.error ? metricsQ.data : ({} as TrainingMetrics);
  const hrv = hrvQ.data && !hrvQ.data.error ? hrvQ.data : ({} as HrvStatus);
  const wellness =
    wellnessQ.data && !wellnessQ.data.error ? wellnessQ.data : ({} as WellnessTrends);

  const garminErrors = [todayQ.data, metricsQ.data, hrvQ.data, wellnessQ.data]
    .map((r) => r?.error)
    .filter((e): e is string => !!e);

  const rows: TrendRow[] = wellness.trend ?? [];

  const loading =
    todayQ.isLoading || metricsQ.isLoading || hrvQ.isLoading || wellnessQ.isLoading;
  const queryError = todayQ.error || metricsQ.error || hrvQ.error || wellnessQ.error;

  // ── Today's snapshot fields ────────────────────────────────────────────────
  const bbVal = today.body_battery_now ?? today.body_battery_min;
  const row2: Array<[number | string, string]> = (
    [
      [today.intensity_minutes, "Intensity Min"],
      [today.moderate_intensity_min, "Moderate Min"],
      [today.vigorous_intensity_min, "Vigorous Min"],
      [today.total_calories, "Total Cal"],
      [today.floors_climbed, "Floors"],
    ] as Array<[number | null | undefined, string]>
  )
    .filter(([v]) => v != null)
    .map(([v, k]) => [v as number, k]);

  const fmtRow2 = (val: number | string, label: string): string => {
    const suffix = label.includes("Cal") ? " kcal" : label.includes("Min") ? " min" : "";
    const v = typeof val === "number" ? val.toLocaleString() : String(val);
    return `${v}${suffix}`;
  };

  // ── Training status fields ─────────────────────────────────────────────────
  const vo2 = metrics.vo2max_running ?? metrics.vo2max_cycling;
  const ts = metrics.training_status ? titleCase(metrics.training_status) : "";
  const readiness = metrics.training_readiness_score;
  const l7 = metrics.training_load_7d;
  const l28 = metrics.training_load_28d;
  const showTraining = !!vo2 || !!ts || readiness != null || l7 != null || l28 != null;
  const rp = metrics.race_predictions ?? {};
  const hasRace = !!(rp["5k"] || rp["10k"] || rp.half_marathon || rp.marathon);

  // ── HRV fields ─────────────────────────────────────────────────────────────
  const hrvLo = hrv.baseline_balanced_low;
  const hrvHi = hrv.baseline_balanced_high;
  const hrvStatus = hrv.status ? titleCase(hrv.status) : "—";
  const hrvColor = hrvStatus.toLowerCase().includes("balanced") ? C_GREEN : C_ROSE;
  const gauge = hrv.last_night_hrv ? hrvGauge(hrv) : null;

  // ── Trend chart specs ──────────────────────────────────────────────────────
  const sleepSpec = rows.length ? sleepStagesChart(rows) : null;
  const bbSpec = rows.length ? bodyBatteryChart(rows) : null;
  const hrSpec = rows.length ? hrChart(rows) : null;
  const stepsSpec = rows.length ? stepsChart(rows) : null;
  const stressSpec = rows.length ? stressChart(rows) : null;
  const intSpec = rows.length ? intensityChart(rows) : null;
  const calSpec = rows.length ? caloriesChart(rows) : null;

  return (
    <div>
      <PageHeader
        title="Health & Wellness"
        subtitle="Garmin wellness trends — sleep, Body Battery, HR, stress, HRV"
        right={<PeriodSelector options={PERIOD_KEYS} value={period} onChange={setPeriod} />}
      />

      {days > 30 && (
        <p className="mb-3 text-xs text-text-muted">
          ⏳ {days} days of data — fetching in parallel, first load takes ~
          {Math.max(5, Math.floor(days / 20))} – {Math.max(10, Math.floor(days / 10))} seconds.
          Cached for 30 min.
        </p>
      )}

      {loading && <Spinner label="Loading Garmin data…" />}
      {!loading && queryError && <ErrorBox message={String(queryError)} />}

      {garminErrors.length > 0 && (
        <div className="mb-4">
          <ErrorBox
            message={`Garmin error: ${garminErrors[0]} — Check the Settings tab; you may need to reconnect or toggle mock mode, then click Refresh data in the sidebar.`}
          />
        </div>
      )}

      {!loading && (
        <>
          {/* ── Today's snapshot ─────────────────────────────────────────── */}
          <h3 className="mb-2 text-lg font-semibold text-text-primary">Today</h3>
          {Object.keys(today).length > 0 ? (
            <>
              <div className="grid grid-cols-2 gap-3 md:grid-cols-5">
                <MetricCard label="Body Battery" value={bbVal != null ? `${bbVal}%` : "—"} />
                <MetricCard label="Steps" value={(today.steps ?? 0).toLocaleString()} />
                <MetricCard label="Resting HR" value={`${today.resting_hr ?? "—"} bpm`} />
                <MetricCard label="Active Cal" value={`${today.active_calories ?? "—"} kcal`} />
                <MetricCard label="Avg Stress" value={`${today.avg_stress ?? "—"}`} />
              </div>
              {row2.length > 0 && (
                <div
                  className="mt-3 grid grid-cols-2 gap-3"
                  style={{
                    gridTemplateColumns: `repeat(${Math.min(row2.length, 5)}, minmax(0, 1fr))`,
                  }}
                >
                  {row2.map(([val, label]) => (
                    <MetricCard key={label} label={label} value={fmtRow2(val, label)} />
                  ))}
                </div>
              )}
            </>
          ) : (
            <Caption>Today's data unavailable.</Caption>
          )}
          <Divider />

          {/* ── Training Status ──────────────────────────────────────────── */}
          {Object.keys(metrics).length > 0 && showTraining && (
            <>
              <h3 className="mb-2 text-lg font-semibold text-text-primary">Training Status</h3>
              <div className="grid grid-cols-2 gap-3 md:grid-cols-5">
                <MetricCard label="VO₂max" value={vo2 ? vo2.toFixed(1) : "—"} />
                <MetricCard label="Training Status" value={ts || "—"} />
                <MetricCard
                  label="Readiness Score"
                  value={readiness != null ? String(readiness) : "—"}
                />
                <MetricCard label="Load 7 d" value={l7 ? l7.toFixed(0) : "—"} />
                <MetricCard label="Load 28 d" value={l28 ? l28.toFixed(0) : "—"} />
              </div>
              {hasRace && (
                <>
                  <div className="mt-3">
                    <SectionLabel>Race Predictions</SectionLabel>
                  </div>
                  <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
                    <MetricCard label="5 K" value={rp["5k"] || "—"} />
                    <MetricCard label="10 K" value={rp["10k"] || "—"} />
                    <MetricCard label="Half Marathon" value={rp.half_marathon || "—"} />
                    <MetricCard label="Marathon" value={rp.marathon || "—"} />
                  </div>
                </>
              )}
              <Divider />
            </>
          )}

          {/* ── HRV Status ───────────────────────────────────────────────── */}
          {hrv.last_night_hrv && (
            <>
              <h3 className="mb-2 text-lg font-semibold text-text-primary">HRV Status</h3>
              <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
                <div className="md:col-span-1">
                  {gauge && <PlotlyChart data={gauge.data} layout={gauge.layout} height={200} />}
                </div>
                <div className="flex flex-col gap-3 md:col-span-2">
                  <MetricCard label="Last Night HRV" value={`${hrv.last_night_hrv} ms`} />
                  <MetricCard
                    label="Baseline Range"
                    value={hrvLo && hrvHi ? `${hrvLo} – ${hrvHi} ms` : "—"}
                  />
                  <span style={{ color: hrvColor, fontWeight: 700, fontSize: 15 }}>
                    {hrvStatus}
                  </span>
                  {hrv.feedback && <Caption>{hrv.feedback}</Caption>}
                </div>
              </div>
              <Divider />
            </>
          )}

          {/* ── Trend charts ─────────────────────────────────────────────── */}
          <h3 className="mb-2 text-lg font-semibold text-text-primary">{period} Trends</h3>

          {rows.length === 0 ? (
            <EmptyState message="No wellness trend data available. If Garmin is connected, try a longer period or check the Settings tab." />
          ) : (
            <>
              <SectionLabel>Sleep</SectionLabel>
              {sleepSpec ? (
                <PlotlyChart data={sleepSpec.data} layout={sleepSpec.layout} />
              ) : (
                <Caption>No sleep stage data available.</Caption>
              )}

              <div className="mt-5">
                <Cols2>
                  <div>
                    <SectionLabel>Body Battery</SectionLabel>
                    {bbSpec ? (
                      <PlotlyChart data={bbSpec.data} layout={bbSpec.layout} />
                    ) : (
                      <Caption>No Body Battery data.</Caption>
                    )}
                  </div>
                  <div>
                    <SectionLabel>Heart Rate</SectionLabel>
                    {hrSpec ? (
                      <PlotlyChart data={hrSpec.data} layout={hrSpec.layout} />
                    ) : (
                      <Caption>No resting HR data.</Caption>
                    )}
                  </div>
                </Cols2>
              </div>

              <div className="mt-5">
                <Cols2>
                  <div>
                    <SectionLabel>Daily Steps</SectionLabel>
                    {stepsSpec ? (
                      <PlotlyChart data={stepsSpec.data} layout={stepsSpec.layout} />
                    ) : (
                      <Caption>No step data.</Caption>
                    )}
                  </div>
                  <div>
                    <SectionLabel>Average Stress</SectionLabel>
                    {stressSpec ? (
                      <PlotlyChart data={stressSpec.data} layout={stressSpec.layout} />
                    ) : (
                      <Caption>No stress data.</Caption>
                    )}
                  </div>
                </Cols2>
              </div>

              {(intSpec || calSpec) && (
                <div className="mt-5">
                  <Cols2>
                    <div>
                      <SectionLabel>Intensity Minutes</SectionLabel>
                      {intSpec ? (
                        <PlotlyChart data={intSpec.data} layout={intSpec.layout} />
                      ) : (
                        <Caption>No intensity data.</Caption>
                      )}
                    </div>
                    <div>
                      <SectionLabel>Calories</SectionLabel>
                      {calSpec ? (
                        <PlotlyChart data={calSpec.data} layout={calSpec.layout} />
                      ) : (
                        <Caption>No calorie data.</Caption>
                      )}
                    </div>
                  </Cols2>
                </div>
              )}
            </>
          )}
        </>
      )}
    </div>
  );
}
