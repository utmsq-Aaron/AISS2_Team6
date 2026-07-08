// Create/edit form for a training goal. Selecting a metric sets sensible unit +
// direction defaults; the target field switches to an mm:ss text input for the
// 5K-time metric (stored as seconds). Validates target > 0 and a future deadline.

import { useMemo, useState } from "react";

import type { GoalInput, GoalMetric, Goal } from "../../lib/api";
import {
  METRIC_META,
  METRIC_ORDER,
  mmssToSeconds,
  secondsToMmss,
} from "../../lib/goalFormat";
import { ErrorBox } from "../Spinner";

function todayIso(): string {
  return new Date().toISOString().slice(0, 10);
}

function isFutureDate(iso: string): boolean {
  if (!iso) return false;
  const d = new Date(iso + "T00:00:00");
  if (Number.isNaN(d.getTime())) return false;
  const today = new Date();
  const a = Date.UTC(d.getFullYear(), d.getMonth(), d.getDate());
  const b = Date.UTC(today.getFullYear(), today.getMonth(), today.getDate());
  return a > b;
}

export function GoalForm({
  initial,
  onSubmit,
  onCancel,
  saving,
  error,
}: {
  initial?: Goal | null;
  onSubmit: (v: GoalInput) => void;
  onCancel: () => void;
  saving?: boolean;
  error?: string;
}) {
  const [title, setTitle] = useState(initial?.title ?? "");
  const [metric, setMetric] = useState<GoalMetric>(initial?.metric ?? "weekly_distance_km");
  const [direction, setDirection] = useState(
    initial?.direction ?? METRIC_META[initial?.metric ?? "weekly_distance_km"].direction,
  );
  // Target is kept as the user-typed string; for 5k_time this is "mm:ss".
  const [targetStr, setTargetStr] = useState<string>(() => {
    if (initial?.target == null) return "";
    return initial.metric === "5k_time"
      ? secondsToMmss(initial.target)
      : String(initial.target);
  });
  const [deadline, setDeadline] = useState(initial?.deadline ?? "");
  const [baseline, setBaseline] = useState<string>(
    initial?.baseline != null ? String(initial.baseline) : "",
  );
  const [why, setWhy] = useState(initial?.why ?? "");
  const [localError, setLocalError] = useState<string | null>(null);

  const isTime = metric === "5k_time";
  const unit = METRIC_META[metric].unit;

  // Parse the target string → seconds (5k_time) or a plain number.
  const parsedTarget = useMemo<number | null>(() => {
    const t = targetStr.trim();
    if (!t) return null;
    if (isTime) return mmssToSeconds(t);
    const n = Number(t);
    return isFinite(n) ? n : null;
  }, [targetStr, isTime]);

  function changeMetric(next: GoalMetric) {
    const meta = METRIC_META[next];
    setMetric(next);
    setDirection(meta.direction);
    // Reset the target so the km ⇄ mm:ss switch never carries a stale value.
    setTargetStr("");
    setLocalError(null);
  }

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setLocalError(null);

    if (!title.trim()) {
      setLocalError("Give your goal a title.");
      return;
    }
    if (parsedTarget == null || parsedTarget <= 0) {
      setLocalError(
        isTime
          ? "Enter a target time as mm:ss (e.g. 25:00)."
          : "Enter a target greater than 0.",
      );
      return;
    }
    if (!deadline || !isFutureDate(deadline)) {
      setLocalError("Pick a deadline in the future.");
      return;
    }

    const baselineNum = baseline.trim() ? Number(baseline) : undefined;

    onSubmit({
      title: title.trim(),
      metric,
      target: parsedTarget,
      unit,
      direction,
      deadline,
      baseline:
        baselineNum != null && isFinite(baselineNum) ? baselineNum : undefined,
      why: why.trim() || undefined,
      status: initial?.status ?? "active",
    });
  }

  return (
    <form onSubmit={handleSubmit} className="flex flex-col gap-4">
      {/* Title */}
      <div>
        <label className="fd-label mb-1 block">Title</label>
        <input
          className="fd-input w-full"
          placeholder="e.g. Run 40 km every week"
          value={title}
          onChange={(e) => setTitle(e.target.value)}
        />
      </div>

      {/* Metric + direction */}
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <div>
          <label className="fd-label mb-1 block">Metric</label>
          <select
            className="fd-input w-full"
            value={metric}
            onChange={(e) => changeMetric(e.target.value as GoalMetric)}
          >
            {METRIC_ORDER.map((m) => (
              <option key={m} value={m}>
                {METRIC_META[m].label}
              </option>
            ))}
          </select>
        </div>
        <div>
          <label className="fd-label mb-1 block">Direction</label>
          <select
            className="fd-input w-full"
            value={direction}
            onChange={(e) =>
              setDirection(e.target.value as GoalInput["direction"])
            }
          >
            <option value="increase">Increase</option>
            <option value="decrease">Decrease</option>
            <option value="maintain">Maintain</option>
          </select>
        </div>
      </div>

      {/* Target + baseline */}
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <div>
          <label className="fd-label mb-1 block">
            Target {isTime ? "(mm:ss)" : `(${unit})`}
          </label>
          {isTime ? (
            <input
              className="fd-input w-full"
              inputMode="numeric"
              placeholder="25:00"
              value={targetStr}
              onChange={(e) => setTargetStr(e.target.value)}
            />
          ) : (
            <input
              className="fd-input w-full"
              type="number"
              min={0}
              step="any"
              placeholder="40"
              value={targetStr}
              onChange={(e) => setTargetStr(e.target.value)}
            />
          )}
        </div>
        <div>
          <label className="fd-label mb-1 block">
            Baseline {isTime ? "(mm:ss, optional)" : `(${unit}, optional)`}
          </label>
          <input
            className="fd-input w-full"
            type={isTime ? "text" : "number"}
            inputMode={isTime ? "numeric" : undefined}
            min={isTime ? undefined : 0}
            step={isTime ? undefined : "any"}
            placeholder={isTime ? "28:30" : "where you are today"}
            value={baseline}
            onChange={(e) => setBaseline(e.target.value)}
          />
        </div>
      </div>

      {/* Deadline */}
      <div>
        <label className="fd-label mb-1 block">Deadline</label>
        <input
          className="fd-input w-full sm:w-56"
          type="date"
          min={todayIso()}
          value={deadline ?? ""}
          onChange={(e) => setDeadline(e.target.value)}
        />
      </div>

      {/* Why */}
      <div>
        <label className="fd-label mb-1 block">Why this matters (optional)</label>
        <textarea
          className="fd-input h-20 w-full"
          placeholder="What's driving this goal? Your coach will use this for context."
          value={why}
          onChange={(e) => setWhy(e.target.value)}
        />
      </div>

      {(localError || error) && <ErrorBox message={localError || error!} />}

      <div className="flex gap-2">
        <button type="submit" className="fd-btn-primary" disabled={saving}>
          {saving ? "Saving…" : "Save goal"}
        </button>
        <button
          type="button"
          className="fd-btn-secondary"
          onClick={onCancel}
          disabled={saving}
        >
          Cancel
        </button>
      </div>
    </form>
  );
}
