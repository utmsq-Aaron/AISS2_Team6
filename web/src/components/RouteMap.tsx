import maplibregl from "maplibre-gl";
import { useEffect, useRef, useState } from "react";

import {
  ACCENT,
  DARK_MAP_ATTR,
  DARK_MAP_TILES,
  ISO_BLUE,
  ISO_BLUE_DARK,
  OSM_MAP_ATTR,
  OSM_MAP_TILES,
  SATELLITE_MAP_ATTR,
  SATELLITE_MAP_TILES,
  WHITE,
} from "../theme/tokens";

// A polyline given as [lat, lon][] (folium order) — converted to GeoJSON [lon, lat].
export interface PolyLineSpec {
  coords: [number, number][];
  color?: string;
  weight?: number;
  opacity?: number;
}
export interface MarkerSpec {
  lat: number;
  lon: number;
  color?: string;
  label?: string;
  /** Optional pre-escaped HTML for the popup (falls back to the plain-text label). */
  html?: string;
}

// The three selectable basemaps. `basemap` prop is the INITIAL value; the
// in-map switcher lets the user change it at runtime.
export type BasemapId = "dark" | "osm" | "satellite";

export const BASEMAPS: Record<BasemapId, { label: string; tiles: string; attr: string }> = {
  dark: { label: "Dark", tiles: DARK_MAP_TILES, attr: DARK_MAP_ATTR },
  osm: { label: "Map", tiles: OSM_MAP_TILES, attr: OSM_MAP_ATTR },
  satellite: { label: "Satellite", tiles: SATELLITE_MAP_TILES, attr: SATELLITE_MAP_ATTR },
};

function rasterStyle(b: BasemapId): maplibregl.StyleSpecification {
  const { tiles, attr } = BASEMAPS[b];
  return {
    version: 8,
    sources: {
      base: { type: "raster", tiles: [tiles], tileSize: 256, attribution: attr },
    },
    layers: [{ id: "base", type: "raster", source: "base" }],
  };
}

function lineFeatures(polylines: PolyLineSpec[]): GeoJSON.FeatureCollection {
  return {
    type: "FeatureCollection",
    features: polylines.map((p, i) => ({
      type: "Feature",
      id: i,
      properties: {
        color: p.color ?? ACCENT,
        weight: p.weight ?? 5,
        opacity: p.opacity ?? 0.9,
      },
      geometry: { type: "LineString", coordinates: p.coords.map(([lat, lon]) => [lon, lat]) },
    })),
  };
}

interface RouteMapProps {
  polylines?: PolyLineSpec[];
  markers?: MarkerSpec[];
  polygons?: GeoJSON.Feature[]; // already in [lon, lat] GeoJSON order
  height?: number;
  /** Initial basemap. The user can switch at runtime via the overlay control. */
  basemap?: BasemapId;
  /** Show the basemap switcher (Dark/Map/Satellite). Default true. */
  showBasemapSwitcher?: boolean;
  className?: string;
}

export function RouteMap({
  polylines = [],
  markers = [],
  polygons = [],
  height = 420,
  basemap = "dark",
  showBasemapSwitcher = true,
  className = "",
}: RouteMapProps) {
  const container = useRef<HTMLDivElement>(null);
  const map = useRef<maplibregl.Map | null>(null);
  const markerObjs = useRef<maplibregl.Marker[]>([]);
  // The data-sync logic, hoisted so the style-switch effect can re-run it
  // (setStyle drops added sources/layers). `fit` gates the fitBounds call so a
  // pure basemap switch doesn't re-frame the map.
  const applyRef = useRef<(fit: boolean) => void>();
  // Which basemap is currently shown. Seeded from the prop; user-switchable.
  const [active, setActive] = useState<BasemapId>(basemap);
  // Skip the style-switch effect on the initial render (map already built with it).
  const firstStyleRun = useRef(true);

  // Create the map once.
  useEffect(() => {
    if (!container.current || map.current) return;
    map.current = new maplibregl.Map({
      container: container.current,
      style: rasterStyle(active),
      center: [8.4, 48.0],
      zoom: 11,
      attributionControl: { compact: true },
    });
    map.current.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");
    return () => {
      map.current?.remove();
      map.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Sync data layers whenever inputs change.
  useEffect(() => {
    const m = map.current;
    if (!m) return;

    // add-if-missing so re-running after setStyle (which drops sources/layers)
    // restores everything. `fit` skips the reframe on pure style switches.
    const apply = (fit: boolean) => {
      // ── Lines ──
      const lines = lineFeatures(polylines);
      const lineSrc = m.getSource("lines") as maplibregl.GeoJSONSource | undefined;
      if (lineSrc) lineSrc.setData(lines);
      else {
        m.addSource("lines", { type: "geojson", data: lines });
        m.addLayer({
          id: "lines",
          type: "line",
          source: "lines",
          layout: { "line-cap": "round", "line-join": "round" },
          paint: {
            "line-color": ["get", "color"],
            "line-width": ["get", "weight"],
            "line-opacity": ["get", "opacity"],
          },
        });
      }

      // ── Polygons (isochrone) ──
      const polyFc: GeoJSON.FeatureCollection = { type: "FeatureCollection", features: polygons };
      const polySrc = m.getSource("polys") as maplibregl.GeoJSONSource | undefined;
      if (polySrc) polySrc.setData(polyFc);
      else {
        m.addSource("polys", { type: "geojson", data: polyFc });
        m.addLayer({
          id: "polys-fill",
          type: "fill",
          source: "polys",
          paint: { "fill-color": ISO_BLUE, "fill-opacity": 0.2 },
        });
        m.addLayer({
          id: "polys-line",
          type: "line",
          source: "polys",
          paint: { "line-color": ISO_BLUE_DARK, "line-width": 2 },
        });
      }

      // ── Markers (recreated each update) ──
      markerObjs.current.forEach((mk) => mk.remove());
      markerObjs.current = markers.map((mk) => {
        const el = document.createElement("div");
        el.style.cssText =
          `width:14px;height:14px;border-radius:50%;border:2px solid ${WHITE};` +
          `background:${mk.color ?? ACCENT};box-shadow:0 0 0 2px rgba(0,0,0,.4)`;
        const marker = new maplibregl.Marker({ element: el }).setLngLat([mk.lon, mk.lat]);
        if (mk.html) {
          marker.setPopup(new maplibregl.Popup({ offset: 12, maxWidth: "280px" }).setHTML(mk.html));
        } else if (mk.label) {
          marker.setPopup(new maplibregl.Popup({ offset: 12 }).setText(mk.label));
        }
        return marker.addTo(m);
      });

      // ── Fit bounds to everything (skipped on pure style switches) ──
      if (!fit) return;
      const bounds = new maplibregl.LngLatBounds();
      let any = false;
      polylines.forEach((p) => p.coords.forEach(([lat, lon]) => { bounds.extend([lon, lat]); any = true; }));
      markers.forEach((mk) => { bounds.extend([mk.lon, mk.lat]); any = true; });
      polygons.forEach((f) => {
        const g = f.geometry;
        if (g.type === "Polygon") g.coordinates.flat().forEach((c) => { bounds.extend(c as [number, number]); any = true; });
        if (g.type === "MultiPolygon") g.coordinates.flat(2).forEach((c) => { bounds.extend(c as [number, number]); any = true; });
      });
      if (any && !bounds.isEmpty()) m.fitBounds(bounds, { padding: 40, maxZoom: 15, duration: 400 });
    };

    applyRef.current = apply;
    if (m.isStyleLoaded()) apply(true);
    else m.once("load", () => apply(true));
  }, [polylines, markers, polygons]);

  // Swap the basemap raster when the user picks a different one. setStyle drops
  // the added sources/layers, so re-apply the data (without re-fitting bounds)
  // once the new style is ready.
  useEffect(() => {
    const m = map.current;
    if (!m) return;
    if (firstStyleRun.current) {
      firstStyleRun.current = false;
      return;
    }
    m.setStyle(rasterStyle(active));
    m.once("styledata", () => applyRef.current?.(false));
  }, [active]);

  return (
    <div className={`relative ${className}`}>
      {showBasemapSwitcher && (
        <div className="absolute left-2 top-2 z-10 flex gap-1 rounded-lg border border-border bg-bg-card/80 px-1 py-1 backdrop-blur">
          {(Object.keys(BASEMAPS) as BasemapId[]).map((id) => (
            <button
              key={id}
              type="button"
              onClick={() => setActive(id)}
              className={`rounded-md px-2 py-0.5 text-[11px] font-medium transition-colors ${
                active === id ? "bg-accent text-white" : "text-text-muted hover:text-text-primary"
              }`}
            >
              {BASEMAPS[id].label}
            </button>
          ))}
        </div>
      )}
      <div
        ref={container}
        className="overflow-hidden rounded-card border border-border"
        style={{ height }}
      />
    </div>
  );
}
