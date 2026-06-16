import { useEffect, useRef, useState } from "react";

import { RouteMap, type MarkerSpec, type PolyLineSpec } from "../RouteMap";
import { ACCENT, C_GREEN, C_RED, activityIcon } from "../../theme/tokens";
import { duration, fmtNum } from "../../lib/format";
import { syncRoute, type SyncActivity } from "../../lib/syncApi";

// One activity preview card — checkbox + icon/name + type·date + Strava badge,
// five metrics, and a lazily-loaded mini route map (or a "No GPS" placeholder).
// Mirrors ui/sync.py `_activity_card`.

interface ActivityCardProps {
  activity: SyncActivity;
  selected: boolean;
  onToggle: (id: number, checked: boolean) => void;
  // True = already on Strava, False = not yet, null = no check done (no badge).
  inStrava: boolean | null;
}

function StravaBadge({ inStrava }: { inStrava: boolean | null }) {
  if (inStrava === true) {
    return (
      <span
        className="ml-1.5 rounded-[10px] px-2 py-px text-[0.75rem]"
        style={{ background: "#22c55e22", color: "#22c55e", border: "1px solid #22c55e55" }}
      >
        ✅ Already on Strava
      </span>
    );
  }
  if (inStrava === false) {
    return (
      <span
        className="ml-1.5 rounded-[10px] px-2 py-px text-[0.75rem]"
        style={{ background: "#3b82f622", color: "#60a5fa", border: "1px solid #3b82f655" }}
      >
        ⬆️ Not on Strava
      </span>
    );
  }
  return null;
}

function MiniMetric({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div className="text-[10px] font-medium uppercase tracking-wide text-text-muted">{label}</div>
      <div className="mt-0.5 text-sm font-semibold text-text-primary">{value}</div>
    </div>
  );
}

export function ActivityCard({ activity, selected, onToggle, inStrava }: ActivityCardProps) {
  const {
    id,
    name,
    type,
    date,
    distance_km,
    duration_s,
    avg_hr,
    elevation_m,
    calories,
    start_lat,
    start_lon,
    has_polyline,
  } = activity;

  const hasGps = start_lat != null && start_lon != null;

  // Lazily fetch the route polyline once, when the card has GPS + polyline.
  const [coords, setCoords] = useState<[number, number][] | null>(null);
  const [routeLoaded, setRouteLoaded] = useState(false);
  const fetched = useRef(false);

  useEffect(() => {
    if (!hasGps || !has_polyline || fetched.current) return;
    fetched.current = true;
    let alive = true;
    syncRoute(id)
      .then((r) => {
        if (alive) setCoords(r.coords ?? []);
      })
      .catch(() => {
        if (alive) setCoords([]);
      })
      .finally(() => {
        if (alive) setRouteLoaded(true);
      });
    return () => {
      alive = false;
    };
  }, [id, hasGps, has_polyline]);

  const polylines: PolyLineSpec[] =
    coords && coords.length
      ? [{ coords, color: ACCENT, weight: 3, opacity: 0.95 }]
      : [];
  const markers: MarkerSpec[] =
    coords && coords.length
      ? [
          { lat: coords[0][0], lon: coords[0][1], color: C_GREEN },
          { lat: coords[coords.length - 1][0], lon: coords[coords.length - 1][1], color: C_RED },
        ]
      : [];

  return (
    <div className="fd-card p-4">
      <div className="grid grid-cols-1 gap-4 md:grid-cols-[3fr_1fr]">
        {/* ── Left: checkbox, name, meta, metrics ─────────────────────────── */}
        <div className="min-w-0">
          <label className="flex cursor-pointer items-start gap-2">
            <input
              type="checkbox"
              checked={selected}
              onChange={(e) => onToggle(id, e.target.checked)}
              className="mt-0.5 accent-accent"
            />
            <span className="font-semibold text-text-primary">
              {activityIcon(type)} {name}
            </span>
          </label>

          <div className="mt-1 flex flex-wrap items-center text-[0.8rem]" style={{ color: "#94a3b8" }}>
            <span>
              {type || "Activity"}  ·  {date}
            </span>
            <StravaBadge inStrava={inStrava} />
          </div>

          <div className="mt-3 grid grid-cols-3 gap-3 sm:grid-cols-5">
            <MiniMetric label="Distance" value={distance_km ? `${distance_km} km` : "—"} />
            <MiniMetric label="Duration" value={duration_s ? duration(duration_s) : "—"} />
            <MiniMetric label="Avg HR" value={avg_hr ? `${fmtNum(avg_hr)} bpm` : "—"} />
            <MiniMetric label="Elevation" value={elevation_m ? `${fmtNum(elevation_m)} m` : "—"} />
            <MiniMetric label="Calories" value={calories ? `${fmtNum(calories)} kcal` : "—"} />
          </div>
        </div>

        {/* ── Right: mini route map or No-GPS placeholder ─────────────────── */}
        <div>
          {hasGps ? (
            has_polyline && coords && coords.length ? (
              <RouteMap polylines={polylines} markers={markers} height={155} basemap="dark" />
            ) : has_polyline && !routeLoaded ? (
              <div
                className="flex items-center justify-center rounded-card border border-dashed border-border text-xs text-text-muted"
                style={{ height: 155 }}
              >
                Loading route…
              </div>
            ) : (
              // Has GPS start but no polyline (or empty route): show start marker.
              <RouteMap
                markers={[{ lat: start_lat as number, lon: start_lon as number, color: ACCENT }]}
                height={155}
                basemap="dark"
              />
            )
          ) : (
            <div
              className="flex items-center justify-center rounded-card border border-dashed border-border text-xs text-text-muted"
              style={{ height: 155 }}
            >
              No GPS
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
