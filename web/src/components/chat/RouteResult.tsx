import { useEffect, useState } from "react";

import { callTool } from "../../lib/api";
import { C_GREEN, C_RED } from "../../theme/tokens";
import { Card } from "../Card";
import { MetricCard } from "../MetricCard";
import { RouteMap } from "../RouteMap";
import type { MarkerSpec, PolyLineSpec } from "../RouteMap";

// Mirror of ui/chat.py `_render_route_map`. Handles the route tools that the
// orchestrator surfaces via trace.route_data: plan_route, plan_circular_route and
// plan_park_loop (all single routes), explore_trails (selection + pagination), and
// get_isochrone.

const TRAIL_COLORS = ["#f97316", "#1E96FF", "#00C864", "#C832C8", "#FFC800"];

interface Waypoint {
  lat: number;
  lon: number;
  ele_m?: number;
}
interface Bounds {
  min_lat?: number | null;
  max_lat?: number | null;
  min_lon?: number | null;
  max_lon?: number | null;
}
interface Trail {
  osm_id?: number;
  name: string;
  route_type?: string | null;
  distance?: number | null;
  network?: string | null;
  difficulty?: string | null;
  surface?: string | null;
  description?: string | null;
  website?: string | null;
  segments?: number[][][]; // [ [ [lon,lat], … ], … ]
  bounds?: Bounds | null;
}
interface Centre {
  lat: number;
  lon: number;
}
interface TrailsData {
  search_centre?: Centre;
  radius_km?: number;
  sport_type?: string;
  offset?: number;
  page_size?: number;
  has_more?: boolean;
  trails?: Trail[];
}

export interface RouteData {
  tool: string;
  data: Record<string, unknown>;
}

export function RouteResult({ routeData }: { routeData: RouteData }) {
  const tool = routeData.tool || "";
  const data = (routeData.data || {}) as Record<string, unknown>;

  if (tool === "plan_route" || tool === "plan_circular_route" || tool === "plan_park_loop") {
    return <SingleRoute data={data} />;
  }
  if (tool === "explore_trails") {
    return <TrailSelection initial={data as TrailsData} />;
  }
  if (tool === "get_isochrone") {
    return <Isochrone data={data} />;
  }
  return null;
}

// ── Single route (plan_route / plan_circular_route) ───────────────────────────
function SingleRoute({ data }: { data: Record<string, unknown> }) {
  const waypoints = (data.waypoints as Waypoint[] | undefined) ?? [];
  if (!waypoints.length) return null;

  const coords: [number, number][] = waypoints.map((wp) => [wp.lat, wp.lon]);
  const polylines: PolyLineSpec[] = [
    { coords, color: "#f97316", weight: 5, opacity: 0.9 },
  ];
  const markers: MarkerSpec[] = [
    { lat: coords[0][0], lon: coords[0][1], color: C_GREEN, label: "Start" },
    {
      lat: coords[coords.length - 1][0],
      lon: coords[coords.length - 1][1],
      color: C_RED,
      label: "Ziel",
    },
  ];

  const distanceKm = data.distance_km as number | undefined;
  const durationMin = data.duration_min as number | undefined;
  const elevation = data.elevation as
    | { gain_m?: number; loss_m?: number }
    | undefined;

  return (
    <div className="mt-3 space-y-3">
      <RouteMap polylines={polylines} markers={markers} height={420} basemap="osm" />
      {(distanceKm != null || durationMin != null || elevation) && (
        <div className="grid grid-cols-3 gap-3">
          <MetricCard
            label="Distanz"
            value={distanceKm != null ? `${distanceKm} km` : "?"}
          />
          <MetricCard
            label="Dauer"
            value={durationMin != null ? `${Math.round(durationMin)} min` : "?"}
          />
          <MetricCard
            label="Höhenmeter"
            value={elevation?.gain_m != null ? `${Math.round(elevation.gain_m)} m` : "?"}
          />
        </div>
      )}
    </div>
  );
}

// ── Trail selection (explore_trails) — selection + pagination ─────────────────
function TrailSelection({ initial }: { initial: TrailsData }) {
  // Pagination via local state — mirrors st.session_state cache + page index.
  const [pageData, setPageData] = useState<TrailsData>(initial);
  const [pageStart, setPageStart] = useState<number>(initial.offset ?? 0);
  const [selIdx, setSelIdx] = useState<number>(0);
  const [loading, setLoading] = useState(false);

  // Reset when a fresh tool result arrives.
  useEffect(() => {
    setPageData(initial);
    setPageStart(initial.offset ?? 0);
    setSelIdx(0);
  }, [initial]);

  const trails = pageData.trails ?? [];
  if (!trails.length) {
    return (
      <Card className="mt-3 px-4 py-3 text-sm text-text-muted">
        Keine Trails gefunden.
      </Card>
    );
  }

  const loadMore = async () => {
    const centre = pageData.search_centre;
    if (!centre) return;
    setLoading(true);
    try {
      const newOffset = pageStart + trails.length;
      const fresh = await callTool<TrailsData>("routes__explore_trails", {
        lat: centre.lat,
        lon: centre.lon,
        radius_km: pageData.radius_km,
        sport_type: pageData.sport_type,
        limit: pageData.page_size ?? 5,
        offset: newOffset,
      });
      if (fresh?.trails?.length) {
        setPageData(fresh);
        setPageStart(newOffset);
        setSelIdx(0);
      }
    } finally {
      setLoading(false);
    }
  };

  // Build polylines for all trails (selected drawn thicker), with bounding-box
  // polygon fallback when a trail has no GPS segments.
  const polylines: PolyLineSpec[] = [];
  const polygons: GeoJSON.Feature[] = [];
  trails.forEach((trail, i) => {
    const isSel = i === selIdx;
    const color = TRAIL_COLORS[i % TRAIL_COLORS.length];
    const weight = isSel ? 5 : 2.5;
    const opacity = isSel ? 0.95 : 0.55;
    const segments = trail.segments ?? [];
    if (segments.length) {
      segments.forEach((seg) => {
        // segments are [lon, lat] — RouteMap wants [lat, lon]
        const coords: [number, number][] = seg.map((pt) => [pt[1], pt[0]]);
        if (coords.length) polylines.push({ coords, color, weight, opacity });
      });
    } else {
      const b = trail.bounds;
      if (
        b &&
        b.min_lat != null &&
        b.max_lat != null &&
        b.min_lon != null &&
        b.max_lon != null
      ) {
        polygons.push({
          type: "Feature",
          geometry: {
            type: "Polygon",
            coordinates: [
              [
                [b.min_lon, b.min_lat],
                [b.max_lon, b.min_lat],
                [b.max_lon, b.max_lat],
                [b.min_lon, b.max_lat],
                [b.min_lon, b.min_lat],
              ],
            ],
          },
          properties: {},
        });
      }
    }
  });

  // Pin for the selected trail (centre of its bounds).
  const markers: MarkerSpec[] = [];
  const selTrail = trails[selIdx];
  const sb = selTrail?.bounds;
  if (sb) {
    const clat = ((sb.min_lat ?? 0) + (sb.max_lat ?? 0)) / 2;
    const clon = ((sb.min_lon ?? 0) + (sb.max_lon ?? 0)) / 2;
    markers.push({ lat: clat, lon: clon, color: "#f97316", label: selTrail.name });
  }

  const from = pageStart + 1;
  const to = pageStart + trails.length;

  return (
    <div className="mt-3 space-y-3">
      {/* Pagination caption + "Load more" */}
      <div className="flex items-center justify-between gap-3">
        <span className="text-xs text-text-muted">
          Trails {from}–{to} shown
          {pageData.has_more ? "  ·  more available" : ""}
        </span>
        {pageData.has_more && (
          <button
            type="button"
            disabled={loading}
            onClick={loadMore}
            className="rounded-md border border-border bg-bg-surface px-3 py-1 text-xs text-text-primary hover:border-accent disabled:opacity-50"
          >
            {loading ? "Loading…" : "Load more ▶"}
          </button>
        )}
      </div>

      {/* Select route radio list */}
      <Card className="px-4 py-3">
        <div className="fd-label mb-2">Select route:</div>
        <div className="space-y-1">
          {trails.map((t, i) => (
            <label
              key={t.osm_id ?? i}
              className="flex cursor-pointer items-center gap-2 text-sm text-text-primary"
            >
              <input
                type="radio"
                name="trail-sel"
                checked={i === selIdx}
                onChange={() => setSelIdx(i)}
                className="accent-accent"
              />
              <span
                className="inline-block h-2.5 w-2.5 rounded-full"
                style={{ background: TRAIL_COLORS[i % TRAIL_COLORS.length] }}
              />
              <span>
                {t.name}  ({t.distance ?? "?"} km)
              </span>
            </label>
          ))}
        </div>
      </Card>

      <RouteMap
        polylines={polylines}
        markers={markers}
        polygons={polygons}
        height={450}
        basemap="osm"
      />

      {/* Selected-trail metrics */}
      <div className="grid grid-cols-3 gap-3">
        <MetricCard label="Distanz" value={`${selTrail?.distance ?? "?"} km`} />
        <MetricCard label="Typ" value={selTrail?.route_type ?? "?"} />
        <MetricCard label="Netzwerk" value={selTrail?.network ?? "?"} />
      </div>
      {selTrail?.description && (
        <p className="text-xs text-text-muted">{selTrail.description}</p>
      )}
      {selTrail?.website && (
        <p className="text-xs text-text-muted">
          Mehr Infos:{" "}
          <a
            href={selTrail.website}
            target="_blank"
            rel="noreferrer noopener"
            className="text-accent underline"
          >
            {selTrail.website}
          </a>
        </p>
      )}
    </div>
  );
}

// ── Isochrone ─────────────────────────────────────────────────────────────────
function Isochrone({ data }: { data: Record<string, unknown> }) {
  const geometry = data.geometry as GeoJSON.Geometry | undefined;
  const centre = data.centre as Centre | undefined;
  if (!geometry || !centre) return null;

  const polygons: GeoJSON.Feature[] = [
    { type: "Feature", geometry, properties: {} },
  ];
  const markers: MarkerSpec[] = [
    { lat: centre.lat, lon: centre.lon, color: "#1E96FF", label: "Start" },
  ];

  return (
    <div className="mt-3">
      <RouteMap polygons={polygons} markers={markers} height={420} basemap="osm" />
    </div>
  );
}
