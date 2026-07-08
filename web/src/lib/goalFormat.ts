// Formatting + status helpers shared across the goal components. Kept UI-free so
// GoalCard / GoalForm / StatusPill all agree on units, labels and colours.

import type { GoalMetric, GoalUnit } from "./api";
import { C_AMBER, C_GREEN, C_RED, TEXT_MUTED } from "../theme/tokens";

// ── Metric metadata (label + default unit/direction on select) ─────────────────
export interface MetricMeta {
  label: string;
  unit: GoalUnit;
  direction: "increase" | "decrease" | "maintain";
}

export const METRIC_META: Record<GoalMetric, MetricMeta> = {
  weekly_distance_km: { label: "Weekly distance", unit: "km", direction: "increase" },
  total_distance_km: { label: "Total distance", unit: "km", direction: "increase" },
  "5k_time": { label: "5K time", unit: "mm:ss", direction: "decrease" },
  bodyweight_kg: { label: "Bodyweight", unit: "kg", direction: "decrease" },
};

export const METRIC_ORDER: GoalMetric[] = [
  "weekly_distance_km",
  "total_distance_km",
  "5k_time",
  "bodyweight_kg",
];

// ── Value formatting ────────────────────────────────────────────────────────────

/** Seconds → "mm:ss" (e.g. 1500 → "25:00"). */
export function secondsToMmss(seconds: number): string {
  if (!isFinite(seconds) || seconds < 0) return "—";
  const total = Math.round(seconds);
  const m = Math.floor(total / 60);
  const s = total % 60;
  return `${m}:${String(s).padStart(2, "0")}`;
}

/** "mm:ss" (or "m:ss") → seconds, or null if unparseable. */
export function mmssToSeconds(text: string): number | null {
  const t = text.trim();
  const m = /^(\d{1,3}):([0-5]?\d)$/.exec(t);
  if (!m) return null;
  return Number(m[1]) * 60 + Number(m[2]);
}

/**
 * Human display of a metric value. `5k_time` renders as mm:ss; everything else
 * as `<value> <unit>` with a sensible number of decimals.
 */
export function formatMetricValue(metric: GoalMetric, value: number | null | undefined): string {
  if (value == null || !isFinite(value)) return "—";
  if (metric === "5k_time") return secondsToMmss(value);
  const unit = METRIC_META[metric].unit;
  const digits = Number.isInteger(value) ? 0 : 1;
  return `${value.toLocaleString(undefined, { maximumFractionDigits: digits })} ${unit}`;
}

/** Display a bare value in a given unit (used when the metric isn't handy). */
export function formatUnitValue(value: number | null | undefined, unit?: GoalUnit): string {
  if (value == null || !isFinite(value)) return "—";
  if (unit === "mm:ss") return secondsToMmss(value);
  const digits = Number.isInteger(value) ? 0 : 1;
  const v = value.toLocaleString(undefined, { maximumFractionDigits: digits });
  return unit ? `${v} ${unit}` : v;
}

// ── Status → colour + label ─────────────────────────────────────────────────────

export interface StatusStyle {
  label: string;
  color: string;
}

export function statusStyle(status: string | null | undefined): StatusStyle {
  switch (status) {
    case "on_track":
      return { label: "On track", color: C_GREEN };
    case "reached":
      return { label: "Reached", color: C_GREEN };
    case "at_risk":
      return { label: "At risk", color: C_AMBER };
    case "behind":
      return { label: "Behind", color: C_RED };
    default:
      return { label: "No data", color: TEXT_MUTED };
  }
}

// ── Deadline helpers ────────────────────────────────────────────────────────────

/** Whole days from today (UTC) to an ISO date; null when no/invalid date. */
export function daysUntil(iso: string | null | undefined): number | null {
  if (!iso) return null;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return null;
  const today = new Date();
  const a = Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate());
  const b = Date.UTC(today.getFullYear(), today.getMonth(), today.getDate());
  return Math.round((a - b) / 86_400_000);
}

/** Friendly deadline label, e.g. "Mar 1, 2026 · 34 days left" / "Overdue by 3 days". */
export function deadlineLabel(iso: string | null | undefined): string {
  if (!iso) return "No deadline";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const dateStr = d.toLocaleDateString(undefined, {
    day: "numeric",
    month: "short",
    year: "numeric",
  });
  const days = daysUntil(iso);
  if (days == null) return dateStr;
  if (days > 0) return `${dateStr} · ${days} day${days === 1 ? "" : "s"} left`;
  if (days === 0) return `${dateStr} · due today`;
  return `${dateStr} · overdue by ${-days} day${days === -1 ? "" : "s"}`;
}
