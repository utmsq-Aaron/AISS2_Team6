"""Chat tab — AI sports analyst backed by a three-phase orchestrator.

Layout: messages fill a container that sits above st.chat_input, so the
input is always at the bottom (ChatGPT / Claude style).
"""

import json
from typing import Dict

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
                _render_trace(st.session_state.chat_traces[i // 2])

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
