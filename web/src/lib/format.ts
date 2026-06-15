// Unit / date formatting + polyline decoding (the bits ui/*.py did in Python).

import polyline from "@mapbox/polyline";

export function km(meters?: number | null, digits = 1): string {
  if (meters == null) return "—";
  return `${(meters / 1000).toFixed(digits)} km`;
}

export function fmtKm(value?: number | null, digits = 1): string {
  if (value == null) return "—";
  return `${value.toFixed(digits)} km`;
}

export function fmtNum(value?: number | null, digits = 0): string {
  if (value == null) return "—";
  return value.toLocaleString(undefined, { maximumFractionDigits: digits });
}

/** Seconds → "1h 23m" / "23m 4s". */
export function duration(sec?: number | null): string {
  if (sec == null) return "—";
  const h = Math.floor(sec / 3600);
  const m = Math.floor((sec % 3600) / 60);
  const s = Math.floor(sec % 60);
  if (h) return `${h}h ${m}m`;
  if (m) return `${m}m ${s}s`;
  return `${s}s`;
}

export function hours(sec?: number | null, digits = 1): string {
  if (sec == null) return "—";
  return `${(sec / 3600).toFixed(digits)} h`;
}

/** Pace from m/s → "min/km" string. */
export function paceFromSpeed(metersPerSec?: number | null): string {
  if (!metersPerSec || metersPerSec <= 0) return "—";
  const secPerKm = 1000 / metersPerSec;
  const m = Math.floor(secPerKm / 60);
  const s = Math.round(secPerKm % 60);
  return `${m}:${String(s).padStart(2, "0")} /km`;
}

export function fmtDate(iso?: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleDateString();
}

/** Decode a Google-encoded polyline → [lat, lon][]. */
export function decodePolyline(encoded?: string | null): [number, number][] {
  if (!encoded) return [];
  try {
    return polyline.decode(encoded) as [number, number][];
  } catch {
    return [];
  }
}
