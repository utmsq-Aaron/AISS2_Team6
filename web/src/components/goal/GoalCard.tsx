// The goal centrepiece: a progress ring alongside title, status, current-vs-target,
// deadline, and the delta needed to stay on track. Handles a missing / unknown
// progress payload gracefully.

import { Pencil, Sparkles } from "lucide-react";

import type { Goal, GoalProgress } from "../../lib/api";
import {
  METRIC_META,
  deadlineLabel,
  formatMetricValue,
  formatUnitValue,
  statusStyle,
} from "../../lib/goalFormat";
import { GoalProgressRing } from "./GoalProgressRing";
import { StatusPill } from "./StatusPill";
import { Spinner } from "../Spinner";

/** Signed delta phrased for the metric direction (e.g. "+7.5 km/week to stay on track"). */
function deltaLine(goal: Goal, progress: GoalProgress): string | null {
  const d = progress.delta_needed;
  if (d == null || !isFinite(d) || Math.abs(d) < 0.05) return null;

  const metric = goal.metric;
  const sign = d > 0 ? "+" : "−";
  const mag = Math.abs(d);
  const valueStr =
    metric === "5k_time"
      ? formatUnitValue(mag, "mm:ss")
      : formatUnitValue(mag, METRIC_META[metric].unit);

  const per = metric === "weekly_distance_km" ? "/week" : "";
  const suffix =
    progress.status === "reached" ? "ahead of target" : "to stay on track";
  return `${sign}${valueStr}${per} ${suffix}`;
}

export function GoalCard({
  goal,
  progress,
  onEdit,
  loading,
}: {
  goal: Goal;
  progress?: GoalProgress | null;
  onEdit: () => void;
  loading?: boolean;
}) {
  const status = progress?.status ?? "unknown";
  const { color } = statusStyle(status);
  const pct = progress?.pct ?? null;

  const hasData = !!progress && status !== "unknown";
  const current = progress?.current;
  const target = progress?.target ?? goal.target;

  const currentStr = formatMetricValue(goal.metric, current);
  const targetStr = formatMetricValue(goal.metric, target);
  const ringSub = hasData && current != null ? `of ${targetStr}` : "no data yet";

  const delta = hasData ? deltaLine(goal, progress) : null;

  return (
    <div className="fd-card p-5 sm:p-6">
      <div className="flex flex-col items-center gap-5 sm:flex-row sm:items-center">
        {/* Ring */}
        <div className="relative">
          <GoalProgressRing
            pct={pct}
            status={status}
            size={140}
            sublabel={ringSub}
          />
          {loading && (
            <div className="absolute inset-0 grid place-items-center rounded-full bg-bg-card/60">
              <Spinner />
            </div>
          )}
        </div>

        {/* Details */}
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <h3 className="text-lg font-semibold text-text-primary">
              {goal.title || METRIC_META[goal.metric].label}
            </h3>
            <StatusPill status={status} />
            {goal.source === "coach" && (
              <span className="inline-flex items-center gap-1 rounded-full border border-accent/40 bg-accent/10 px-2 py-0.5 text-[0.72rem] font-medium text-accent">
                <Sparkles size={12} /> Set by coach
              </span>
            )}
          </div>

          {goal.why && (
            <p className="mt-1 text-sm text-text-muted">{goal.why}</p>
          )}

          <div className="mt-3 flex flex-wrap items-baseline gap-x-6 gap-y-1">
            <div>
              <span className="text-2xl font-bold text-text-primary">
                {hasData ? currentStr : "—"}
              </span>
              <span className="ml-1 text-sm text-text-muted">/ {targetStr}</span>
            </div>
            <span className="text-sm text-text-muted">
              {deadlineLabel(goal.deadline)}
            </span>
          </div>

          {delta ? (
            <p className="mt-2 text-sm font-medium" style={{ color }}>
              {delta}
            </p>
          ) : !hasData ? (
            <p className="mt-2 text-sm text-text-muted">
              No progress data yet — log an activity to see how you're tracking.
            </p>
          ) : null}

          <button
            type="button"
            onClick={onEdit}
            className="fd-btn-ghost mt-3 inline-flex items-center gap-1.5 text-sm"
          >
            <Pencil size={14} /> Edit goal
          </button>
        </div>
      </div>
    </div>
  );
}
