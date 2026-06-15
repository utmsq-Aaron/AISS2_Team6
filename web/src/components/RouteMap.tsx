import maplibregl from "maplibre-gl";
import { useEffect, useRef } from "react";

import { ACCENT, DARK_MAP_ATTR, DARK_MAP_TILES } from "../theme/tokens";

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
}

interface RouteMapProps {
  polylines?: PolyLineSpec[];
  markers?: MarkerSpec[];
  polygons?: GeoJSON.Feature[]; // already in [lon, lat] GeoJSON order
  height?: number;
  basemap?: "dark" | "osm";
  className?: string;
}

function rasterStyle(basemap: "dark" | "osm"): maplibregl.StyleSpecification {
  const tiles =
    basemap === "osm"
      ? ["https://a.tile.openstreetmap.org/{z}/{x}/{y}.png"]
      : [DARK_MAP_TILES];
  return {
    version: 8,
    sources: {
      base: { type: "raster", tiles, tileSize: 256, attribution: DARK_MAP_ATTR },
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

export function RouteMap({
  polylines = [],
  markers = [],
  polygons = [],
  height = 420,
  basemap = "dark",
  className = "",
}: RouteMapProps) {
  const container = useRef<HTMLDivElement>(null);
  const map = useRef<maplibregl.Map | null>(null);
  const markerObjs = useRef<maplibregl.Marker[]>([]);

  // Create the map once.
  useEffect(() => {
    if (!container.current || map.current) return;
    map.current = new maplibregl.Map({
      container: container.current,
      style: rasterStyle(basemap),
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

    const apply = () => {
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
          paint: { "fill-color": "#1E96FF", "fill-opacity": 0.2 },
        });
        m.addLayer({
          id: "polys-line",
          type: "line",
          source: "polys",
          paint: { "line-color": "#0050AA", "line-width": 2 },
        });
      }

      // ── Markers (recreated each update) ──
      markerObjs.current.forEach((mk) => mk.remove());
      markerObjs.current = markers.map((mk) => {
        const el = document.createElement("div");
        el.style.cssText =
          `width:14px;height:14px;border-radius:50%;border:2px solid #fff;` +
          `background:${mk.color ?? ACCENT};box-shadow:0 0 0 2px rgba(0,0,0,.4)`;
        const marker = new maplibregl.Marker({ element: el }).setLngLat([mk.lon, mk.lat]);
        if (mk.label) marker.setPopup(new maplibregl.Popup({ offset: 12 }).setText(mk.label));
        return marker.addTo(m);
      });

      // ── Fit bounds to everything ──
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

    if (m.isStyleLoaded()) apply();
    else m.once("load", apply);
  }, [polylines, markers, polygons]);

  return (
    <div
      ref={container}
      className={`overflow-hidden rounded-card border border-border ${className}`}
      style={{ height }}
    />
  );
}
