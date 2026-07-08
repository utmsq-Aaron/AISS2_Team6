// Training-volume charts (distance / sport-breakdown / time / elevation /
// year-over-year). Relocated verbatim from the old Dashboard's TrainingCharts.

import type { Data } from "plotly.js";
import { useMemo } from "react";

import { PlotlyChart } from "../PlotlyChart";
import { dayStr, sportOf, type Activity } from "../../lib/stravaTypes";
import { ACCENT, C_AMBER, CHART_COLORS } from "../../theme/tokens";

// ── Adaptive period bucketing (day / week / month) ───────────────────────────────
function isoWeek(d: Date): string {
  const start = new Date(Date.UTC(d.getUTCFullYear(), 0, 1));
  const days = Math.floor((d.getTime() - start.getTime()) / 86400000);
  const week = Math.floor((days + start.getUTCDay()) / 7);
  return `${d.getUTCFullYear()}-W${String(week).padStart(2, "0")}`;
}
function bucketKey(d: Date, col: "day" | "week" | "month"): string {
  const y = d.getUTCFullYear();
  const m = String(d.getUTCMonth() + 1).padStart(2, "0");
  const day = String(d.getUTCDate()).padStart(2, "0");
  if (col === "day") return `${y}-${m}-${day}`;
  if (col === "month") return `${y}-${m}`;
  return isoWeek(d);
}

interface ChartRow {
  id: number;
  type: string;
  dt: Date | null;
  distance_km: number;
  moving_time_min: number;
  elevation_m: number;
  avg_speed_kmh: number;
  year: number | null;
}

function aggregate(
  rows: ChartRow[],
  col: "day" | "week" | "month",
  field: "distance_km" | "moving_time_min" | "elevation_m",
) {
  const m = new Map<string, number>();
  for (const r of rows) {
    if (!r.dt) continue;
    const k = bucketKey(r.dt, col);
    m.set(k, (m.get(k) ?? 0) + (r[field] as number));
  }
  return [...m.entries()]
    .map(([key, value]) => ({ key, value: Math.round(value * 10) / 10 }))
    .sort((a, b) => a.key.localeCompare(b.key));
}

export function TrainingVolumeSection({
  activities,
  periodDays,
}: {
  activities: Activity[];
  periodDays: number;
}) {
  const typed = useMemo(
    () =>
      activities.filter(
        (a) => sportOf(a) && sportOf(a) !== "Unknown" && sportOf(a).trim(),
      ),
    [activities],
  );

  let aggCol: "day" | "week" | "month";
  let aggLabel: string;
  if (periodDays > 0 && periodDays <= 30) {
    aggCol = "day";
    aggLabel = "Day";
  } else if (periodDays > 0 && periodDays <= 180) {
    aggCol = "week";
    aggLabel = "Week";
  } else {
    aggCol = "month";
    aggLabel = "Month";
  }

  const rows = useMemo(
    () =>
      typed
        .map((a) => {
          const ds = dayStr(a);
          const dt = ds ? new Date(ds) : null;
          return {
            id: a.id,
            type: sportOf(a),
            dt,
            distance_km: a.distance_km ?? 0,
            moving_time_min: Math.round((a.moving_time_hours ?? 0) * 60 * 10) / 10,
            elevation_m: Math.round(a.elevation_gain_m ?? 0),
            avg_speed_kmh: a.avg_speed_kmh ?? 0,
            year: dt && !Number.isNaN(dt.getTime()) ? dt.getUTCFullYear() : null,
          };
        })
        .filter((r) => r.dt && !Number.isNaN(r.dt.getTime())),
    [typed],
  );

  const distAgg = aggregate(rows, aggCol, "distance_km");
  const timeAgg = aggregate(rows, aggCol, "moving_time_min");
  const timeHours = timeAgg.map((d) => ({
    key: d.key,
    value: Math.round((d.value / 60) * 100) / 100,
  }));
  const elevAgg = aggregate(rows, aggCol, "elevation_m");

  const sportCounts = useMemo(() => {
    const m = new Map<string, number>();
    for (const r of rows) m.set(r.type, (m.get(r.type) ?? 0) + 1);
    return [...m.entries()];
  }, [rows]);
  const nSportTypes = sportCounts.length;

  const singleSport = nSportTypes === 1 ? sportCounts[0]?.[0] ?? "" : "";
  const last50 = useMemo(() => {
    return [...rows]
      .sort((a, b) => (a.dt as Date).getTime() - (b.dt as Date).getTime())
      .slice(-50);
  }, [rows]);

  if (activities.length === 0) return null;

  return (
    <div className="grid grid-cols-1 gap-6 md:grid-cols-2">
      {/* Distance per period */}
      <div>
        <p className="fd-label mb-1">Distance per {aggLabel}</p>
        <PlotlyChart
          data={[
            {
              type: "bar",
              x: distAgg.map((d) => d.key),
              y: distAgg.map((d) => d.value),
              marker: { color: ACCENT, line: { width: 0 } },
            } as Data,
          ]}
          layout={{ yaxis: { ticksuffix: " km" } }}
          height={260}
        />
      </div>

      {/* Sport breakdown OR single-sport diagnostic */}
      <div>
        {nSportTypes > 1 ? (
          <>
            <p className="fd-label mb-1">Sport Breakdown</p>
            <PlotlyChart
              data={[
                {
                  type: "pie",
                  values: sportCounts.map((s) => s[1]),
                  labels: sportCounts.map((s) => s[0]),
                  hole: 0.5,
                  marker: { colors: CHART_COLORS },
                  textposition: "inside",
                  textinfo: "percent+label",
                  textfont: { size: 11 },
                } as unknown as Data,
              ]}
              height={260}
            />
          </>
        ) : (
          <SingleSportChart sport={singleSport} rows={last50} />
        )}
      </div>

      {/* Training time per period (area) */}
      <div>
        <p className="fd-label mb-1">Training Time per {aggLabel}</p>
        <PlotlyChart
          data={[
            {
              type: "scatter",
              mode: "lines",
              x: timeHours.map((d) => d.key),
              y: timeHours.map((d) => d.value),
              fill: "tozeroy",
              line: { color: ACCENT, width: 2 },
            } as Data,
          ]}
          layout={{ yaxis: { ticksuffix: " h" } }}
          height={260}
        />
      </div>

      {/* Year-over-year (long periods) OR elevation per period */}
      <div>
        {periodDays === 0 || periodDays > 90 ? (
          <YearOverYearChart rows={rows} />
        ) : (
          <>
            <p className="fd-label mb-1">Elevation per {aggLabel}</p>
            <PlotlyChart
              data={[
                {
                  type: "bar",
                  x: elevAgg.map((d) => d.key),
                  y: elevAgg.map((d) => d.value),
                  marker: { color: C_AMBER, line: { width: 0 } },
                } as Data,
              ]}
              layout={{ yaxis: { ticksuffix: " m" } }}
              height={260}
            />
          </>
        )}
      </div>
    </div>
  );
}

function SingleSportChart({ sport, rows }: { sport: string; rows: ChartRow[] }) {
  const runningLike = ["Run", "TrailRun", "VirtualRun", "Hike", "Walk"].includes(sport);
  const cyclingLike = [
    "Ride",
    "MountainBikeRide",
    "GravelRide",
    "EBikeRide",
    "VirtualRide",
  ].includes(sport);

  const xs = rows.map((r) => r.dt as Date);
  const paceRows = rows.filter((r) => r.avg_speed_kmh > 0);
  const hasPace = runningLike && paceRows.length > 0;
  const hasSpeed = cyclingLike && rows.some((r) => r.avg_speed_kmh > 0);
  const hasElev = rows.some((r) => r.elevation_m > 0);

  const sizeOf = (r: ChartRow) => Math.max(6, Math.min(28, r.distance_km / 2 + 6));

  if (hasPace) {
    return (
      <ScatterPanel
        label="Pace per Activity"
        x={xs}
        y={rows.map((r) =>
          r.avg_speed_kmh > 0 ? Math.round((60 / r.avg_speed_kmh) * 100) / 100 : null,
        )}
        sizes={rows.map(sizeOf)}
        ticksuffix=" min/km"
      />
    );
  }
  if (hasSpeed) {
    return (
      <ScatterPanel
        label="Speed per Activity"
        x={xs}
        y={rows.map((r) => r.avg_speed_kmh || null)}
        sizes={rows.map(sizeOf)}
        ticksuffix=" km/h"
      />
    );
  }
  if (hasElev) {
    return (
      <>
        <p className="fd-label mb-1">Elevation per Activity</p>
        <PlotlyChart
          data={[
            {
              type: "bar",
              x: xs,
              y: rows.map((r) => r.elevation_m),
              marker: { color: CHART_COLORS[2], line: { width: 0 } },
            } as Data,
          ]}
          layout={{ yaxis: { ticksuffix: " m" } }}
          height={260}
        />
      </>
    );
  }
  return (
    <>
      <p className="fd-label mb-1">Distance per Activity</p>
      <PlotlyChart
        data={[
          {
            type: "bar",
            x: xs,
            y: rows.map((r) => r.distance_km),
            marker: { color: ACCENT, line: { width: 0 } },
          } as Data,
        ]}
        layout={{ yaxis: { ticksuffix: " km" } }}
        height={260}
      />
    </>
  );
}

function ScatterPanel({
  label,
  x,
  y,
  sizes,
  ticksuffix,
}: {
  label: string;
  x: Date[];
  y: (number | null)[];
  sizes: number[];
  ticksuffix: string;
}) {
  return (
    <>
      <p className="fd-label mb-1">{label}</p>
      <PlotlyChart
        data={[
          {
            type: "scatter",
            mode: "markers",
            x,
            y,
            marker: { color: ACCENT, size: sizes, line: { width: 0 } },
          } as Data,
        ]}
        layout={{ yaxis: { ticksuffix } }}
        height={260}
      />
    </>
  );
}

function YearOverYearChart({ rows }: { rows: ChartRow[] }) {
  const years = useMemo(() => {
    const set = new Set<number>();
    for (const r of rows) if (r.year != null) set.add(r.year);
    return [...set].sort();
  }, [rows]);
  const sports = useMemo(() => {
    const set = new Set<string>();
    for (const r of rows) set.add(r.type);
    return [...set];
  }, [rows]);

  const traces: Data[] = sports.map((sp, idx) => {
    const yByYear = new Map<number, number>();
    for (const r of rows) {
      if (r.type === sp && r.year != null)
        yByYear.set(r.year, (yByYear.get(r.year) ?? 0) + r.distance_km);
    }
    return {
      type: "bar",
      name: sp,
      x: years.map((y) => String(y)),
      y: years.map((y) => Math.round((yByYear.get(y) ?? 0) * 10) / 10),
      marker: { color: CHART_COLORS[idx % CHART_COLORS.length], line: { width: 0 } },
    } as Data;
  });

  return (
    <>
      <p className="fd-label mb-1">Year-over-Year Distance</p>
      <PlotlyChart
        data={traces}
        layout={{ barmode: "stack", yaxis: { ticksuffix: " km" } }}
        height={260}
      />
    </>
  );
}
