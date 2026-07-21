// Plan vs. Actual — did the training match the plan? Target line = the plan's
// deterministic weekly km (goal sport); bars = what actually happened (recorded
// review values where present, else summed from Strava activities in that week).
// Phases show as background bands; milestones ride along in the hover text.
import { useQuery } from "@tanstack/react-query";
import type { Data, Layout, Shape } from "plotly.js";
import { useMemo } from "react";

import { callTool, getAthleteOverview } from "../../lib/api";
import type { ActivitiesResult as StravaActivitiesResult } from "../../lib/stravaTypes";
import { ACCENT, TEXT_MUTED } from "../../theme/tokens";
import { ExplainButton } from "../ExplainButton";
import { PlotlyChart } from "../PlotlyChart";
import { Spinner } from "../Spinner";

const PHASE_COLORS: Record<string, string> = {
  base: "#FDBA74", build: "#FB923C", peak: "#F97316", taper: "#FFD9B8",
};

function matchesSport(t: string, sport: "run" | "ride"): boolean {
  const s = t.toLowerCase();
  return sport === "run" ? s.includes("run") : s.includes("ride") || s.includes("bike");
}

export function PlanVsActualSection() {
  const ovQ = useQuery({ queryKey: ["athlete-overview"], queryFn: getAthleteOverview });
  const plan = ovQ.data?.plan;
  const race = ovQ.data?.profile?.race;
  const planStart = plan?.weeks?.[0]?.start_date;

  // Activities from the plan's first week onward — independent of the page's
  // period filter, so early plan weeks stay covered.
  const actsQ = useQuery({
    queryKey: ["plan-vs-actual-acts", planStart],
    enabled: Boolean(planStart),
    queryFn: () =>
      callTool<StravaActivitiesResult>("strava__get_activities",
        { limit: 400, start_date: planStart }),
  });

  const chart = useMemo(() => {
    if (!plan || !race) return null;
    const sport = (race.sport ?? "run") as "run" | "ride";
    const weeks = plan.weeks;
    const acts = actsQ.data?.activities ?? [];

    const weekKm = weeks.map((w) => {
      if (w.actual?.distance_km != null) return w.actual.distance_km;
      const start = w.start_date;
      const end = new Date(new Date(w.start_date).getTime() + 7 * 86400000)
        .toISOString().slice(0, 10);
      let km = 0;
      for (const a of acts) {
        const d = a.date || (a.start_date || "").slice(0, 10);
        if (d && d >= start && d < end && matchesSport(a.sport_type || a.type || "", sport)) {
          km += a.distance_km ?? 0;
        }
      }
      return Math.round(km * 10) / 10;
    });

    const today = new Date().toISOString().slice(0, 10);
    const labels = weeks.map((w) => `W${w.week}`);
    const hover = weeks.map((w, i) => {
      const ms = (w.milestones ?? []) as { name?: string }[];
      return `W${w.week} · ${w.phase}${w.cutback ? " (cutback)" : ""}`
        + `<br>target ${w.target_km} km · actual ${w.start_date <= today ? weekKm[i] : "—"} km`
        + (w.actual ? " (reviewed)" : "")
        + (ms.length ? `<br>${ms.map((m) => m.name).join(", ")}` : "");
    });

    // Phase background bands (category axis → index coordinates).
    const shapes: Partial<Shape>[] = [];
    let runStart = 0;
    for (let i = 1; i <= weeks.length; i++) {
      if (i === weeks.length || weeks[i].phase !== weeks[runStart].phase) {
        shapes.push({
          type: "rect", xref: "x", yref: "paper",
          x0: runStart - 0.5, x1: i - 0.5, y0: 0, y1: 1,
          fillcolor: PHASE_COLORS[weeks[runStart].phase] ?? "#334155",
          opacity: 0.10, line: { width: 0 },
        });
        runStart = i;
      }
    }

    const data: Data[] = [
      {
        type: "bar",
        name: "Actual",
        x: labels,
        // Future weeks have no actual yet — hide their bars instead of showing 0.
        y: weeks.map((w, i) => (w.start_date <= today ? weekKm[i] : null)),
        marker: { color: ACCENT, line: { width: 0 } },
        hovertext: hover, hoverinfo: "text",
      } as Data,
      {
        type: "scatter",
        mode: "lines+markers",
        name: "Target",
        x: labels,
        y: weeks.map((w) => w.target_km),
        line: { color: TEXT_MUTED, width: 2, shape: "hv" },
        marker: { size: 5 },
        hovertext: hover, hoverinfo: "text",
      } as Data,
    ];
    const layout: Partial<Layout> = {
      shapes: shapes as Shape[],
      yaxis: { ticksuffix: " km" },
      legend: { orientation: "h" },
    };
    const summary = {
      race: { name: race.name, sport, distance_km: race.distance_km, date: race.date },
      weeks: weeks.map((w, i) => ({
        week: w.week, phase: w.phase, target_km: w.target_km,
        actual_km: w.start_date <= today ? weekKm[i] : null,
        reviewed: Boolean(w.actual), cutback: Boolean(w.cutback),
      })),
    };
    return { data, layout, summary };
  }, [plan, race, actsQ.data]);

  if (ovQ.isLoading) return <Spinner label="Loading plan…" />;
  if (!plan || !race || !chart) {
    return (
      <p className="text-sm text-text-muted">
        No training plan yet — set a race goal in the Coach tab and generate a plan
        to see how your weeks track against it.
      </p>
    );
  }

  return (
    <>
      <div className="mb-2 flex items-center justify-between gap-2">
        <p className="text-sm text-text-muted">
          Each bar is what you actually did that week (goal sport, {race.sport === "ride" ? "riding" : "running"});
          the grey line is the plan's target. Phases tint the background.
        </p>
        <ExplainButton title="Training plan vs. actual weekly volume" data={chart.summary} />
      </div>
      <PlotlyChart data={chart.data} layout={chart.layout} height={300} />
    </>
  );
}
