// Key-metrics grid + the period selector, relocated from the old Dashboard.

import { useMemo } from "react";

import { MetricCard } from "../MetricCard";
import { PeriodSelector } from "../PeriodSelector";
import { PERIODS, type Activity, type Period } from "../../lib/stravaTypes";

export function AnalysisOverview({
  activities,
  period,
  onPeriodChange,
}: {
  activities: Activity[];
  period: Period;
  onPeriodChange: (p: Period) => void;
}) {
  const metrics = useMemo(() => {
    const totalDist = activities.reduce((s, a) => s + (a.distance_km || 0), 0);
    const totalH = activities.reduce((s, a) => s + (a.moving_time_hours || 0), 0);
    const totalElev = activities.reduce((s, a) => s + (a.elevation_gain_m || 0), 0);
    const hrs = activities
      .map((a) => a.avg_heart_rate)
      .filter((h): h is number => h != null);
    const avgHr = hrs.length ? hrs.reduce((a, b) => a + b, 0) / hrs.length : null;
    return { totalDist, totalH, totalElev, avgHr, count: activities.length };
  }, [activities]);

  return (
    <>
      <div className="mb-4">
        <PeriodSelector options={PERIODS} value={period} onChange={onPeriodChange} />
      </div>

      <div className="grid grid-cols-2 gap-3 md:grid-cols-5">
        <MetricCard label="Activities" value={metrics.count.toLocaleString()} />
        <MetricCard
          label="Total Distance"
          value={`${metrics.totalDist.toLocaleString(undefined, {
            maximumFractionDigits: 1,
          })} km`}
        />
        <MetricCard label="Total Time" value={`${Math.round(metrics.totalH).toLocaleString()} h`} />
        <MetricCard
          label="Total Elevation"
          value={`${Math.round(metrics.totalElev).toLocaleString()} m`}
        />
        <MetricCard
          label="Avg Heart Rate"
          value={metrics.avgHr != null ? `${metrics.avgHr.toFixed(0)} bpm` : "—"}
        />
      </div>
    </>
  );
}
