// Shared colored-route-overlay machinery — the metric-coloured GPS track gradient.
// Extracted verbatim from dashboard/ActivityAnalysis.tsx so both the Dashboard
// (ActivityAnalysis) and the chat (chat/RouteResult → ActivityTrack) can render
// the same heart-rate / pace / altitude / cadence / power overlay on an activity map.

import { ACCENT, GRAD_HIGH, GRAD_LOW, GRAD_MID, TEXT_MUTED } from "../theme/tokens";
import type { PolyLineSpec } from "./RouteMap";

export const MAX_ROUTE_SEGMENTS = 200;

// Structural point type — any object carrying lat/lon plus the optional per-point
// metric channels. Both ActivityAnalysis's StreamPoint and RouteResult's TrackPoint
// satisfy it, so the overlay functions work for either caller.
export interface OverlayPoint {
  lat?: number | null;
  lon?: number | null;
  ele?: number | null;
  hr?: number | null;
  cadence?: number | null;
  velocity?: number | null;
  watts?: number | null;
}

// key -> [label, invert, highLabel, lowLabel]
// high is always red (top of legend), low is always green (bottom)
export type MetricKey = "hr" | "velocity" | "ele" | "cadence" | "watts";
export const METRIC_DEFS: Record<MetricKey, [string, boolean, string, string]> = {
  hr: ["Heart Rate", false, "High HR", "Low HR"],
  velocity: ["Pace", true, "Slow", "Fast"], // invert: fast (high vel) = green
  ele: ["Elevation", false, "High Elev.", "Low Elev."],
  cadence: ["Cadence", false, "High Cadence", "Low Cadence"],
  watts: ["Power", false, "High Power", "Low Power"],
};

// Endpoints of the metric gradient, derived once from the shared GRAD_* tokens so
// the coloured line and the Legend's CSS gradient can never drift apart.
function hexToRgb(h: string): [number, number, number] {
  const n = parseInt(h.slice(1), 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}
const GRAD_LOW_RGB = hexToRgb(GRAD_LOW);
const GRAD_MID_RGB = hexToRgb(GRAD_MID);
const GRAD_HIGH_RGB = hexToRgb(GRAD_HIGH);

// Green/low (0.0) -> Yellow/mid (0.5) -> Red/high (1.0)
export function gradientColor(t: number): string {
  t = Math.max(0, Math.min(1, t));
  let r: number, g: number, b: number;
  if (t <= 0.5) {
    const s = t * 2;
    r = Math.round(GRAD_LOW_RGB[0] + s * (GRAD_MID_RGB[0] - GRAD_LOW_RGB[0]));
    g = Math.round(GRAD_LOW_RGB[1] + s * (GRAD_MID_RGB[1] - GRAD_LOW_RGB[1]));
    b = Math.round(GRAD_LOW_RGB[2] + s * (GRAD_MID_RGB[2] - GRAD_LOW_RGB[2]));
  } else {
    const s = (t - 0.5) * 2;
    r = Math.round(GRAD_MID_RGB[0] + s * (GRAD_HIGH_RGB[0] - GRAD_MID_RGB[0]));
    g = Math.round(GRAD_MID_RGB[1] + s * (GRAD_HIGH_RGB[1] - GRAD_MID_RGB[1]));
    b = Math.round(GRAD_MID_RGB[2] + s * (GRAD_HIGH_RGB[2] - GRAD_MID_RGB[2]));
  }
  const hex = (n: number) => n.toString(16).padStart(2, "0");
  return `#${hex(r)}${hex(g)}${hex(b)}`;
}

export function norm(val: number, lo: number, hi: number, invert = false): number {
  if (hi === lo) return 0.5;
  const t = (val - lo) / (hi - lo);
  return invert ? 1.0 - t : t;
}

/** Build colored route polyline segments (each segment its own colour). */
export function coloredSegments(points: OverlayPoint[], metric: MetricKey, invert: boolean): PolyLineSpec[] {
  let valid = points.filter(
    (p) => p.lat != null && p.lon != null && (p as unknown as Record<string, unknown>)[metric] != null,
  );
  if (valid.length < 2) return [];
  // Downsample for performance — keep the last point to preserve route end
  if (valid.length > MAX_ROUTE_SEGMENTS + 1) {
    const step = valid.length / MAX_ROUTE_SEGMENTS;
    const sampled: OverlayPoint[] = [];
    for (let i = 0; i < MAX_ROUTE_SEGMENTS; i++) sampled.push(valid[Math.floor(i * step)]);
    sampled.push(valid[valid.length - 1]);
    valid = sampled;
  }
  const values = valid.map((p) => Number((p as unknown as Record<string, unknown>)[metric]));
  const lo = Math.min(...values);
  const hi = Math.max(...values);
  const segs: PolyLineSpec[] = [];
  for (let i = 0; i < valid.length - 1; i++) {
    segs.push({
      coords: [
        [valid[i].lat as number, valid[i].lon as number],
        [valid[i + 1].lat as number, valid[i + 1].lon as number],
      ],
      color: gradientColor(norm(values[i], lo, hi, invert)),
      weight: 5,
      opacity: 0.92,
    });
  }
  return segs;
}

export function plainRoute(points: OverlayPoint[]): PolyLineSpec[] {
  const valid = points.filter((p) => p.lat != null && p.lon != null);
  if (valid.length < 2) return [];
  return [
    {
      coords: valid.map((p) => [p.lat as number, p.lon as number]),
      color: ACCENT,
      weight: 4,
      opacity: 0.9,
    },
  ];
}

export function Legend({ highLabel, lowLabel }: { highLabel: string; lowLabel: string }) {
  return (
    <div className="flex flex-col items-center gap-1.5 pt-12">
      <span className="text-[10px] text-center" style={{ color: TEXT_MUTED }}>
        {highLabel}
      </span>
      <div
        className="rounded"
        style={{
          width: 16,
          height: 110,
          background: `linear-gradient(to bottom,${GRAD_HIGH},${GRAD_MID},${GRAD_LOW})`,
        }}
      />
      <span className="text-[10px] text-center" style={{ color: TEXT_MUTED }}>
        {lowLabel}
      </span>
    </div>
  );
}
