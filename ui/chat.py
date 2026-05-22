"""Chat tab — AI sports analyst backed by a three-phase orchestrator.

Layout: messages fill a container that sits above st.chat_input, so the
input is always at the bottom (ChatGPT / Claude style).
"""

import json
from typing import Dict, List

import streamlit as st


@st.cache_resource(show_spinner=False)
def _get_orchestrator():
    from ui.orchestrator import FitDashOrchestrator
    return FitDashOrchestrator()


# ── Debug panel ───────────────────────────────────────────────────────────────

def _render_trace(trace: Dict) -> None:
    if not trace:
        return

    plan       = trace.get("plan") or {}
    tool_calls = trace.get("tool_calls") or []
    timing     = trace.get("timing") or {}
    agents     = trace.get("agents") or []
    error      = trace.get("error")

    total_ms = sum(timing.values())
    label    = f"🔍 Agent trace  ·  {len(tool_calls)} tool call(s)  ·  {total_ms} ms"

    with st.expander(label, expanded=False):
        if error:
            st.error(f"Orchestrator error: {error}")

        # ── Agent pipeline overview ───────────────────────────────────────────
        if agents:
            st.markdown("**Agent pipeline:**")
            for ag in agents:
                st.caption(
                    f"Phase {ag['phase']} — **{ag['agent']}** — {ag['duration_ms']} ms"
                )

        # ── FetchingAgent plan ────────────────────────────────────────────────
        data_summary = next(
            (ag.get("data_summary") for ag in agents if ag.get("agent") == "FetchingAgent"), ""
        )
        reasoning = plan.get("reasoning", "")
        steps     = plan.get("steps") or []
        if data_summary:
            st.caption(f"Data: {data_summary}")
        if reasoning:
            st.markdown(f"**FetchingAgent plan:** {reasoning}")
        if steps:
            st.markdown(f"**{len(steps)} planned MCP call(s):**")
            for s in steps:
                args_str = json.dumps(s.get("args") or {})
                st.code(f"{s['tool']}({args_str})  # {s.get('label','')}", language="python")
        elif not error:
            st.caption("No MCP tool calls needed for this question.")

        # ── Tool execution results ────────────────────────────────────────────
        if tool_calls:
            st.markdown("**MCP execution results:**")
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

        # ── Timing breakdown ──────────────────────────────────────────────────
        if timing:
            parts = []
            _LABELS = {
                "fetch_ms":    "FetchingAgent",
                "analysis_ms": "Viz+Flyover (∥)",
                "chat_ms":     "ChatAgent",
                "plan_ms":     "Plan",
                "exec_ms":     "Exec",
                "synth_ms":    "Synth",
            }
            for key, lbl in _LABELS.items():
                if key in timing:
                    parts.append(f"{lbl} {timing[key]} ms")
            parts.append(f"**Total {total_ms} ms**")
            st.caption("  ·  ".join(parts))


# ── Inline renderers ──────────────────────────────────────────────────────────

def _render_viz_actions(actions: List[Dict]) -> None:
    """Render inline charts for all viz actions attached to a trace."""
    from ui import viz
    for action in (actions or []):
        if action.get("type") == "viz":
            viz.render(action["tool"], action["result"], action.get("metric_focus", ""))


def _render_flythrough_inline(actions: List[Dict]) -> None:
    """Render flythrough pinned to this message turn.

    While rendering: shown inline (no expander) so reruns don't collapse it mid-progress.
    When video is ready: wrapped in an auto-opened expander so the conversation stays readable.
    """
    for action in (actions or []):
        if action.get("type") == "flythrough":
            from ui.flythrough_3d import show_flythrough
            activity_id = action["activity_id"]
            orientation = action.get("orientation", "landscape")
            resolution  = action.get("resolution", "2K")
            name        = action.get("activity_name") or "Flythrough"
            render_key  = f"ft_video_{activity_id}_{orientation}_{resolution}"

            kwargs = dict(
                mode=action.get("mode", "satellite_3d"),
                duration_sec=int(action.get("duration_sec", 60)),
                orientation=orientation,
                resolution=resolution,
                hidden=True,
            )

            if render_key in st.session_state:
                # Video ready — wrap in expander; expanded=True on first appearance
                # so user sees it without having to open manually.
                with st.expander(f"🎬 {name}", expanded=True):
                    show_flythrough(activity_id, name, **kwargs)
            else:
                # Still rendering — show status inline so reruns don't hide progress
                show_flythrough(activity_id, name, **kwargs)
            break


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
                    trace_i = st.session_state.chat_traces[i // 2]
                    _render_viz_actions(trace_i.get("actions") or [])
                    _render_flythrough_inline(trace_i.get("actions") or [])
            if msg["role"] == "assistant" and i // 2 < len(st.session_state.chat_traces):
                _render_trace(st.session_state.chat_traces[i // 2])

        # Handle new user input
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

                is_flythrough = any(
                    a.get("type") == "flythrough"
                    for a in (trace.get("actions") or [])
                )

                if not is_flythrough:
                    st.markdown(answer)
                    _render_viz_actions(trace.get("actions") or [])

            if not is_flythrough:
                _render_trace(trace)

            # Persist BEFORE anything that calls st.rerun().
            # show_flythrough() polls via st.rerun() every 3 s — if we haven't
            # appended yet, that rerun sees an incomplete chat_history and the
            # assistant message is permanently lost.
            st.session_state.chat_history.append({"role": "assistant", "content": answer})
            st.session_state.chat_traces.append(trace)

            if len(st.session_state.chat_history) > 20:
                st.session_state.chat_history = st.session_state.chat_history[-20:]
                st.session_state.chat_traces  = st.session_state.chat_traces[-10:]

            # For flythrough turns, rerun immediately so the history loop
            # renders the widget at the correct position inside the chat bubble.
            if is_flythrough:
                st.rerun()

        # Clear button lives inside the message area, below the last message
        if st.session_state.chat_history:
            if st.button("Clear conversation", type="secondary"):
                st.session_state.chat_history = []
                st.session_state.chat_traces  = []
                for k in [k for k in st.session_state if k.startswith("ft_video_")]:
                    st.session_state.pop(k, None)
                st.rerun()
