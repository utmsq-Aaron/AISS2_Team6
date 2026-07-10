import { useEffect, useState } from "react";

import { callTool } from "../../lib/api";
import { C_GREEN, C_RED } from "../../theme/tokens";
import { Card } from "../Card";
import { MetricCard } from "../MetricCard";
import { RouteMap } from "../RouteMap";
import type { MarkerSpec, PolyLineSpec } from "../RouteMap";

// Mirror of ui/chat.py `_render_route_map` (and core/route_render.py, used by the
// Telegram bridge). Handles the tools the orchestrator surfaces via trace.route_data
// (see core.agent_trace.ROUTE_TOOLS): plan_route, plan_circular_route and
// plan_park_loop (single routes), explore_trails (selection + pagination),
// get_isochrone, and an activity's recorded GPS track (get_activity_streams /
// get_activity_gps_track) — the last of which Telegram already drew but the web
// dropped, so the same run/ride map now renders in both.

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

/** A point of interest (café, shop, …) found by the agent's place searches,
 *  enriched with any maps_place_details the agent also fetched. */
export interface Poi {
  lat: number;
  lon: number;
  label: string;
  placeId?: string;
  rating?: number;
  ratingCount?: number;
  openNow?: boolean;
  hours?: string[];
  phone?: string;
  website?: string;
}

/** Pull POIs (place searches + fetched details) out of the trace's tool calls. */
export function extractPois(trace: Record<string, unknown>): Poi[] {
  const calls = (trace?.tool_calls as Array<Record<string, unknown>> | undefined) ?? [];
  const pois: Poi[] = [];
  const byId = new Map<string, Poi>();
  for (const c of calls) {
    const tool = (c?.tool as string) || "";
    if (c?.error) continue;
    let data: Record<string, unknown>;
    try {
      data = typeof c.result === "string" ? JSON.parse(c.result) : (c.result as never);
    } catch {
      continue;
    }
    if (tool.endsWith("maps_search_places") || tool.endsWith("maps_search_along_route")) {
      for (const p of (data?.places ?? []) as Array<Record<string, unknown>>) {
        const lat = p?.lat as number | undefined;
        const lon = p?.lon as number | undefined;
        if (lat == null || lon == null) continue;
        const id = (p.place_id as string) ?? `${lat},${lon}`;
        if (byId.has(id)) continue;
        const name = (p.name as string) || "Place";
        const address = (p.address as string) || "";
        const poi: Poi = {
          lat,
          lon,
          label: address ? `${name} · ${address}` : name,
          placeId: p.place_id as string | undefined,
          rating: p.rating as number | undefined,
          ratingCount: p.rating_count as number | undefined,
          openNow: p.open_now as boolean | undefined,
        };
        byId.set(id, poi);
        pois.push(poi);
      }
    } else if (tool.endsWith("maps_place_details")) {
      const id = data?.place_id as string | undefined;
      const poi = id ? byId.get(id) : undefined;
      if (!poi) continue;
      poi.rating = (data.rating as number | undefined) ?? poi.rating;
      poi.ratingCount = (data.rating_count as number | undefined) ?? poi.ratingCount;
      poi.openNow = (data.open_now as boolean | undefined) ?? poi.openNow;
      poi.hours = (data.opening_hours as string[] | undefined) ?? poi.hours;
      poi.phone = (data.phone as string | undefined) ?? poi.phone;
      poi.website = (data.website as string | undefined) ?? poi.website;
    }
  }
  return pois;
}

const esc = (s: unknown) =>
  String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]!,
  );

/** Build the (escaped) popup HTML for a POI pin: name, address, rating, hours. */
function poiPopupHtml(p: Poi, distM: number): string {
  const [name, ...rest] = p.label.split(" · ");
  const address = rest.join(" · ");
  // Google's weekdayDescriptions start on Monday; JS getDay() starts on Sunday.
  const today = p.hours?.[(new Date().getDay() + 6) % 7];
  const lines = [
    `<strong>${esc(name)}</strong>`,
    address ? `<span style="opacity:.75">${esc(address)}</span>` : "",
    p.rating != null ? `★ ${esc(p.rating)}${p.ratingCount != null ? ` (${esc(p.ratingCount)})` : ""}` : "",
    today ? esc(today) : "",
    p.openNow != null ? (p.openNow ? "Open now" : "Currently closed") : "",
    `~${Math.round(distM)} m from the route`,
  ].filter(Boolean);
  return lines.join("<br/>");
}

function haversineM(lat1: number, lon1: number, lat2: number, lon2: number): number {
  const rad = Math.PI / 180;
  const a =
    Math.sin(((lat2 - lat1) * rad) / 2) ** 2 +
    Math.cos(lat1 * rad) * Math.cos(lat2 * rad) * Math.sin(((lon2 - lon1) * rad) / 2) ** 2;
  return 12742000 * Math.asin(Math.sqrt(a));
}

export function RouteResult({
  routeData,
  pois = [],
  question = "",
}: {
  routeData: RouteData;
  pois?: Poi[];
  question?: string;
}) {
  const tool = routeData.tool || "";
  const data = (routeData.data || {}) as Record<string, unknown>;

  if (tool === "plan_route" || tool === "plan_circular_route" || tool === "plan_park_loop") {
    return <SingleRoute data={data} pois={pois} question={question} />;
  }
  if (tool === "explore_trails") {
    return <TrailSelection initial={data as TrailsData} />;
  }
  if (tool === "get_isochrone") {
    return <Isochrone data={data} />;
  }
  if (tool === "get_activity_streams" || tool === "get_activity_gps_track") {
    return <ActivityTrack data={data} />;
  }
  return null;
}

// ── Single route (plan_route / plan_circular_route) ───────────────────────────
function SingleRoute({
  data,
  pois = [],
  question = "",
}: {
  data: Record<string, unknown>;
  pois?: Poi[];
  question?: string;
}) {
  // "Route with detour": planning a variant through a chosen POI replaces the shown
  // route locally (the original stays one click away).
  const [alt, setAlt] = useState<{ data: Record<string, unknown>; via: string } | null>(null);
  const [planning, setPlanning] = useState<string | null>(null);

  // Personalised duration: ORS's duration_min is the PROFILE's pace — walking
  // speed for all foot routes. For a jogging/cycling request, estimate from the
  // user's own recent Strava pace instead (median avg speed of that sport).
  // The sport comes from the tool's requested_profile AND the user's own words —
  // the agent often normalises "jogging" to the ORS profile "foot-walking"
  // before calling the tool, so the question text is the more reliable signal.
  const requested = (
    ((data.requested_profile as string) ?? (data.profile as string)) || ""
  ).toLowerCase();
  const q = (question || "").toLowerCase();
  const sport: "Run" | "Ride" | null =
    /run|jog/.test(requested) || /jogg|joggen|lauf|läuf|run\b|rennen/.test(q)
      ? "Run"
      : /cycl|bike|ride|mtb/.test(requested) || /\brad|fahrrad|bike|cycl|velo|mtb/.test(q)
        ? "Ride"
        : null;
  const [personalKmh, setPersonalKmh] = useState<number | null>(null);
  useEffect(() => {
    if (!sport) return;
    let cancelled = false;
    (async () => {
      try {
        const res = await callTool<{
          activities?: Array<{ type?: string; sport_type?: string; avg_speed_kmh?: number | null }>;
        }>("strava__get_activities", { limit: 30 });
        const speeds = (res?.activities ?? [])
          .filter((a) => (a.sport_type ?? a.type ?? "").includes(sport))
          .map((a) => a.avg_speed_kmh)
          .filter((v): v is number => v != null && v > 0)
          .sort((x, y) => x - y);
        if (!cancelled && speeds.length >= 2) {
          setPersonalKmh(speeds[Math.floor(speeds.length / 2)]);
        }
      } catch {
        /* Strava not connected — keep the ORS estimate */
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [sport]);

  const origWaypoints = (data.waypoints as Waypoint[] | undefined) ?? [];
  if (!origWaypoints.length) return null;
  const origCoords: [number, number][] = origWaypoints.map((wp) => [wp.lat, wp.lon]);

  const shown = alt?.data ?? data;
  const waypoints = (shown.waypoints as Waypoint[] | undefined) ?? origWaypoints;
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
      label: "Finish",
    },
  ];

  // POIs (cafés etc. the agent found) as extra pins — but ONLY those actually on
  // the route (≤500 m from the ORIGINAL track); a hit across town has nothing to
  // do with this route and is not shown at all. Capped so the map stays readable.
  const nearby = pois
    .map((p) => ({
      poi: p,
      dist: Math.min(...origCoords.map(([la, lo]) => haversineM(p.lat, p.lon, la, lo))),
    }))
    .filter((x) => x.dist <= 500)
    .sort((a, b) => a.dist - b.dist)
    .slice(0, 5);
  nearby.forEach(({ poi, dist }) =>
    markers.push({
      lat: poi.lat,
      lon: poi.lon,
      color: "#1E96FF",
      label: poi.label,
      html: poiPopupHtml(poi, dist),
    }),
  );

  const planVia = async (poi: Poi) => {
    setPlanning(poi.label);
    try {
      // Keep the WHOLE original route, detouring via the POI — not just start→POI.
      // Route through ~14 points sampled from the original track, with the sample
      // nearest the POI replaced by the POI itself. Dense enough that ORS follows
      // the original shape (length stays within ~1 %), and the route now passes
      // the café. (Fewer vias would let ORS shortcut the loop's meanders.)
      const inner = origCoords.slice(1, -1);
      const step = Math.max(1, Math.floor(inner.length / 14));
      const sampled = inner.filter((_, i) => i % step === 0).slice(0, 14);
      let vias: [number, number][];
      if (sampled.length) {
        const nearestIdx = sampled.reduce(
          (best, c, i) =>
            haversineM(poi.lat, poi.lon, c[0], c[1]) <
            haversineM(poi.lat, poi.lon, sampled[best][0], sampled[best][1])
              ? i
              : best,
          0,
        );
        vias = sampled.map((c, i) => (i === nearestIdx ? [poi.lat, poi.lon] : c));
      } else {
        vias = [[poi.lat, poi.lon]]; // degenerate track — plain detour
      }
      const res = await callTool<Record<string, unknown>>("routes__plan_route", {
        start_lat: origCoords[0][0],
        start_lon: origCoords[0][1],
        end_lat: origCoords[origCoords.length - 1][0],
        end_lon: origCoords[origCoords.length - 1][1],
        waypoints: vias,
        profile: (data.profile as string) ?? undefined,
      });
      if (res && Array.isArray(res.waypoints) && res.waypoints.length) {
        setAlt({ data: res, via: poi.label });
      }
    } finally {
      setPlanning(null);
    }
  };

  // plan_route/plan_park_loop → distance_km; plan_circular_route → actual_distance_km
  const distanceKm =
    (shown.distance_km as number | undefined) ?? (shown.actual_distance_km as number | undefined);
  const durationMin = shown.duration_min as number | undefined;
  const elevation = shown.elevation as
    | { gain_m?: number; loss_m?: number }
    | undefined;

  return (
    <div className="mt-3 space-y-3">
      {alt && (
        <div className="flex items-center justify-between gap-3 text-xs text-text-muted">
          <span>Route with detour via {alt.via.split(" · ")[0]}</span>
          <button
            type="button"
            className="rounded-md border border-border bg-bg-surface px-2 py-1 hover:border-accent"
            onClick={() => setAlt(null)}
          >
            ← Original route
          </button>
        </div>
      )}
      <RouteMap polylines={polylines} markers={markers} height={420} basemap="osm" />
      <div className="grid grid-cols-3 gap-3">
        <MetricCard label="Distance" value={distanceKm != null ? `${distanceKm} km` : "?"} />
        <MetricCard
          label={
            personalKmh != null && distanceKm != null
              ? "Duration (your pace)"
              : sport === "Run"
                ? "Walking time"
                : "Duration"
          }
          value={
            personalKmh != null && distanceKm != null
              ? `~${Math.round((distanceKm / personalKmh) * 60)} min`
              : durationMin != null
                ? `${Math.round(durationMin)} min`
                : "?"
          }
        />
        <MetricCard
          label="Elevation gain"
          value={elevation?.gain_m != null ? `${Math.round(elevation.gain_m)} m` : "?"}
        />
      </div>
      {nearby.length > 0 && (
        <Card className="px-4 py-3">
          <div className="fd-label mb-2">Places along the route</div>
          <div className="space-y-2">
            {nearby.map(({ poi, dist }) => {
              const name = poi.label.split(" · ")[0];
              const active = alt?.via === poi.label;
              return (
                <div key={poi.label} className="flex items-center justify-between gap-3 text-sm">
                  <span className="min-w-0 truncate text-text-primary">
                    <span
                      className="mr-2 inline-block h-2.5 w-2.5 rounded-full align-middle"
                      style={{ background: "#1E96FF" }}
                    />
                    {name}
                    <span className="ml-2 text-xs text-text-muted">~{Math.round(dist)} m off route</span>
                  </span>
                  <button
                    type="button"
                    disabled={planning !== null || active}
                    onClick={() => planVia(poi)}
                    className="shrink-0 rounded-md border border-border bg-bg-surface px-2 py-1 text-xs text-text-primary hover:border-accent disabled:opacity-50"
                  >
                    {active ? "✓ on detour" : planning === poi.label ? "planning…" : "Route with detour"}
                  </button>
                </div>
              );
            })}
          </div>
        </Card>
      )}
    </div>
  );
}

// ── Activity GPS track (get_activity_streams / get_activity_gps_track) ─────────
// An activity's recorded GPS track: { points: [{lat, lon, …}] }. The Strava tool
// also returns an `activity` metadata block (name/distance/pace/HR); the Garmin
// one returns points only. Mirrors core/route_render.py's track branch.
interface TrackPoint {
  lat?: number | null;
  lon?: number | null;
}
interface ActivityMeta {
  name?: string;
  date?: string;
  distance_km?: number | null;
  pace_display?: string | null;
  avg_hr?: number | null;
}

function ActivityTrack({ data }: { data: Record<string, unknown> }) {
  const points = (data.points as TrackPoint[] | undefined) ?? [];
  const coords: [number, number][] = points
    .filter((p) => p.lat != null && p.lon != null)
    .map((p) => [p.lat as number, p.lon as number]);
  if (coords.length < 2) return null; // nothing drawable (e.g. an indoor activity)

  const polylines: PolyLineSpec[] = [
    { coords, color: "#f97316", weight: 4, opacity: 0.9 },
  ];
  const markers: MarkerSpec[] = [
    { lat: coords[0][0], lon: coords[0][1], color: C_GREEN, label: "Start" },
    {
      lat: coords[coords.length - 1][0],
      lon: coords[coords.length - 1][1],
      color: C_RED,
      label: "Finish",
    },
  ];

  const meta = (data.activity as ActivityMeta | undefined) ?? {};
  const distanceKm = meta.distance_km;
  const pace = meta.pace_display;
  const avgHr = meta.avg_hr;
  const hasMetrics = distanceKm != null || !!pace || avgHr != null;

  return (
    <div className="mt-3 space-y-3">
      {meta.name && (
        <div className="fd-label">
          {meta.name}
          {meta.date ? ` · ${meta.date}` : ""}
        </div>
      )}
      <RouteMap polylines={polylines} markers={markers} height={420} basemap="osm" />
      {hasMetrics && (
        <div className="grid grid-cols-3 gap-3">
          <MetricCard label="Distance" value={distanceKm != null ? `${distanceKm} km` : "?"} />
          <MetricCard label="Pace" value={pace ? `${pace} /km` : "?"} />
          <MetricCard label="Avg HR" value={avgHr != null ? `${Math.round(avgHr)} bpm` : "?"} />
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
        No trails found.
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
        <MetricCard label="Distance" value={`${selTrail?.distance ?? "?"} km`} />
        <MetricCard label="Type" value={selTrail?.route_type ?? "?"} />
        <MetricCard label="Network" value={selTrail?.network ?? "?"} />
      </div>
      {selTrail?.description && (
        <p className="text-xs text-text-muted">{selTrail.description}</p>
      )}
      {selTrail?.website && (
        <p className="text-xs text-text-muted">
          More info:{" "}
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
