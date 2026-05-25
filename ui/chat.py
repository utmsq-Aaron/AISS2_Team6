"""Chat tab — AI sports analyst backed by a three-phase orchestrator.

Layout: messages fill a container that sits above st.chat_input, so the
input is always at the bottom (ChatGPT / Claude style).
"""

import json
from typing import Dict, Optional

import streamlit as st


@st.cache_resource(show_spinner=False)
def _get_orchestrator():
    from ui.orchestrator import FitDashOrchestrator
    return FitDashOrchestrator()


# ── Route map ────────────────────────────────────────────────────────────────

_TRAIL_COLORS = ["#FF6400", "#1E96FF", "#00C864", "#C832C8", "#FFC800"]


def _render_route_map(route_data: Dict, key_suffix: str = "") -> None:
    """Render a folium map below a chat message for route-tool results."""
    try:
        import folium
        from streamlit_folium import st_folium
    except ImportError:
        st.info("streamlit-folium nicht installiert: `pip install folium streamlit-folium`")
        return

    tool = route_data.get("tool", "")
    data = route_data.get("data", {})

    # ── Single route (plan_route / plan_circular_route) ──────────────────────
    if tool in ("plan_route", "plan_circular_route"):
        waypoints = data.get("waypoints", [])
        if not waypoints:
            return
        coords = [[wp["lat"], wp["lon"]] for wp in waypoints]
        center = [sum(c[0] for c in coords) / len(coords),
                  sum(c[1] for c in coords) / len(coords)]
        m = folium.Map(location=center, zoom_start=13, tiles="OpenStreetMap")
        folium.PolyLine(coords, color="#FF6400", weight=5, opacity=0.9).add_to(m)
        folium.Marker(coords[0],  popup="Start", icon=folium.Icon(color="green", icon="play")).add_to(m)
        folium.Marker(coords[-1], popup="Ziel",  icon=folium.Icon(color="red",   icon="stop")).add_to(m)
        st_folium(m, height=420, use_container_width=True,
                  key=f"route_map_{key_suffix}", returned_objects=[])

    # ── Trail selection (explore_trails) ─────────────────────────────────────
    elif tool == "explore_trails":
        # --- Pagination via session state -----------------------------------
        cache_key = f"trails_cache_{key_suffix}"
        page_key  = f"trails_page_{key_suffix}"

        # On first render: seed cache from the tool result
        if cache_key not in st.session_state:
            st.session_state[cache_key] = data.get("trails", [])
            st.session_state[page_key]  = 0   # 0-based index of first visible trail

        cached_trails = st.session_state[cache_key]
        if not cached_trails:
            st.info("Keine Trails gefunden.")
            return

        # "Mehr laden" button — fetches the next page from the MCP server
        col_info, col_btn = st.columns([3, 1])
        col_info.caption(
            f"Trails {st.session_state[page_key] + 1}–"
            f"{st.session_state[page_key] + len(cached_trails)} angezeigt"
            + ("  ·  weitere verfügbar" if data.get("has_more") else "")
        )
        if data.get("has_more") and col_btn.button("Mehr laden ▶", key=f"more_{key_suffix}"):
            from ui.shared import call_tool
            new_offset = st.session_state[page_key] + len(cached_trails)
            raw = call_tool("explore_trails", {
                "lat":        data["search_centre"]["lat"],
                "lon":        data["search_centre"]["lon"],
                "radius_km":  data["radius_km"],
                "sport_type": data["sport_type"],
                "limit":      data.get("page_size", 5),
                "offset":     new_offset,
            })
            new_data = json.loads(raw)
            if new_data.get("trails"):
                st.session_state[cache_key] = new_data["trails"]
                st.session_state[page_key]  = new_offset
                # Update route_data so next render uses the fresh page
                route_data["data"] = new_data
                cached_trails = new_data["trails"]
            st.rerun()

        trails = cached_trails
        names = [
            f"{t['name']}  ({t.get('distance') or '?'} km)"
            for t in trails
        ]
        sel_idx = st.radio(
            "Route auswählen:",
            range(len(names)),
            format_func=lambda i: names[i],
            key=f"trail_sel_{key_suffix}",
        )
        st.session_state["selected_route"] = trails[sel_idx]

        centre = data.get("search_centre", {})
        m = folium.Map(
            location=[centre.get("lat", 48.0), centre.get("lon", 8.0)],
            zoom_start=10,
            tiles="OpenStreetMap",
        )

        for i, trail in enumerate(trails):
            is_sel = (i == sel_idx)
            color  = _TRAIL_COLORS[i % len(_TRAIL_COLORS)]
            weight = 5 if is_sel else 2.5
            opacity = 0.95 if is_sel else 0.55

            segments = trail.get("segments", [])
            if segments:
                # Draw each GPS segment as a PolyLine (real track)
                for seg in segments:
                    # segments are stored as [lon, lat] — folium wants [lat, lon]
                    coords = [[pt[1], pt[0]] for pt in seg]
                    folium.PolyLine(
                        coords,
                        color=color,
                        weight=weight,
                        opacity=opacity,
                        tooltip=trail["name"],
                    ).add_to(m)
            else:
                # Fallback: draw bounding-box rectangle
                b = trail.get("bounds")
                if b and None not in (b.get("min_lat"), b.get("max_lat"),
                                      b.get("min_lon"), b.get("max_lon")):
                    folium.Polygon(
                        [[b["min_lat"], b["min_lon"]], [b["min_lat"], b["max_lon"]],
                         [b["max_lat"], b["max_lon"]], [b["max_lat"], b["min_lon"]]],
                        color=color, weight=weight, fill=True,
                        fill_color=color, fill_opacity=0.15,
                        tooltip=trail["name"],
                    ).add_to(m)

            # Pin for selected trail
            if is_sel:
                b = trail.get("bounds")
                if b:
                    clat = ((b.get("min_lat") or 0) + (b.get("max_lat") or 0)) / 2
                    clon = ((b.get("min_lon") or 0) + (b.get("max_lon") or 0)) / 2
                    folium.Marker(
                        [clat, clon],
                        popup=trail["name"],
                        icon=folium.Icon(color="orange", icon="map-marker"),
                    ).add_to(m)

        st_folium(m, height=450, use_container_width=True,
                  key=f"trail_map_{key_suffix}", returned_objects=[])

        t = trails[sel_idx]
        cols = st.columns(3)
        cols[0].metric("Distanz", f"{t.get('distance') or '?'} km")
        cols[1].metric("Typ", t.get("route_type") or "?")
        cols[2].metric("Netzwerk", t.get("network") or "?")
        if t.get("description"):
            st.caption(t["description"])
        if t.get("website"):
            st.caption(f"Mehr Infos: {t['website']}")

    # ── Isochrone ─────────────────────────────────────────────────────────────
    elif tool == "get_isochrone":
        geometry = data.get("geometry", {})
        centre = data.get("centre", {})
        if not geometry or not centre:
            return
        m = folium.Map(
            location=[centre["lat"], centre["lon"]],
            zoom_start=11,
            tiles="OpenStreetMap",
        )
        folium.GeoJson(
            {"type": "Feature", "geometry": geometry, "properties": {}},
            style_function=lambda _: {
                "fillColor": "#1E96FF",
                "color": "#0050AA",
                "weight": 2,
                "fillOpacity": 0.20,
            },
        ).add_to(m)
        folium.Marker(
            [centre["lat"], centre["lon"]],
            popup="Start",
            icon=folium.Icon(color="blue", icon="home"),
        ).add_to(m)
        st_folium(m, height=420, use_container_width=True,
                  key=f"isochrone_map_{key_suffix}", returned_objects=[])


# ── Debug panel ───────────────────────────────────────────────────────────────

def _render_trace(trace: Dict) -> None:
    if not trace:
        return

    plan       = trace.get("plan") or {}
    tool_calls = trace.get("tool_calls") or []
    timing     = trace.get("timing") or {}
    error      = trace.get("error")

    total_ms = sum(timing.values())
    label    = f"🔍 Agent trace  ·  {len(tool_calls)} calls  ·  {total_ms} ms"

    with st.expander(label, expanded=False):
        if error:
            st.error(f"Orchestrator error: {error}")

        reasoning = plan.get("reasoning", "")
        steps     = plan.get("steps") or []
        if reasoning:
            st.markdown(f"**Plan reasoning:** {reasoning}")
        if steps:
            st.markdown(f"**{len(steps)} planned call(s):**")
            for s in steps:
                args_str = json.dumps(s.get("args") or {})
                st.code(f"{s['tool']}({args_str})  # {s.get('label','')}", language="python")
        elif not error:
            st.caption("No tool calls needed for this question.")

        if tool_calls:
            st.markdown("**Execution results:**")
            cols_header = st.columns([3, 2, 1, 1])
            cols_header[0].caption("Tool")
            cols_header[1].caption("Label")
            cols_header[2].caption("Duration")
            cols_header[3].caption("Status")
            for c in sorted(tool_calls, key=lambda x: x.get("duration_ms", 0), reverse=True):
                cols = st.columns([3, 2, 1, 1])
                cols[0].code(c["tool"], language=None)
                cols[1].caption(c.get("label", "—"))
                cols[2].caption(f"{c.get('duration_ms', 0)} ms")
                cols[3].caption("❌" if c.get("error") else "✅")

        if timing:
            parts = []
            if "plan_ms"  in timing: parts.append(f"Plan {timing['plan_ms']} ms")
            if "exec_ms"  in timing: parts.append(f"Exec {timing['exec_ms']} ms")
            if "synth_ms" in timing: parts.append(f"Synth {timing['synth_ms']} ms")
            parts.append(f"**Total {total_ms} ms**")
            st.caption("  ·  ".join(parts))


# ── Main render ───────────────────────────────────────────────────────────────

def render_chat() -> None:
    st.markdown("### Ask anything about your fitness data")
    st.caption(
        "The assistant fetches live data from Strava and Garmin before answering — "
        "no guessing, only real numbers."
    )

    if "chat_history" not in st.session_state:
        st.session_state.chat_history = []
    if "chat_traces" not in st.session_state:
        st.session_state.chat_traces = []

    orchestrator = _get_orchestrator()

    # ── Message area (container sits ABOVE the chat_input in page flow) ───────
    messages = st.container()

    # ── Input — renders inline after the container → always at the bottom ─────
    placeholder = (
        "e.g. What are my personal bests?  /  "
        "How was my sleep last week?  /  "
        "Show HR peaks before sleep in the last 4 weeks"
    )
    prompt = st.chat_input(placeholder)

    # ── Fill the message area ─────────────────────────────────────────────────
    with messages:
        # Render conversation history
        for i, msg in enumerate(st.session_state.chat_history):
            avatar = "🏃" if msg["role"] == "assistant" else None
            with st.chat_message(msg["role"], avatar=avatar):
                st.markdown(msg["content"])
            if msg["role"] == "assistant" and i // 2 < len(st.session_state.chat_traces):
                trace = st.session_state.chat_traces[i // 2]
                _render_trace(trace)
                if trace.get("route_data"):
                    _render_route_map(trace["route_data"], key_suffix=trace.get("run_id", str(i)))

        # Handle new user input — rendered into the same container so it
        # appears above the input widget, not below it
        if prompt:
            st.session_state.chat_history.append({"role": "user", "content": prompt})
            with st.chat_message("user"):
                st.markdown(prompt)

            status_placeholder = st.empty()

            def _update_status(msg: str) -> None:
                status_placeholder.caption(f"⏳ {msg}")

            with st.chat_message("assistant", avatar="🏃"):
                history_before = st.session_state.chat_history[:-1]
                answer, trace  = orchestrator.run(prompt, history_before, _update_status)
                status_placeholder.empty()
                st.markdown(answer)

            _render_trace(trace)
            if trace.get("route_data"):
                _render_route_map(trace["route_data"], key_suffix=trace.get("run_id", "new"))

            st.session_state.chat_history.append({"role": "assistant", "content": answer})
            st.session_state.chat_traces.append(trace)

            if len(st.session_state.chat_history) > 20:
                st.session_state.chat_history = st.session_state.chat_history[-20:]
                st.session_state.chat_traces  = st.session_state.chat_traces[-10:]

        # Clear button lives inside the message area, below the last message
        if st.session_state.chat_history:
            if st.button("Clear conversation", type="secondary"):
                st.session_state.chat_history = []
                st.session_state.chat_traces  = []
                st.rerun()
