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
  C_AMBER, C_CYAN, C_INDIGO, C_ROSE, MAP_FINISH, MAP_START, TEXT_MUTED,
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
// Strava's own summary for this activity, returned alongside the streams. Prefer
// these over anything re-derived from the raw points: the summary row above this
// panel shows the same figures, and two different numbers for one run read as a bug.
interface StreamActivityMeta {
  distance_km?: number;
  pace_display?: string;
  avg_hr?: number | null;
}
interface StreamData {
  activity_id?: number;
  activity?: StreamActivityMeta;
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

  // No cadence chart, and no cadence track overlay either (see `available`
  // below) — it only ever appeared for activities recorded with a cadence
  // sensor, so the switcher gained and lost a button depending on which session
  // you opened. The average is still listed in the route summary table.

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

  // One chart at a time. Showing every available stream at once (a 2-up grid of
  // up to five charts) was the single busiest thing on the page; picking one keeps
  // it readable and lets each chart be tall enough to actually read.
  return <ChartSwitcher charts={charts} />;
}

function ChartSwitcher({
  charts,
}: {
  charts: Array<{ title: string; data: Data[]; layout: Partial<Layout> }>;
}) {
  const [active, setActive] = useState(charts[0]?.title ?? "");
  const shown = charts.find((c) => c.title === active) ?? charts[0];
  if (!shown) return null;

  return (
    <div>
      {charts.length > 1 && (
        <div className="mb-3 inline-flex flex-wrap gap-1 rounded-lg border border-border bg-bg-surface p-1">
          {charts.map((c) => (
            <button
              key={c.title}
              type="button"
              onClick={() => setActive(c.title)}
              aria-pressed={shown.title === c.title}
              className={`rounded-md px-3 py-1 text-xs font-medium transition-colors ${
                shown.title === c.title
                  ? "bg-accent text-white"
                  : "text-text-muted hover:text-text-primary"
              }`}
            >
              {c.title}
            </button>
          ))}
        </div>
      )}
      <PlotlyChart data={shown.data} layout={shown.layout} height={300} />
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

export function ActivityAnalysis({
  activityId,
  elevationGainM,
}: {
  activityId: number;
  /** Strava's own climb figure for this activity, when the caller has it. Summing
   *  the raw altitude stream instead overstates it several-fold — a 1 Hz altimeter
   *  wanders by a metre or two constantly, and every wobble counts as a climb. */
  elevationGainM?: number;
}) {
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
    // Cadence deliberately omitted — see the note in StreamCharts. (The chat's
    // route view keeps it: there the user asks for that overlay explicitly.)
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

  // One view, no tabs: the numbers and the map sit side by side (the map is the
  // wide half), the per-stream charts run full width underneath. The tabs this
  // replaced hid two thirds of the panel behind a click for no real gain — the
  // three parts answer one question together.
  return (
    <div className="space-y-4">
      <div className="grid gap-4 lg:grid-cols-[minmax(0,17rem)_minmax(0,1fr)]">
        <RouteInfo
          rows={rows}
          points={points}
          meta={data?.activity}
          elevationGainM={elevationGainM}
        />

        <div className="min-w-0">
          {/* Metric selector — which channel colours the track */}
          {available.length > 0 && (
            <div className="mb-3 inline-flex flex-wrap gap-1 rounded-lg border border-border bg-bg-surface p-1">
              {available.map((k) => (
                <button
                  key={k}
                  onClick={() => setChosen(k)}
                  aria-pressed={activeKey === k}
                  className={`rounded-md px-3 py-1 text-xs font-medium transition-colors ${
                    activeKey === k
                      ? "bg-accent text-white"
                      : "text-text-muted hover:text-text-primary"
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
                  height={360}
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
        </div>
      </div>

      <StreamCharts rows={rows} />
    </div>
  );
}

// Summary numbers for the left column — derived from the same stream rows the map
// and charts use, so the panel never disagrees with itself. A label/value table
// rather than a tile grid: it lives in a narrow column beside the map, where one
// row per number reads far better than wrapped tiles.
// Altitude samples wander by up to a metre even standing still, so only rises
// beyond this count as climbing. Without the threshold the sum runs several times
// over Strava's figure for the same activity.
const ELE_NOISE_M = 1;

function RouteInfo({
  rows,
  points,
  meta,
  elevationGainM,
}: {
  rows: StreamRow[];
  points: StreamPoint[];
  meta?: StreamActivityMeta;
  elevationGainM?: number;
}) {
  const streamDistKm = rows.length ? rows[rows.length - 1].dist_km : 0;
  const distKm = meta?.distance_km ?? streamDistKm;
  const times = points.map((p) => p.time_s).filter((t): t is number => t != null);
  const elapsedMin = times.length ? (Math.max(...times) - Math.min(...times)) / 60 : 0;

  let gain = 0;
  let prev: number | null = null;
  for (const p of points) {
    if (p.ele == null) continue;
    if (prev != null && p.ele - prev > ELE_NOISE_M) gain += p.ele - prev;
    prev = p.ele;
  }

  const eles = points.map((p) => p.ele).filter((e): e is number => e != null);
  const avgHr = meta?.avg_hr ?? avgOf(rows, "hr");
  const avgVel = avgOf(rows, "velocity");
  const avgCad = avgOf(rows, "cadence");
  const avgW = avgOf(rows, "watts");

  const stats: Array<[string, string]> = [
    ["Distance", `${distKm.toFixed(2)} km`],
    // Elapsed, not moving time — the summary line above shows moving time, and
    // the gap between the two is exactly how long the stops were.
    ["Elapsed time", elapsedMin >= 60
      ? `${Math.floor(elapsedMin / 60)} h ${Math.round(elapsedMin % 60)} min`
      : `${Math.round(elapsedMin)} min`],
    ["Elevation gain", `${Math.round(elevationGainM ?? gain)} m`],
  ];
  if (eles.length) stats.push(["Highest point", `${Math.round(Math.max(...eles))} m`]);
  if (meta?.pace_display) {
    stats.push(["Avg pace", meta.pace_display]);
  } else if (avgVel != null && avgVel > 0.5) {
    stats.push(["Avg pace", `${(1000 / (avgVel * 60)).toFixed(2)} min/km`]);
  }
  if (avgVel != null && avgVel > 0.5) {
    stats.push(["Avg speed", `${(avgVel * 3.6).toFixed(1)} km/h`]);
  }
  if (avgHr != null) stats.push(["Avg heart rate", `${avgHr.toFixed(0)} bpm`]);
  if (avgCad != null) stats.push(["Avg cadence", `${avgCad.toFixed(0)} spm`]);
  if (avgW != null) stats.push(["Avg power", `${avgW.toFixed(0)} W`]);

  return (
    <div className="overflow-hidden rounded-lg border border-border bg-bg-surface/40">
      <table className="w-full text-sm">
        <caption className="sr-only">Route summary</caption>
        <tbody>
          {stats.map(([label, value]) => (
            <tr key={label} className="border-b border-border/60 last:border-0">
              <th scope="row" className="fd-label px-3 py-2 text-left font-normal">
                {label}
              </th>
              <td className="px-3 py-2 text-right font-semibold tabular-nums text-text-primary">
                {value}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
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
