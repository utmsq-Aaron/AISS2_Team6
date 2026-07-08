"""3D Activity Flythrough — MapLibre GL JS cinematic camera animation.

Two modes:
  dark         — dark vector basemap with star-field fog
  satellite_3d — ESRI satellite imagery + terrain DEM, real 3D mountains

Animation engine:
  - Speed follows actual GPS timing; speed-adaptive EMA bearing
  - Dynamic pitch and zoom: tilts up on climbs, zooms out at speed

Video export (WebCodecs + mp4-muxer):
  - Deterministic frame-by-frame encoding — no real-time capture, no frame drops
  - H.264 hardware acceleration via VideoEncoder; MP4 container via mp4-muxer
  - Landscape 16:9 or Portrait 9:16 at HD / 2K / 4K
  - map.once('idle') per frame guarantees all tiles are loaded before capture
  - Auto-downloads as .mp4; double-click Export to cancel mid-encoding
"""

import threading
from typing import List, Optional

import streamlit as st

# The Streamlit-free HTML engine + track-prep helpers now live in
# core/flythrough_html.py, so the FastAPI route (api/routers/flythrough.py) can
# build the exact same page. Re-exported here for backward compatibility —
# ui/video_renderer.py imports _build_html from this module.
from core.flythrough_html import (  # noqa: F401
    _DARK_STYLE_JS,
    _HTML,
    _SAT3D_STYLE_JS,
    _build_html,
    _downsample,
    _prepare_track,
    _smooth,
)


# ── Data pipeline ─────────────────────────────────────────────────────────────

def _fetch_track(activity_id: int) -> List[List[float]]:
    """Return [[lon, lat, ele, time_s], ...] — time_s may be None."""
    from ui.activity_analysis import _load_streams
    data   = _load_streams(activity_id)
    points = data.get("points", [])
    if not points:
        raise ValueError("No GPS stream data for this activity.")
    return [
        [p["lon"], p["lat"], p.get("ele") or 0.0, p.get("time_s")]
        for p in points
        if p.get("lat") is not None and p.get("lon") is not None
    ]


# ── Public entry point ────────────────────────────────────────────────────────

def show_flythrough(
    activity_id: int,
    activity_name: str = "",
    mode: Optional[str] = None,
    duration_sec: int = 0,
    orientation: str = "landscape",
    hidden: bool = False,
    resolution: str = "2K",
) -> None:
    """Render a 3D flythrough for an activity.

    hidden=True  — server-side render via Playwright; emits a download button.
    hidden=False — interactive preview iframe + Python export controls below.
    """
    import time as _time

    name = activity_name or f"Activity {activity_id}"

    # ── Load GPS track ────────────────────────────────────────────────────────
    try:
        if hidden:
            raw   = _fetch_track(activity_id)
            track = _prepare_track(raw)
        else:
            with st.spinner("Loading GPS track…"):
                raw   = _fetch_track(activity_id)
                track = _prepare_track(raw)
    except Exception as e:
        st.error(f"Could not load GPS data: {e}")
        return

    # ── Mode ──────────────────────────────────────────────────────────────────
    if mode is None:
        mode_label = st.radio(
            "Map style",
            ["Satellite 3D", "Dark Flat"],
            index=0,
            horizontal=True,
            key=f"flythrough_mode_{activity_id}",
            label_visibility="collapsed",
        )
        mode = "satellite_3d" if mode_label == "Satellite 3D" else "dark"

    # ── Hidden / agent-triggered: non-blocking server-side render ────────────
    if hidden:
        render_key = f"ft_video_{activity_id}_{orientation}_{resolution}_{duration_sec}"
        thread_key = render_key + "_thread"

        # Promote a completed background render into the permanent cache
        ti = st.session_state.get(thread_key)
        if ti and ti["status"] == "done":
            st.session_state[render_key] = ti["data"]
            del st.session_state[thread_key]
            ti = None

        video_bytes = st.session_state.get(render_key)

        if video_bytes is None:
            if ti is None:
                # First call — kick off background thread
                ti = {"status": "running", "data": None, "error": None}
                st.session_state[thread_key] = ti

                def _run(ti=ti):
                    try:
                        from ui.video_renderer import render_flythrough
                        ti["data"] = render_flythrough(
                            track, name,
                            mode=mode,
                            duration_sec=duration_sec,
                            orientation=orientation,
                            resolution=resolution,
                        )
                        ti["status"] = "done"
                    except Exception as exc:
                        ti["error"] = str(exc)
                        ti["status"] = "error"

                threading.Thread(target=_run, daemon=True).start()

            if ti["status"] == "error":
                st.error(f"Render failed: {ti['error']}")
                del st.session_state[thread_key]
                return

            # Still running — show status and poll every 3 s
            st.info(
                f"🎬 Rendering **{name}** "
                f"({duration_sec} s · {orientation} · {resolution}) in the background — "
                "keep chatting or exploring the dashboard!"
            )
            _time.sleep(3)
            st.rerun()
            return

        # ── Video ready ───────────────────────────────────────────────────────
        safe_fn = name.replace(" ", "_").replace("/", "-")[:40]
        # Constrain preview via column width — st.video fills its container and the
        # browser scales height proportionally, so a narrow column keeps portrait-4K
        # from filling the screen.  Download delivers the full-resolution file.
        # Portrait 9:16  → 28 % col  ≈ 336 px wide → ~597 px tall
        # Landscape 16:9 → 55 % col  ≈ 660 px wide → ~371 px tall
        if orientation == "portrait":
            vid_col, _ = st.columns([5, 13])
        else:
            vid_col, _ = st.columns([6, 5])
        with vid_col:
            st.video(video_bytes, format="video/mp4")
        st.download_button(
            label=f"⬇ Download full-quality MP4 — {name}",
            data=video_bytes,
            file_name=f"flythrough_{safe_fn}.mp4",
            mime="video/mp4",
            type="primary",
            key=f"ft_dl_{render_key}",
        )
        return

    # ── Visible: interactive preview iframe ───────────────────────────────────
    ele_values = [p[2] for p in track if p[2]]
    has_timing = any(p[3] is not None for p in track if len(p) > 3)
    ele_range  = f"{min(ele_values):.0f} – {max(ele_values):.0f} m" if ele_values else "—"

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("GPS Points",      f"{len(track):,}")
    c2.metric("Elevation Range", ele_range)
    c3.metric("Speed Data",      "✓ GPS timing" if has_timing else "uniform")
    c4.metric("Mode",            "Satellite 3D" if mode == "satellite_3d" else "Dark Flat")

    mode_str = "Satellite 3D · real terrain" if mode == "satellite_3d" else "Dark flat · star-field"
    st.caption(f"{'🌍' if mode == 'satellite_3d' else '🗺'} {mode_str} · "
               "Adjust pitch / zoom / duration in the preview, then export below.")

    st.iframe(
        _build_html(track, name, mode=mode, duration_sec=duration_sec),
        height=630,
    )

    # ── Export controls (Python-level, below the preview) ────────────────────
    st.markdown("**Export video**")
    ec1, ec2, ec3 = st.columns(3)
    exp_orient = ec1.radio(
        "Orientation", ["Landscape", "Portrait"],
        horizontal=True, key=f"ft_orient_{activity_id}",
    )
    exp_res = ec2.radio(
        "Resolution", ["HD", "2K", "4K"],
        horizontal=True, index=1, key=f"ft_res_{activity_id}",
    )
    exp_dur = ec3.slider(
        "Duration (s)", 30, 120, max(30, min(120, duration_sec or 60)),
        step=5, key=f"ft_dur_{activity_id}",
    )

    render_key = f"ft_video_{activity_id}_{exp_orient}_{exp_res}_{exp_dur}"
    thread_key = render_key + "_thread"

    # Promote completed background render into permanent cache
    ti = st.session_state.get(thread_key)
    if ti and ti["status"] == "done":
        st.session_state[render_key] = ti["data"]
        del st.session_state[thread_key]
        ti = None

    if st.button("Render & Export", type="primary", key=f"ft_render_{activity_id}",
                 disabled=bool(st.session_state.get(thread_key))):
        if render_key not in st.session_state and not st.session_state.get(thread_key):
            ti = {"status": "running", "data": None, "error": None}
            st.session_state[thread_key] = ti

            def _run(ti=ti):
                try:
                    from ui.video_renderer import render_flythrough
                    ti["data"] = render_flythrough(
                        track, name,
                        mode=mode,
                        duration_sec=exp_dur,
                        orientation=exp_orient.lower(),
                        resolution=exp_res,
                    )
                    ti["status"] = "done"
                except Exception as exc:
                    ti["error"] = str(exc)
                    ti["status"] = "error"

            threading.Thread(target=_run, daemon=True).start()
            st.rerun()

    # Status / result
    ti = st.session_state.get(thread_key)
    if ti:
        if ti["status"] == "error":
            st.error(f"Render failed: {ti['error']}")
            del st.session_state[thread_key]
        else:
            st.info(
                f"🎬 Rendering **{exp_dur} s · {exp_orient} · {exp_res}** in the background — "
                "keep exploring the dashboard!"
            )
            _time.sleep(3)
            st.rerun()

    if render_key in st.session_state:
        safe_fn = name.replace(" ", "_").replace("/", "-")[:40]
        # Same column constraint as the chat path — prevents portrait-4K from filling the screen
        if exp_orient == "Portrait":
            _vcol, _ = st.columns([5, 13])
        else:
            _vcol, _ = st.columns([6, 5])
        with _vcol:
            st.video(st.session_state[render_key], format="video/mp4")
        st.download_button(
            label=f"⬇ Download — {exp_orient} {exp_res} {exp_dur}s",
            data=st.session_state[render_key],
            file_name=f"flythrough_{safe_fn}_{exp_orient.lower()}_{exp_res}.mp4",
            mime="video/mp4",
            type="primary",
            key=f"ft_dl_{activity_id}_{render_key}",
        )
