// Official Strava stats — YTD / last-4-weeks / all-time tabs. Relocated verbatim
// from the old Dashboard. Reads `official_stats` from the shared athlete query.

import type { ReactNode } from "react";
import { useState } from "react";

import { MetricCard } from "../MetricCard";
import type { OfficialStats, PeriodStats } from "../../lib/stravaTypes";

function TabBtn({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: ReactNode;
}) {
  return (
    <button onClick={onClick} className={`fd-tab ${active ? "fd-tab-active" : ""}`}>
      {children}
    </button>
  );
}

export function OfficialStatsSection({ stats }: { stats: OfficialStats }) {
  const [tab, setTab] = useState<"ytd" | "lfw" | "all">("ytd");
  return (
    <>
      <div className="mb-3 flex gap-1 border-b border-border">
        <TabBtn active={tab === "ytd"} onClick={() => setTab("ytd")}>
          Year to Date
        </TabBtn>
        <TabBtn active={tab === "lfw"} onClick={() => setTab("lfw")}>
          Last 4 Weeks
        </TabBtn>
        <TabBtn active={tab === "all"} onClick={() => setTab("all")}>
          All Time
        </TabBtn>
      </div>
      <OfficialStatsTab stats={stats} tab={tab} />
    </>
  );
}

function OfficialStatsTab({
  stats,
  tab,
}: {
  stats: OfficialStats;
  tab: "ytd" | "lfw" | "all";
}) {
  const period: PeriodStats | undefined =
    tab === "ytd" ? stats.year_to_date : tab === "lfw" ? stats.last_4_weeks : stats.all_time;

  const rows = [
    { sport: "Run", t: period?.run },
    { sport: "Ride", t: period?.ride },
    { sport: "Swim", t: period?.swim },
  ].filter((r) => r.t && r.t.count > 0);

  return (
    <div>
      {rows.length > 0 ? (
        <div className="overflow-x-auto rounded-card border border-border">
          <table className="w-full min-w-[34rem] text-sm">
            <thead>
              <tr className="bg-bg-surface text-text-muted">
                <th className="px-4 py-2 text-left font-medium">Sport</th>
                <th className="px-4 py-2 text-right font-medium">Activities</th>
                <th className="px-4 py-2 text-right font-medium">Distance (km)</th>
                <th className="px-4 py-2 text-right font-medium">Time (h)</th>
                <th className="px-4 py-2 text-right font-medium">Elevation (m)</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.sport} className="border-t border-border text-text-primary">
                  <td className="px-4 py-2">{r.sport}</td>
                  <td className="px-4 py-2 text-right">{r.t!.count}</td>
                  <td className="px-4 py-2 text-right">{r.t!.distance_km}</td>
                  <td className="px-4 py-2 text-right">{r.t!.moving_time_hours}</td>
                  <td className="px-4 py-2 text-right">{r.t!.elevation_gain_m}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <p className="text-sm text-text-muted">No data recorded yet.</p>
      )}

      {/* All-time: biggest ride & climb */}
      {tab === "all" &&
      (stats.biggest_ride_distance_km || stats.biggest_climb_elevation_gain_m) ? (
        <div className="mt-3 grid grid-cols-2 gap-3">
          <MetricCard label="Biggest Ride" value={`${stats.biggest_ride_distance_km ?? 0} km`} />
          <MetricCard
            label="Biggest Climb"
            value={`${Math.round(stats.biggest_climb_elevation_gain_m ?? 0)} m`}
          />
        </div>
      ) : null}
    </div>
  );
}
