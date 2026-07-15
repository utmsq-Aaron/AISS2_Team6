// Formatting + status helpers shared across the goal components. Kept UI-free so
// GoalPanel / StatusPill / GoalProgressRing all agree on labels and colours.
//
// `statusStyle` maps a panel's HEALTH status (on_track/at_risk/behind/reached/
// unknown) — a distinct axis from a goal's LIFECYCLE status (active/achieved/
// archived, handled directly in GoalPanel).

import type { GoalEvent } from "./api";
import { C_AMBER, C_GREEN, C_RED, TEXT_MUTED } from "../theme/tokens";

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

// ── Target event (issue #25) ─────────────────────────────────────────────────────

/** Compact one-line label for a goal's optional target event, e.g.
 *  "Berlin Marathon · 2026-09-27 · 42.2 km". Omits missing parts; returns "" when the
 *  event has nothing worth showing (caller then renders no chip). */
export function formatEventChip(event: GoalEvent | null | undefined): string {
  if (!event) return "";
  const parts: string[] = [];
  if (event.name) parts.push(event.name);
  if (event.date) parts.push(event.date);
  if (event.distance_km != null) parts.push(`${event.distance_km} km`);
  if (parts.length === 0 && event.elevation_gain_m != null) parts.push(`${event.elevation_gain_m} m`);
  return parts.join(" · ");
}

// ── Relative time ────────────────────────────────────────────────────────────────

/** Friendly "time ago" label, e.g. "Updated 2h ago" / "Updated just now". */
export function timeAgoLabel(iso: string | null | undefined, prefix = "Updated"): string {
  if (!iso) return "Not yet updated";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "Not yet updated";
  const seconds = Math.max(0, Math.round((Date.now() - d.getTime()) / 1000));
  if (seconds < 45) return `${prefix} just now`;
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${prefix} ${minutes}m ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${prefix} ${hours}h ago`;
  const days = Math.round(hours / 24);
  if (days < 30) return `${prefix} ${days}d ago`;
  return `${prefix} ${d.toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" })}`;
}
