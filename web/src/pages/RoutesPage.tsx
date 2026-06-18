import { useMemo, useState } from "react";
import { useMutation } from "@tanstack/react-query";

import { PageHeader } from "../components/PageHeader";
import { MetricCard } from "../components/MetricCard";
import { Card, SectionLabel } from "../components/Card";
import { Spinner, ErrorBox, EmptyState } from "../components/Spinner";
import { RouteMap, type PolyLineSpec, type MarkerSpec } from "../components/RouteMap";
import { callTool } from "../lib/api";
import { CHART_COLORS, C_GREEN, C_RED } from "../theme/tokens";

// ── Preset locations (ui/routes_explorer.py PRESETS) ───────────────────────────
const PRESETS: Record<string, [number, number]> = {
  "KIT Campus Süd (Karlsruhe)": [49.013, 8.4093],
  "Karlsruhe Hauptbahnhof": [49.0069, 8.4037],
  "Heidelberg Altstadt": [49.4093, 8.6942],
  "Stuttgart Schlossplatz": [48.7784, 9.1797],
  "München Marienplatz": [48.1374, 11.5755],
  "Freiburg Münsterplatz": [47.9959, 7.8524],
};
const PRESET_NAMES = Object.keys(PRESETS);
const CUSTOM = "Custom coordinates";

const TOOLS = ["explore_trails", "plan_circular_route", "plan_route", "get_isochrone"] as const;
type ToolName = (typeof TOOLS)[number];

// ── Tool result shapes (confirmed via live backend curl) ───────────────────────
interface Waypoint {
  lat: number;
  lon: number;
  ele_m?: number;
}
interface ElevationStats {
  gain_m?: number;
  loss_m?: number;
  min_m?: number;
  max_m?: number;
}

interface RouteResult {
  profile?: string;
  distance_km?: number;
  actual_distance_km?: number;
  target_distance_km?: number;
  duration_min?: number;
  elevation?: ElevationStats;
  waypoints_count?: number;
  waypoints?: Waypoint[];
}

interface TrailBounds {
  min_lat?: number | null;
  min_lon?: number | null;
  max_lat?: number | null;
  max_lon?: number | null;
}
interface Trail {
  osm_id?: number;
  name: string;
  route_type?: string | null;
  distance?: string | null;
  network?: string | null;
  difficulty?: string | null;
  surface?: string | null;
  description?: string | null;
  website?: string | null;
  segments?: [number, number][][]; // [lon, lat] points
  bounds?: TrailBounds | null;
}
interface TrailsResult {
  search_centre?: { lat: number; lon: number };
  radius_km?: number;
  sport_type?: string;
  offset?: number;
  page_size?: number;
  has_more?: boolean;
  total_found?: number;
  trails?: Trail[];
}

interface IsochroneResult {
  profile?: string;
  range_type?: string;
  range_value?: number;
  range_label?: string;
  area_km2?: number;
  reach_factor?: number;
  centre?: { lat: number; lon: number };
  geometry?: GeoJSON.Polygon | GeoJSON.MultiPolygon;
}

type ToolResult = RouteResult | TrailsResult | IsochroneResult | { error?: string };

const SPORT_TYPES = ["hiking", "cycling", "running", "mtb"] as const;
const CIRCULAR_PROFILES = [
  "foot-hiking",
  "foot-walking",
  "cycling-regular",
  "cycling-mountain",
  "running",
] as const;
const PLAN_PROFILES = ["cycling-regular", "foot-hiking", "foot-walking", "cycling-mountain"] as const;
const ISO_PROFILES = ["cycling-regular", "foot-hiking", "foot-walking"] as const;

export function RoutesPage() {
  // ── Inputs ──────────────────────────────────────────────────────────────────
  const [presetChoice, setPresetChoice] = useState<string>(CUSTOM);
  const [lat, setLat] = useState(49.013);
  const [lon, setLon] = useState(8.4093);
  const [tool, setTool] = useState<ToolName>("explore_trails");

  // explore_trails params
  const [sportType, setSportType] = useState<(typeof SPORT_TYPES)[number]>("hiking");
  const [radiusKm, setRadiusKm] = useState(20);
  const [trailLimit, setTrailLimit] = useState(5);

  // plan_circular_route params
  const [circDistance, setCircDistance] = useState(10);
  const [circProfile, setCircProfile] = useState<(typeof CIRCULAR_PROFILES)[number]>("foot-hiking");

  // plan_route params
  const [endPreset, setEndPreset] = useState<string>(PRESET_NAMES[1]);
  const [planProfile, setPlanProfile] = useState<(typeof PLAN_PROFILES)[number]>("cycling-regular");

  // get_isochrone params
  const [rangeType, setRangeType] = useState<"time" | "distance">("time");
  const [isoMinutes, setIsoMinutes] = useState(30);
  const [isoMeters, setIsoMeters] = useState(10000);
  const [isoProfile, setIsoProfile] = useState<(typeof ISO_PROFILES)[number]>("cycling-regular");

  // ── Result state (mirrors st.session_state rex_result / rex_tool) ─────────────
  const [resultTool, setResultTool] = useState<ToolName | null>(null);
  const [result, setResult] = useState<ToolResult | null>(null);
  const [selIdx, setSelIdx] = useState(0);
  const [loadingMore, setLoadingMore] = useState(false);

  // Effective start coords (preset overrides custom).
  const effLat = presetChoice === CUSTOM ? lat : PRESETS[presetChoice][0];
  const effLon = presetChoice === CUSTOM ? lon : PRESETS[presetChoice][1];

  // ── Build args for the chosen tool (matches routes_explorer.py) ───────────────
  function buildArgs(): { name: string; args: Record<string, unknown> } {
    if (tool === "explore_trails") {
      return {
        name: "routes__explore_trails",
        args: {
          lat: effLat,
          lon: effLon,
          sport_type: sportType,
          radius_km: radiusKm,
          limit: trailLimit,
          offset: 0,
        },
      };
    }
    if (tool === "plan_circular_route") {
      return {
        name: "routes__plan_circular_route",
        args: { lat: effLat, lon: effLon, distance_km: circDistance, profile: circProfile },
      };
    }
    if (tool === "plan_route") {
      const [elat, elon] = PRESETS[endPreset];
      return {
        name: "routes__plan_route",
        args: { start_lat: effLat, start_lon: effLon, end_lat: elat, end_lon: elon, profile: planProfile },
      };
    }
    // get_isochrone
    const rangeValue = rangeType === "time" ? isoMinutes * 60 : isoMeters;
    return {
      name: "routes__get_isochrone",
      args: { lat: effLat, lon: effLon, range_type: rangeType, range_value: rangeValue, profile: isoProfile },
    };
  }

  const run = useMutation({
    mutationFn: async () => {
      const { name, args } = buildArgs();
      return callTool<ToolResult>(name, args);
    },
    onSuccess: (data) => {
      setResult(data);
      setResultTool(tool);
      setSelIdx(0);
    },
  });

  const runLabel =
    tool === "explore_trails"
      ? "Find trails 🔍"
      : tool === "plan_circular_route"
        ? "Plan loop 🔄"
        : tool === "plan_route"
          ? "Plan route 🛣️"
          : "Calculate reachability 🔵";

  // ── Load more (explore_trails pagination, merge by offset) ─────────────────────
  async function loadMore() {
    if (resultTool !== "explore_trails" || !result) return;
    const data = result as TrailsResult;
    const centre = data.search_centre ?? { lat: effLat, lon: effLon };
    const current = data.trails ?? [];
    const nextOffset = (data.offset ?? 0) + current.length;
    setLoadingMore(true);
    try {
      const next = await callTool<TrailsResult>("routes__explore_trails", {
        lat: centre.lat,
        lon: centre.lon,
        radius_km: data.radius_km ?? 15,
        sport_type: data.sport_type ?? "hiking",
        limit: data.page_size ?? 5,
        offset: nextOffset,
      });
      const merged: TrailsResult = {
        ...data,
        offset: next.offset ?? data.offset,
        page_size: next.page_size ?? data.page_size,
        has_more: next.has_more,
        trails: [...current, ...(next.trails ?? [])],
      };
      setResult(merged);
    } finally {
      setLoadingMore(false);
    }
  }

  const toolError = result && "error" in result && (result as { error?: string }).error;

  return (
    <div>
      <PageHeader
        title="🗺️ Routes Explorer"
        subtitle="Direct route tool testing — no chat or Strava required."
      />

      {/* ── Inputs ──────────────────────────────────────────────────────────── */}
      <Card className="mb-4 p-5">
        <div className="grid grid-cols-1 gap-4 md:grid-cols-5">
          {/* Starting point (3/5) */}
          <div className="md:col-span-3">
            <SectionLabel>Starting point</SectionLabel>
            <select
              className="fd-input mt-1 w-full"
              value={presetChoice}
              onChange={(e) => setPresetChoice(e.target.value)}
            >
              <option value={CUSTOM}>{CUSTOM}</option>
              {PRESET_NAMES.map((p) => (
                <option key={p} value={p}>
                  {p}
                </option>
              ))}
            </select>
            {presetChoice === CUSTOM ? (
              <div className="mt-2 grid grid-cols-2 gap-2">
                <label className="block">
                  <span className="text-xs text-text-muted">Latitude</span>
                  <input
                    type="number"
                    step={0.001}
                    value={lat}
                    onChange={(e) => setLat(parseFloat(e.target.value))}
                    className="fd-input mt-1 w-full"
                  />
                </label>
                <label className="block">
                  <span className="text-xs text-text-muted">Longitude</span>
                  <input
                    type="number"
                    step={0.001}
                    value={lon}
                    onChange={(e) => setLon(parseFloat(e.target.value))}
                    className="fd-input mt-1 w-full"
                  />
                </label>
              </div>
            ) : (
              <p className="mt-2 text-xs text-text-muted">
                📍 {effLat.toFixed(4)}°N, {effLon.toFixed(4)}°E
              </p>
            )}
          </div>

          {/* Funktion picker (2/5) */}
          <div className="md:col-span-2">
            <SectionLabel>Funktion</SectionLabel>
            <select
              className="fd-input mt-1 w-full"
              value={tool}
              onChange={(e) => setTool(e.target.value as ToolName)}
            >
              {TOOLS.map((t) => (
                <option key={t} value={t}>
                  {t}
                </option>
              ))}
            </select>
          </div>
        </div>

        {/* ── Tool-specific params ──────────────────────────────────────────── */}
        <div className="mt-4">
          {tool === "explore_trails" && (
            <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
              <label className="block">
                <span className="text-xs text-text-muted">Sport type</span>
                <select
                  className="fd-input mt-1 w-full"
                  value={sportType}
                  onChange={(e) => setSportType(e.target.value as (typeof SPORT_TYPES)[number])}
                >
                  {SPORT_TYPES.map((s) => (
                    <option key={s} value={s}>
                      {s}
                    </option>
                  ))}
                </select>
              </label>
              <Slider label={`Radius (km): ${radiusKm}`} min={5} max={50} value={radiusKm} onChange={setRadiusKm} />
              <Slider
                label={`Trails per page: ${trailLimit}`}
                min={3}
                max={10}
                value={trailLimit}
                onChange={setTrailLimit}
              />
            </div>
          )}

          {tool === "plan_circular_route" && (
            <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
              <Slider
                label={`Target distance (km): ${circDistance}`}
                min={3}
                max={80}
                value={circDistance}
                onChange={setCircDistance}
              />
              <label className="block">
                <span className="text-xs text-text-muted">Profile</span>
                <select
                  className="fd-input mt-1 w-full"
                  value={circProfile}
                  onChange={(e) => setCircProfile(e.target.value as (typeof CIRCULAR_PROFILES)[number])}
                >
                  {CIRCULAR_PROFILES.map((p) => (
                    <option key={p} value={p}>
                      {p}
                    </option>
                  ))}
                </select>
              </label>
            </div>
          )}

          {tool === "plan_route" && (
            <div>
              <div className="text-sm font-semibold text-text-primary">Destination</div>
              <div className="mt-2 grid grid-cols-1 gap-4 md:grid-cols-2">
                <label className="block">
                  <span className="text-xs text-text-muted">Destination preset</span>
                  <select
                    className="fd-input mt-1 w-full"
                    value={endPreset}
                    onChange={(e) => setEndPreset(e.target.value)}
                  >
                    {PRESET_NAMES.map((p) => (
                      <option key={p} value={p}>
                        {p}
                      </option>
                    ))}
                  </select>
                </label>
                <div className="flex items-end">
                  <p className="text-xs text-text-muted">
                    📍 {PRESETS[endPreset][0].toFixed(4)}°N, {PRESETS[endPreset][1].toFixed(4)}°E
                  </p>
                </div>
              </div>
              <label className="mt-4 block md:w-1/2">
                <span className="text-xs text-text-muted">Profile</span>
                <select
                  className="fd-input mt-1 w-full"
                  value={planProfile}
                  onChange={(e) => setPlanProfile(e.target.value as (typeof PLAN_PROFILES)[number])}
                >
                  {PLAN_PROFILES.map((p) => (
                    <option key={p} value={p}>
                      {p}
                    </option>
                  ))}
                </select>
              </label>
            </div>
          )}

          {tool === "get_isochrone" && (
            <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
              <label className="block">
                <span className="text-xs text-text-muted">Type</span>
                <select
                  className="fd-input mt-1 w-full"
                  value={rangeType}
                  onChange={(e) => setRangeType(e.target.value as "time" | "distance")}
                >
                  <option value="time">time</option>
                  <option value="distance">distance</option>
                </select>
              </label>
              {rangeType === "time" ? (
                <Slider
                  label={`Minutes: ${isoMinutes} = ${isoMinutes * 60} s`}
                  min={5}
                  max={120}
                  value={isoMinutes}
                  onChange={setIsoMinutes}
                />
              ) : (
                <Slider
                  label={`Distance (m): ${isoMeters}`}
                  min={1000}
                  max={30000}
                  step={500}
                  value={isoMeters}
                  onChange={setIsoMeters}
                />
              )}
              <label className="block">
                <span className="text-xs text-text-muted">Profile</span>
                <select
                  className="fd-input mt-1 w-full"
                  value={isoProfile}
                  onChange={(e) => setIsoProfile(e.target.value as (typeof ISO_PROFILES)[number])}
                >
                  {ISO_PROFILES.map((p) => (
                    <option key={p} value={p}>
                      {p}
                    </option>
                  ))}
                </select>
              </label>
            </div>
          )}
        </div>

        {/* ── Run button ──────────────────────────────────────────────────────── */}
        <div className="mt-5 border-t border-border pt-4">
          <button className="fd-btn-primary w-full" onClick={() => run.mutate()} disabled={run.isPending}>
            {run.isPending ? `Calling ${tool}…` : runLabel}
          </button>
        </div>
      </Card>

      {/* ── Status ──────────────────────────────────────────────────────────── */}
      {run.isPending && <Spinner label={`Calling ${tool}…`} />}
      {run.isError && <ErrorBox message={`Error: ${String(run.error)}`} />}
      {toolError && <ErrorBox message={`Error: ${toolError}`} />}

      {/* ── Results ─────────────────────────────────────────────────────────── */}
      {result && resultTool && !toolError && (
        <div className="space-y-4">
          {(resultTool === "plan_route" || resultTool === "plan_circular_route") && (
            <RouteResultView tool={resultTool} data={result as RouteResult} />
          )}
          {resultTool === "explore_trails" && (
            <TrailsResultView
              data={result as TrailsResult}
              selIdx={selIdx}
              onSelect={setSelIdx}
              onLoadMore={loadMore}
              loadingMore={loadingMore}
            />
          )}
          {resultTool === "get_isochrone" && <IsochroneResultView data={result as IsochroneResult} />}

          {/* Rohdaten (JSON) */}
          <RawData data={result} />
        </div>
      )}
    </div>
  );
}

// ── Slider helper ──────────────────────────────────────────────────────────────
function Slider({
  label,
  min,
  max,
  value,
  onChange,
  step = 1,
}: {
  label: string;
  min: number;
  max: number;
  value: number;
  onChange: (v: number) => void;
  step?: number;
}) {
  return (
    <label className="block">
      <span className="text-xs text-text-muted">{label}</span>
      <input
        type="range"
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
        className="mt-2 w-full accent-accent"
      />
    </label>
  );
}

// ── plan_route / plan_circular_route ────────────────────────────────────────────
function RouteResultView({ tool, data }: { tool: ToolName; data: RouteResult }) {
  const waypoints = useMemo(() => data.waypoints ?? [], [data.waypoints]);
  const polylines: PolyLineSpec[] = useMemo(() => {
    if (!waypoints.length) return [];
    return [
      {
        coords: waypoints.map((wp) => [wp.lat, wp.lon] as [number, number]),
        color: "#f97316",
        weight: 5,
        opacity: 0.9,
      },
    ];
  }, [waypoints]);
  const markers: MarkerSpec[] = useMemo(() => {
    if (!waypoints.length) return [];
    const a = waypoints[0];
    const b = waypoints[waypoints.length - 1];
    return [
      { lat: a.lat, lon: a.lon, color: C_GREEN, label: "Start" },
      { lat: b.lat, lon: b.lon, color: C_RED, label: "Ziel" },
    ];
  }, [waypoints]);

  const distance = data.distance_km ?? data.actual_distance_km;
  const elev = data.elevation ?? {};
  const wpCount = data.waypoints_count ?? waypoints.length;
  void tool;

  return (
    <Card className="p-5">
      <div className="mb-4 grid grid-cols-2 gap-3 md:grid-cols-4">
        <MetricCard label="Distance" value={`${distance ?? "?"} km`} />
        <MetricCard label="Duration" value={`${data.duration_min ?? "?"} min`} />
        <MetricCard label="Elevation gain" value={`${elev.gain_m ?? "?"} m`} />
        <MetricCard label="Waypoints" value={wpCount} />
      </div>
      {waypoints.length ? (
        <RouteMap polylines={polylines} markers={markers} height={480} basemap="osm" />
      ) : (
        <EmptyState message="Keine Wegpunkte in der Antwort." />
      )}
    </Card>
  );
}

// ── explore_trails ──────────────────────────────────────────────────────────────
function TrailsResultView({
  data,
  selIdx,
  onSelect,
  onLoadMore,
  loadingMore,
}: {
  data: TrailsResult;
  selIdx: number;
  onSelect: (i: number) => void;
  onLoadMore: () => void;
  loadingMore: boolean;
}) {
  const trails = useMemo(() => data.trails ?? [], [data.trails]);
  const sel = Math.min(selIdx, Math.max(0, trails.length - 1));

  const polylines: PolyLineSpec[] = useMemo(() => {
    const out: PolyLineSpec[] = [];
    trails.forEach((trail, i) => {
      const isSel = i === sel;
      const color = CHART_COLORS[i % CHART_COLORS.length];
      const segments = trail.segments ?? [];
      for (const seg of segments) {
        // segments are [lon, lat] — RouteMap wants [lat, lon].
        out.push({
          coords: seg.map((pt) => [pt[1], pt[0]] as [number, number]),
          color,
          weight: isSel ? 5 : 2.5,
          opacity: isSel ? 0.95 : 0.5,
        });
      }
    });
    return out;
  }, [trails, sel]);

  const markers: MarkerSpec[] = useMemo(() => {
    const t = trails[sel];
    const b = t?.bounds;
    if (!b) return [];
    const clat = ((b.min_lat ?? 0) + (b.max_lat ?? 0)) / 2;
    const clon = ((b.min_lon ?? 0) + (b.max_lon ?? 0)) / 2;
    return [{ lat: clat, lon: clon, color: "#FF9800", label: t?.name }];
  }, [trails, sel]);

  if (!trails.length) {
    return (
      <Card className="p-5">
        <EmptyState message="Keine Trails gefunden" />
      </Card>
    );
  }

  const t = trails[sel];

  return (
    <Card className="p-5">
      <div className="mb-3 flex items-center justify-between gap-3">
        <span className="text-xs text-text-muted">
          Trails 1–{trails.length} shown{data.has_more ? "  ·  more available" : ""}
        </span>
      </div>

      {/* Radio list to select trail */}
      <div className="mb-4 space-y-1">
        {trails.map((tr, i) => {
          const color = CHART_COLORS[i % CHART_COLORS.length];
          return (
            <label
              key={tr.osm_id ?? i}
              className={`flex cursor-pointer items-center gap-2 rounded-md px-2 py-1.5 text-sm transition-colors ${
                i === sel ? "bg-bg-surface text-text-primary" : "text-text-muted hover:text-text-primary"
              }`}
            >
              <input
                type="radio"
                name="trail-select"
                checked={i === sel}
                onChange={() => onSelect(i)}
                className="accent-accent"
              />
              <span className="inline-block h-2.5 w-2.5 rounded-full" style={{ background: color }} />
              <span>
                {tr.name} ({tr.distance ?? "?"} km)
              </span>
            </label>
          );
        })}
      </div>

      <RouteMap polylines={polylines} markers={markers} height={480} basemap="osm" />

      {/* Selected trail details */}
      <div className="mt-4 grid grid-cols-3 gap-3">
        <MetricCard label="Distanz" value={`${t.distance ?? "?"} km`} />
        <MetricCard label="Typ" value={t.route_type ?? "?"} />
        <MetricCard label="Netzwerk" value={t.network ?? "?"} />
      </div>
      {t.description && <p className="mt-2 text-xs text-text-muted">{t.description}</p>}
      {t.website && (
        <p className="mt-1 text-xs text-text-muted">
          Mehr Infos:{" "}
          <a href={t.website} target="_blank" rel="noreferrer" className="text-accent hover:underline">
            {t.website}
          </a>
        </p>
      )}

      {/* Mehr laden / Load more */}
      {data.has_more && (
        <div className="mt-4">
          <button className="fd-btn-secondary" onClick={onLoadMore} disabled={loadingMore}>
            {loadingMore ? "Loading next page…" : "Load more ▶"}
          </button>
        </div>
      )}
    </Card>
  );
}

// ── get_isochrone ────────────────────────────────────────────────────────────────
function IsochroneResultView({ data }: { data: IsochroneResult }) {
  const geometry = data.geometry;
  const centre = data.centre;

  const polygons: GeoJSON.Feature[] = useMemo(() => {
    if (!geometry) return [];
    return [{ type: "Feature", geometry, properties: {} }];
  }, [geometry]);
  const markers: MarkerSpec[] = centre
    ? [{ lat: centre.lat, lon: centre.lon, color: "#1E96FF", label: "Start" }]
    : [];

  return (
    <Card className="p-5">
      <div className="mb-4 grid grid-cols-2 gap-3">
        <MetricCard label="Reachable area" value={`${data.area_km2 ?? "?"} km²`} />
        <MetricCard label="Label" value={data.range_label ?? "?"} />
      </div>
      {geometry && centre ? (
        <RouteMap polygons={polygons} markers={markers} height={480} basemap="osm" />
      ) : (
        <EmptyState message="No isochrone data." />
      )}
    </Card>
  );
}

// ── Rohdaten (JSON) expander ─────────────────────────────────────────────────────
function RawData({ data }: { data: ToolResult }) {
  const [open, setOpen] = useState(false);

  const display = useMemo(() => {
    const d: Record<string, unknown> = { ...(data as Record<string, unknown>) };
    const wps = d["waypoints"] as unknown[] | undefined;
    if (Array.isArray(wps) && wps.length > 10) {
      d["waypoints"] = wps.slice(0, 5);
      d["waypoints_truncated"] = `… ${wps.length - 5} weitere`;
    }
    const trails = d["trails"] as Trail[] | undefined;
    if (Array.isArray(trails)) {
      d["trails"] = trails.map((t) => {
        const { segments: _segments, ...rest } = t;
        void _segments;
        return rest;
      });
    }
    return d;
  }, [data]);

  return (
    <Card className="p-4">
      <button
        className="flex w-full items-center gap-2 text-left text-sm font-medium text-text-primary"
        onClick={() => setOpen((o) => !o)}
      >
        <span className="text-text-muted">{open ? "▼" : "▶"}</span>
        Rohdaten (JSON)
      </button>
      {open && (
        <pre className="mt-3 max-h-96 overflow-auto rounded-lg border border-border bg-bg-surface p-3 text-xs text-text-muted">
          {JSON.stringify(display, null, 2)}
        </pre>
      )}
    </Card>
  );
}
