"""Routes Explorer tab — direct route-tool access without chat or Strava."""

import json

import streamlit as st

from ui.shared import call_tool

# ── Preset locations ──────────────────────────────────────────────────────────

PRESETS = {
    "KIT Campus Süd (Karlsruhe)":  (49.0130, 8.4093),
    "Karlsruhe Hauptbahnhof":      (49.0069, 8.4037),
    "Heidelberg Altstadt":         (49.4093, 8.6942),
    "Stuttgart Schlossplatz":      (48.7784, 9.1797),
    "München Marienplatz":         (48.1374, 11.5755),
    "Freiburg Münsterplatz":       (47.9959, 7.8524),
}

_MAP_STYLE = "https://basemaps.cartocdn.com/gl/voyager-gl-style/style.json"
_TRAIL_COLORS = ["#FF6400", "#1E96FF", "#00C864", "#C832C8", "#FFC800"]


# ── Map rendering ─────────────────────────────────────────────────────────────

def _render_map(tool: str, data: dict, key: str) -> None:
    try:
        import folium
        from streamlit_folium import st_folium
    except ImportError:
        st.warning("folium nicht installiert.")
        return

    if tool in ("plan_route", "plan_circular_route"):
        waypoints = data.get("waypoints", [])
        if not waypoints:
            st.info("Keine Wegpunkte in der Antwort.")
            return
        coords = [[wp["lat"], wp["lon"]] for wp in waypoints]
        center = [sum(c[0] for c in coords) / len(coords),
                  sum(c[1] for c in coords) / len(coords)]
        m = folium.Map(location=center, zoom_start=13, tiles="OpenStreetMap")
        folium.PolyLine(coords, color="#FF6400", weight=5, opacity=0.9).add_to(m)
        folium.Marker(coords[0],  popup="Start", icon=folium.Icon(color="green", icon="play")).add_to(m)
        folium.Marker(coords[-1], popup="Ziel",  icon=folium.Icon(color="red",   icon="stop")).add_to(m)
        st_folium(m, height=480, use_container_width=True, key=key, returned_objects=[])

    elif tool == "explore_trails":
        trails = data.get("trails", [])
        if not trails:
            st.info("Keine Trails gefunden.")
            return

        names = [f"{t['name']}  ({t.get('distance') or '?'} km)" for t in trails]
        sel_idx = st.radio("Trail auswählen:", range(len(names)),
                           format_func=lambda i: names[i], key=f"sel_{key}")

        centre = data.get("search_centre", {})
        m = folium.Map(location=[centre.get("lat", 49.0), centre.get("lon", 8.4)],
                       zoom_start=10, tiles="OpenStreetMap")

        for i, trail in enumerate(trails):
            is_sel = (i == sel_idx)
            color  = _TRAIL_COLORS[i % len(_TRAIL_COLORS)]
            segments = trail.get("segments", [])

            if segments:
                for seg in segments:
                    folium.PolyLine(
                        [[pt[1], pt[0]] for pt in seg],
                        color=color, weight=5 if is_sel else 2.5,
                        opacity=0.95 if is_sel else 0.5,
                        tooltip=trail["name"],
                    ).add_to(m)
            else:
                b = trail.get("bounds")
                if b and None not in (b.get("min_lat"), b.get("max_lat"),
                                      b.get("min_lon"), b.get("max_lon")):
                    folium.Polygon(
                        [[b["min_lat"], b["min_lon"]], [b["min_lat"], b["max_lon"]],
                         [b["max_lat"], b["max_lon"]], [b["max_lat"], b["min_lon"]]],
                        color=color, weight=3 if is_sel else 1.5,
                        fill=True, fill_color=color,
                        fill_opacity=0.35 if is_sel else 0.1,
                        tooltip=trail["name"],
                    ).add_to(m)

            if is_sel:
                b = trail.get("bounds")
                if b:
                    clat = ((b.get("min_lat") or 0) + (b.get("max_lat") or 0)) / 2
                    clon = ((b.get("min_lon") or 0) + (b.get("max_lon") or 0)) / 2
                    folium.Marker([clat, clon], popup=trail["name"],
                                  icon=folium.Icon(color="orange", icon="map-marker")).add_to(m)

        st_folium(m, height=480, use_container_width=True, key=key, returned_objects=[])

        t = trails[sel_idx]
        c1, c2, c3 = st.columns(3)
        c1.metric("Distanz", f"{t.get('distance') or '?'} km")
        c2.metric("Typ", t.get("route_type") or "?")
        c3.metric("Netzwerk", t.get("network") or "?")
        if t.get("description"):
            st.caption(t["description"])
        if t.get("website"):
            st.caption(f"Mehr Infos: {t['website']}")

        # Load-more
        if data.get("has_more"):
            if st.button("Mehr laden ▶", key=f"more_{key}"):
                next_offset = data.get("offset", 0) + data.get("page_size", 5)
                with st.spinner("Lade nächste Seite…"):
                    new_raw = call_tool("routes__explore_trails", {
                        "lat":        centre.get("lat"),
                        "lon":        centre.get("lon"),
                        "radius_km":  data.get("radius_km", 15),
                        "sport_type": data.get("sport_type", "hiking"),
                        "limit":      data.get("page_size", 5),
                        "offset":     next_offset,
                    })
                    st.session_state["rex_result"] = json.loads(new_raw)
                    st.session_state["rex_tool"]   = "explore_trails"
                st.rerun()

    elif tool == "get_isochrone":
        geometry = data.get("geometry", {})
        centre   = data.get("centre", {})
        if not geometry or not centre:
            st.info("Keine Isochrone-Daten.")
            return
        m = folium.Map(location=[centre["lat"], centre["lon"]],
                       zoom_start=11, tiles="OpenStreetMap")
        folium.GeoJson(
            {"type": "Feature", "geometry": geometry, "properties": {}},
            style_function=lambda _: {"fillColor": "#1E96FF", "color": "#0050AA",
                                      "weight": 2, "fillOpacity": 0.20},
        ).add_to(m)
        folium.Marker([centre["lat"], centre["lon"]], popup="Start",
                      icon=folium.Icon(color="blue", icon="home")).add_to(m)
        st_folium(m, height=480, use_container_width=True, key=key, returned_objects=[])

    elif tool == "get_elevation_profile":
        elev = data.get("elevation", {})
        profile = data.get("profile", [])
        if profile:
            coords = [[p["lat"], p["lon"]] for p in profile]
            center = [sum(c[0] for c in coords) / len(coords),
                      sum(c[1] for c in coords) / len(coords)]
            m = folium.Map(location=center, zoom_start=13, tiles="OpenStreetMap")
            folium.PolyLine(coords, color="#9B59B6", weight=4, opacity=0.9).add_to(m)
            st_folium(m, height=480, use_container_width=True, key=key, returned_objects=[])
        st.json(elev)


# ── Summary metrics ───────────────────────────────────────────────────────────

def _render_metrics(tool: str, data: dict) -> None:
    if tool in ("plan_route", "plan_circular_route"):
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Distanz",      f"{data.get('distance_km') or data.get('actual_distance_km', '?')} km")
        c2.metric("Dauer",        f"{data.get('duration_min', '?')} min")
        elev = data.get("elevation", {})
        c3.metric("Höhengewinn",  f"{elev.get('gain_m', '?')} m")
        c4.metric("Wegpunkte",    data.get("waypoints_count", len(data.get("waypoints", []))))
    elif tool == "explore_trails":
        c1, c2, c3 = st.columns(3)
        c1.metric("Gefunden",  data.get("total_found", "?"))
        c2.metric("Angezeigt", len(data.get("trails", [])))
        c3.metric("Umkreis",   f"{data.get('radius_km', '?')} km")
    elif tool == "get_isochrone":
        c1, c2 = st.columns(2)
        c1.metric("Erreichbare Fläche", f"{data.get('area_km2', '?')} km²")
        c2.metric("Label", data.get("range_label", "?"))


# ── Main render ───────────────────────────────────────────────────────────────

def render_routes_explorer() -> None:
    st.markdown("### 🗺️ Routen Explorer")
    st.caption("Direkter Test der Route-Tools — kein Chat, kein Strava nötig.")

    # ── Inputs ────────────────────────────────────────────────────────────────
    col_loc, col_tool = st.columns([3, 2])

    with col_loc:
        preset_choice = st.selectbox("Startpunkt", ["Eigene Koordinaten"] + list(PRESETS.keys()))
        if preset_choice == "Eigene Koordinaten":
            lc, lnc = st.columns(2)
            lat = lc.number_input("Latitude",  value=49.0130, format="%.4f", step=0.001)
            lon = lnc.number_input("Longitude", value=8.4093,  format="%.4f", step=0.001)
        else:
            lat, lon = PRESETS[preset_choice]
            st.caption(f"📍 {lat:.4f}°N, {lon:.4f}°E")

    with col_tool:
        tool_choice = st.selectbox("Tool", [
            "explore_trails",
            "plan_circular_route",
            "plan_route",
            "get_isochrone",
        ])

    # ── Tool-specific params ──────────────────────────────────────────────────
    args: dict = {"lat": lat, "lon": lon}
    run_label = "Ausführen ▶"

    if tool_choice == "explore_trails":
        p1, p2, p3 = st.columns(3)
        args["sport_type"] = p1.selectbox("Sportart", ["hiking", "cycling", "running", "mtb"])
        args["radius_km"]  = p2.slider("Umkreis (km)", 5, 50, 20)
        args["limit"]      = p3.slider("Trails pro Seite", 3, 10, 5)
        args["offset"]     = 0
        run_label = "Trails suchen 🔍"

    elif tool_choice == "plan_circular_route":
        p1, p2 = st.columns(2)
        args["distance_km"] = p1.slider("Zieldistanz (km)", 3, 80, 10)
        args["profile"]     = p2.selectbox("Profil", [
            "foot-hiking", "foot-walking", "cycling-regular", "cycling-mountain", "running"
        ])
        run_label = "Loop planen 🔄"

    elif tool_choice == "plan_route":
        st.markdown("**Ziel**")
        ep1, ep2 = st.columns(2)
        end_preset = ep1.selectbox("Ziel-Preset", list(PRESETS.keys()), index=1)
        elat, elon = PRESETS[end_preset]
        ep2.caption(f"📍 {elat:.4f}°N, {elon:.4f}°E")
        args["end_lat"] = elat
        args["end_lon"] = elon
        args["profile"] = st.selectbox("Profil", [
            "cycling-regular", "foot-hiking", "foot-walking", "cycling-mountain"
        ])
        args["start_lat"] = lat
        args["start_lon"] = lon
        del args["lat"], args["lon"]
        run_label = "Route planen 🛣️"

    elif tool_choice == "get_isochrone":
        p1, p2, p3 = st.columns(3)
        args["range_type"]  = p1.selectbox("Typ", ["time", "distance"])
        if args["range_type"] == "time":
            minutes = p2.slider("Minuten", 5, 120, 30)
            args["range_value"] = minutes * 60
            p2.caption(f"{minutes} min = {minutes*60} s")
        else:
            args["range_value"] = p2.slider("Distanz (m)", 1000, 30000, 10000, 500)
        args["profile"] = p3.selectbox("Profil", [
            "cycling-regular", "foot-hiking", "foot-walking"
        ])
        run_label = "Erreichbarkeit berechnen 🔵"

    # ── Run button ────────────────────────────────────────────────────────────
    st.divider()
    if st.button(run_label, type="primary", use_container_width=True):
        with st.spinner(f"Rufe {tool_choice} auf…"):
            try:
                raw = call_tool(f"routes__{tool_choice}", args)
                result = json.loads(raw)
                st.session_state["rex_result"] = result
                st.session_state["rex_tool"]   = tool_choice
            except Exception as e:
                st.error(f"Fehler: {e}")
                st.session_state.pop("rex_result", None)

    # ── Results ───────────────────────────────────────────────────────────────
    if "rex_result" in st.session_state and "rex_tool" in st.session_state:
        tool = st.session_state["rex_tool"]
        data = st.session_state["rex_result"]

        st.divider()
        _render_metrics(tool, data)
        _render_map(tool, data, key=f"rex_{tool}")

        with st.expander("Raw JSON", expanded=False):
            # Trim waypoints for readability
            display = dict(data)
            if "waypoints" in display and len(display["waypoints"]) > 10:
                display = {**display,
                           "waypoints": display["waypoints"][:5],
                           "waypoints_truncated": f"… {len(data['waypoints'])-5} more"}
            if "trails" in display:
                display = {**display,
                           "trails": [{k: v for k, v in t.items() if k != "segments"}
                                      for t in display["trails"]]}
            st.json(display)
