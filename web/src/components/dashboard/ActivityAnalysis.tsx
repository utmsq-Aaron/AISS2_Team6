// Activity stream analysis — colored route overlay + per-distance metric charts.
// Faithful port of ui/activity_analysis.py.

import type { Data, Layout } from "plotly.js";
import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";

import { PlotlyChart } from "../PlotlyChart";
import { RouteMap } from "../RouteMap";
import { Spinner, EmptyState } from "../Spinner";
import { callTool } from "../../lib/api";
import {
  C_AMBER, C_CYAN, C_GREEN, C_INDIGO, C_ROSE, MAP_FINISH, MAP_START, TEXT_MUTED,
} from "../../theme/tokens";
import { useUiStore } from "../../store/uiStore";
import {
  type MetricKey, METRIC_DEFS, coloredSegments, plainRoute, Legend,
} from "../trackOverlay";

const MAX_PACE_OUTLIER_MIN_KM = 20;

interface StreamPoint {
  lat: number | null;
  lon: number | null;
  ele: number | null;
  time_s: number | null;
  dist_m: number | null;
  hr: number | null;
  cadence: number | null;
  velocity: number | null;
  watts: number | null;
}
interface StreamData {
  activity_id?: number;
  points?: StreamPoint[];
  has_hr?: boolean;
  has_cadence?: boolean;
  has_velocity?: boolean;
  has_watts?: boolean;
  error?: string;
}

// ── Stream charts ──────────────────────────────────────────────────────────
interface StreamRow {
  dist_km: number;
  hr: number | null;
  velocity: number | null;
  ele: number | null;
  cadence: number | null;
  watts: number | null;
}

function avgOf(rows: StreamRow[], key: keyof StreamRow): number | null {
  const vals = rows.map((r) => r[key]).filter((v): v is number => v != null);
  if (!vals.length) return null;
  return vals.reduce((a, b) => a + b, 0) / vals.length;
}

function StreamCharts({ rows }: { rows: StreamRow[] }) {
  const charts: Array<{ title: string; data: Data[]; layout: Partial<Layout> }> = [];

  const dist = rows.map((r) => r.dist_km);

  if (rows.some((r) => r.hr != null)) {
    const avg = avgOf(rows, "hr");
    charts.push({
      title: "Heart Rate",
      data: [
        {
          x: dist,
          y: rows.map((r) => r.hr),
          mode: "lines",
          line: { color: C_ROSE, width: 1.5, shape: "spline" },
          fill: "tozeroy",
          fillcolor: "rgba(251,113,133,0.12)",
          hovertemplate: "<b>%{x:.2f} km</b><br>HR: %{y:.0f} bpm<extra></extra>",
        } as Data,
      ],
      layout: {
        yaxis: { ticksuffix: " bpm" },
        shapes: avg != null ? [hLine(avg)] : [],
        annotations: avg != null ? [hAnnot(`avg ${avg.toFixed(0)}`, avg)] : [],
      },
    });
  }

  const velRows = rows.filter((r) => r.velocity != null && r.velocity > 0.5);
  if (velRows.length) {
    const paced = velRows
      .map((r) => ({ x: r.dist_km, y: 1000 / ((r.velocity as number) * 60) }))
      .filter((p) => p.y < MAX_PACE_OUTLIER_MIN_KM);
    charts.push({
      title: "Pace",
      data: [
        {
          x: paced.map((p) => p.x),
          y: paced.map((p) => p.y),
          mode: "lines",
          line: { color: C_CYAN, width: 1.5, shape: "spline" },
          fill: "tozeroy",
          fillcolor: "rgba(34,211,238,0.10)",
          hovertemplate: "<b>%{x:.2f} km</b><br>Pace: %{y:.2f} min/km<extra></extra>",
        } as Data,
      ],
      layout: { yaxis: { ticksuffix: " /km", autorange: "reversed" } },
    });
  }

  if (rows.some((r) => r.ele != null)) {
    charts.push({
      title: "Elevation",
      data: [
        {
          x: dist,
          y: rows.map((r) => r.ele),
          mode: "lines",
          line: { color: C_AMBER, width: 1.5, shape: "spline" },
          fill: "tozeroy",
          fillcolor: "rgba(252,211,77,0.10)",
          hovertemplate: "<b>%{x:.2f} km</b><br>Elevation: %{y:.0f} m<extra></extra>",
        } as Data,
      ],
      layout: { yaxis: { ticksuffix: " m" } },
    });
  }

  if (rows.some((r) => r.cadence != null)) {
    const avg = avgOf(rows, "cadence");
    charts.push({
      title: "Cadence",
      data: [
        {
          x: dist,
          y: rows.map((r) => r.cadence),
          mode: "lines",
          line: { color: C_GREEN, width: 1.5, shape: "spline" },
          fill: "tozeroy",
          fillcolor: "rgba(34,197,94,0.10)",
          hovertemplate: "<b>%{x:.2f} km</b><br>Cadence: %{y:.0f} spm<extra></extra>",
        } as Data,
      ],
      layout: {
        shapes: avg != null ? [hLine(avg)] : [],
        annotations: avg != null ? [hAnnot(`avg ${avg.toFixed(0)}`, avg)] : [],
      },
    });
  }

  if (rows.some((r) => r.watts != null)) {
    const avg = avgOf(rows, "watts");
    charts.push({
      title: "Power",
      data: [
        {
          x: dist,
          y: rows.map((r) => r.watts),
          mode: "lines",
          line: { color: C_INDIGO, width: 1.5, shape: "spline" },
          fill: "tozeroy",
          fillcolor: "rgba(129,140,248,0.10)",
          hovertemplate: "<b>%{x:.2f} km</b><br>Power: %{y:.0f} W<extra></extra>",
        } as Data,
      ],
      layout: {
        yaxis: { ticksuffix: " W" },
        shapes: avg != null ? [hLine(avg)] : [],
        annotations: avg != null ? [hAnnot(`avg ${avg.toFixed(0)} W`, avg)] : [],
      },
    });
  }

  if (!charts.length) {
    return (
      <p className="text-sm text-text-muted">
        No metric streams available for this activity (outdoor GPS required).
      </p>
    );
  }

  // 2-up grid, matching the Streamlit pairing.
  return (
    <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
      {charts.map((c) => (
        <div key={c.title}>
          <p className="fd-label mb-1">{c.title}</p>
          <PlotlyChart data={c.data} layout={c.layout} height={220} />
        </div>
      ))}
    </div>
  );
}

type Shape = NonNullable<Layout["shapes"]>[number];
type Annotation = NonNullable<Layout["annotations"]>[number];

function hLine(y: number): Shape {
  return {
    type: "line",
    xref: "paper",
    x0: 0,
    x1: 1,
    y0: y,
    y1: y,
    line: { dash: "dot", color: TEXT_MUTED, width: 1 },
  };
}
function hAnnot(text: string, y: number): Annotation {
  return {
    xref: "paper",
    x: 1,
    y,
    text,
    showarrow: false,
    font: { color: TEXT_MUTED, size: 10 },
    xanchor: "right",
    yanchor: "bottom",
  };
}

// ── Public component ────────────────────────────────────────────────────────

export function ActivityAnalysis({ activityId }: { activityId: number }) {
  const refreshVersion = useUiStore((s) => s.refreshVersion);
  const { data, isLoading, error } = useQuery({
    queryKey: ["streams", activityId, refreshVersion],
    queryFn: () => callTool<StreamData>("strava__get_activity_streams", { activity_id: activityId }),
  });

  const points = useMemo(() => data?.points ?? [], [data]);

  // Determine available overlay metrics, mirroring show_analysis()
  const available = useMemo(() => {
    const out: MetricKey[] = [];
    if (data?.has_hr) out.push("hr");
    if (data?.has_velocity) out.push("velocity");
    if (points.some((p) => p.ele != null)) out.push("ele");
    if (data?.has_cadence) out.push("cadence");
    if (data?.has_watts) out.push("watts");
    return out;
  }, [data, points]);

  const [chosen, setChosen] = useState<MetricKey | null>(null);
  const activeKey: MetricKey | null =
    chosen && available.includes(chosen) ? chosen : (available[0] ?? null);

  const rows: StreamRow[] = useMemo(
    () =>
      points.map((p, i) => ({
        dist_km: (p.dist_m ?? i) / 1000,
        hr: p.hr,
        velocity: p.velocity,
        ele: p.ele,
        cadence: p.cadence,
        watts: p.watts,
      })),
    [points],
  );

  if (isLoading) return <Spinner label="Loading GPS streams…" />;
  if (error) return <ErrorWarn message={`Stream data unavailable: ${String(error)}`} />;
  if (data?.error) return <ErrorWarn message={`No stream data: ${data.error}`} />;
  if (!points.length) return <EmptyState message="No GPS stream data for this activity." />;

  const segs =
    activeKey != null
      ? coloredSegments(points, activeKey, METRIC_DEFS[activeKey][1])
      : plainRoute(points);
  const [, , highLbl, lowLbl] = activeKey != null ? METRIC_DEFS[activeKey] : ["", false, "", ""];

  const startPt = points.find((p) => p.lat != null && p.lon != null);
  const finishPt = [...points].reverse().find((p) => p.lat != null && p.lon != null);

  return (
    <div>
      <h3 className="mb-3 text-lg font-semibold text-text-primary">Activity Analysis</h3>

      {/* Metric selector */}
      {available.length > 0 && (
        <div className="mb-3 inline-flex flex-wrap gap-1 rounded-lg border border-border bg-bg-surface p-1">
          {available.map((k) => (
            <button
              key={k}
              onClick={() => setChosen(k)}
              aria-pressed={activeKey === k}
              className={`rounded-md px-3 py-1 text-xs font-medium transition-colors ${
                activeKey === k ? "bg-accent text-white" : "text-text-muted hover:text-text-primary"
              }`}
            >
              {METRIC_DEFS[k][0]}
            </button>
          ))}
        </div>
      )}

      <div className="flex gap-3">
        <div className="min-w-0 flex-1">
          {segs.length ? (
            <RouteMap
              polylines={segs}
              markers={[
                ...(startPt
                  ? [{ lat: startPt.lat as number, lon: startPt.lon as number, color: MAP_START, label: "Start" }]
                  : []),
                ...(finishPt
                  ? [{ lat: finishPt.lat as number, lon: finishPt.lon as number, color: MAP_FINISH, label: "Finish" }]
                  : []),
              ]}
              height={440}
              ariaLabel="Activity route map"
            />
          ) : (
            <EmptyState message="Not enough GPS points for route visualization." />
          )}
        </div>
        {available.length > 0 && segs.length > 0 && (
          <Legend highLabel={highLbl as string} lowLabel={lowLbl as string} />
        )}
      </div>

      <div className="my-5 h-px bg-border" />
      <StreamCharts rows={rows} />
    </div>
  );
}

function ErrorWarn({ message }: { message: string }) {
  return (
    <div className="rounded-lg border border-metric-amber/40 bg-metric-amber/10 px-4 py-3 text-sm text-metric-amber">
      {message}
    </div>
  );
}
