"""LLM-generated chart rendering for the chat tab.

Architecture:
  1. Generate  — LLM writes Plotly code informed by both the raw data AND the
                 orchestrator's conclusion (shared reasoning, no re-derivation).
  2. Execute   — run the code in a restricted namespace.
  3. Fix loop  — if execution raises, send the error back to the LLM for one
                 targeted fix, then re-execute (Reflexion pattern, max 2 attempts).
  4. Cache     — generated + fixed code is stored in st.session_state by run_id
                 so rerenders (history loop, tab switches) never call the LLM again.
"""

import json
import re
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from ui.shared import MODEL, get_openai_client

# Tools whose results are visualised elsewhere (maps / flythroughs) — skip here.
_SKIP_TOOLS = {
    "plan_route", "plan_circular_route", "explore_trails", "get_isochrone",
    "get_activity_streams", "get_activity_gps_track", "prepare_flythrough",
    "get_current_weather", "get_weather_forecast", "get_pollen_levels", "get_uv_index",
}


# ── Data helpers ──────────────────────────────────────────────────────────────

def _compact(data: Any, max_chars: int = 2500) -> str:
    """Compact JSON for the prompt — keep structure visible but limit size."""
    if isinstance(data, list) and len(data) > 60:
        head = json.dumps(data[:60], ensure_ascii=False)
        return f"{head[:-1]}, … ({len(data)} items total)]"
    raw = json.dumps(data, ensure_ascii=False)
    return raw if len(raw) <= max_chars else raw[:max_chars] + "…"


# ── LLM calls ─────────────────────────────────────────────────────────────────

_STRAVA_DOMAIN_HINT = """\
Strava data conventions (ALWAYS apply):
- distance / distance_km: activity lists from strava__get_activities already have
  distance_km (pre-converted). Raw stream data has distance in metres → divide by 1000.
- moving_time: raw Strava API returns SECONDS. Pre-formatted tool results have
  moving_time_hours. Divide seconds by 60 for minutes, 3600 for hours.
- start_date / date: ISO-8601 string ("2025-06-01T06:30:00Z") or "YYYY-MM-DD".
  Always parse with pd.to_datetime() before using as an axis.
- average_speed: m/s in raw Strava. activity lists may already have avg_speed_kmh.
- elevation_gain_m / total_elevation_gain: metres of total climbing.
- average_heartrate / avg_heart_rate: bpm.
- Grouping by week: use df['date'].dt.to_period('W') or dt.isocalendar().week.
- Always use the pre-computed _km / _hours / _kmh fields when available rather
  than raw Strava fields to avoid unit mistakes.
"""


def _generate_code(
    question: str,
    answer_text: str,
    var_lines: List[str],
    chart_hints: Optional[List[str]] = None,
    _client=None,
    _model: Optional[str] = None,
) -> Optional[str]:
    """Ask the LLM to write focused Plotly code.

    Passes both the raw data variables AND the orchestrator's conclusion so the
    chart illustrates what was actually found, not a generic data dump.
    When chart_hints is provided (from the orchestrator's <!--charts:...-->  tag),
    those descriptions drive chart selection directly — no re-derivation needed.
    """
    vars_block = "\n".join(var_lines)
    conclusion = answer_text[:900] if answer_text else ""

    prompt = "You are a data-visualization expert for a personal sports analytics app.\n\n"
    prompt += f'Question: "{question}"\n\n'
    if chart_hints:
        prompt += (
            "The assistant requested these specific charts:\n"
            + "\n".join(f"  - {h}" for h in chart_hints)
            + "\n\nWrite EXACTLY those charts (1 chart per bullet). "
            "Do not add others.\n\n"
        )
        if conclusion:
            prompt += (
                f"Context (assistant's conclusion):\n{conclusion}\n\n"
                "Use this context for titles and axis labels — it explains what was found.\n\n"
            )
    elif conclusion:
        prompt += (
            f"The assistant already analysed the data and concluded:\n"
            f"{conclusion}\n\n"
            "Write 1–3 Plotly charts that ILLUSTRATE THIS CONCLUSION visually.\n"
            "Focus on the specific finding — do not show all available metrics.\n\n"
        )
    else:
        prompt += "Write 1–3 Plotly charts that directly answer the question.\n\n"

    prompt += (
        f"Available data (pre-loaded Python variables):\n{vars_block}\n\n"
        f"{_STRAVA_DOMAIN_HINT}\n"
        "Coding rules:\n"
        "- Pre-imported: pd (pandas), px (plotly.express), go (plotly.graph_objects), json\n"
        "- Variables are already loaded — never read files or call APIs\n"
        "- figures is a list already defined. Call figures.append(fig) for each chart. "
        "  Do NOT write figures = [] (it is already initialised).\n"
        "- Every figure must end with: "
        "fig.update_layout(template='plotly_dark', margin=dict(t=40,b=30,l=10,r=10))\n"
        "- For activity lists: start with df = pd.DataFrame(data_<tool_name>), "
        "  then parse dates and use the pre-computed _km / _hours fields.\n"
        "- Handle None/missing values gracefully (dropna, fillna(0), or skip)\n"
        "- Return ONLY the Python code in ```python … ```"
    )
    try:
        resp = (_client or get_openai_client()).chat.completions.create(
            model=(_model or MODEL),
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
            max_tokens=1800,
        )
        return resp.choices[0].message.content or ""
    except Exception:
        return None


def _fix_code(code: str, error: str, var_names: List[str], _client=None, _model: Optional[str] = None) -> Optional[str]:
    """Ask the LLM to fix a code snippet that raised a known error (Reflexion step)."""
    prompt = (
        f"Fix this Python/Plotly code. It raised an error at runtime.\n\n"
        f"```python\n{code}\n```\n\n"
        f"Error: {error}\n\n"
        f"Available variables: {', '.join(var_names)}\n"
        "Return ONLY the corrected ```python … ``` block, nothing else."
    )
    try:
        resp = (_client or get_openai_client()).chat.completions.create(
            model=(_model or MODEL),
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
            max_tokens=1600,
        )
        return _extract_code(resp.choices[0].message.content or "")
    except Exception:
        return None


# ── Code extraction + execution ───────────────────────────────────────────────

def _extract_code(text: str) -> Optional[str]:
    m = re.search(r"```python\s*(.*?)\s*```", text, re.DOTALL)
    if not m:
        m = re.search(r"```\s*(.*?)\s*```", text, re.DOTALL)
    return m.group(1).strip() if m else None


def _try_execute(code: str, data_vars: Dict[str, Any]) -> Tuple[List[go.Figure], str]:
    """Run code in a restricted namespace.  Returns (figures, error_message).
    error_message is '' on success.
    """
    ns: Dict[str, Any] = {
        "pd": pd, "px": px, "go": go, "json": json,
        "figures": [],
        **data_vars,
    }
    try:
        exec(code, ns)  # noqa: S102
        figs = [f for f in (ns.get("figures") or []) if isinstance(f, go.Figure)]
        return figs, ""
    except Exception as exc:
        return [], f"{type(exc).__name__}: {exc}"


# ── Public entry point ────────────────────────────────────────────────────────

def generate_and_render(trace: Dict, key_suffix: str = "") -> None:
    """Generate and render LLM-written charts for a completed orchestrator turn.

    Flow:
      1. Build data_vars from tool results (skip map/flythrough tools).
      2. On first call for this run_id: generate code via LLM (shared reasoning:
         passes both raw data and the orchestrator's answer text as context).
      3. Execute.  On error: ask LLM to fix the specific exception, re-execute once.
      4. Cache the working code in st.session_state — rerenders skip the LLM entirely.
    """
    run_id   = trace.get("run_id") or key_suffix
    question = trace.get("question", "")
    answer   = trace.get("answer", "")   # orchestrator's conclusion → shared context
    if not question:
        return

    # ── Collect tool results ──────────────────────────────────────────────────
    data_vars: Dict[str, Any] = {}
    var_lines: List[str] = []
    _seen_vars: set = set()
    for tc in (trace.get("tool_calls") or []):
        if tc.get("error"):
            continue
        bare = tc["tool"].split("__", 1)[-1] if "__" in tc["tool"] else tc["tool"]
        if bare in _SKIP_TOOLS:
            continue
        try:
            data = json.loads(tc["result"]) if isinstance(tc["result"], str) else tc["result"]
        except Exception:
            continue
        if not data or (isinstance(data, dict) and data.get("error")):
            continue
        var_name = f"data_{bare}"
        data_vars[var_name] = data
        # Keep var_lines in sync with data_vars — one entry per variable name,
        # last call wins (matches exec namespace behaviour).
        if var_name in _seen_vars:
            var_lines = [l for l in var_lines if not l.startswith(f"{var_name} =")]
        _seen_vars.add(var_name)
        var_lines.append(f"{var_name} = {_compact(data)}")

    if not data_vars:
        return

    # ── Generate (once) ───────────────────────────────────────────────────────
    cache: Dict[str, str] = st.session_state.setdefault("_chart_code_cache", {})
    # Mark permanently failed run_ids so we don't retry forever
    failed: set = st.session_state.setdefault("_chart_code_failed", set())
    if run_id in failed:
        return

    hints = trace.get("chart_hints") or []
    if run_id not in cache:
        with st.spinner("Generating chart…"):
            raw = _generate_code(question, answer, var_lines, chart_hints=hints)
        if not raw:
            return
        code = _extract_code(raw)
        if not code:
            return
        cache[run_id] = code

    code = cache[run_id]

    # ── Execute + fix loop (max 2 attempts) ───────────────────────────────────
    for attempt in range(2):
        figures, error = _try_execute(code, data_vars)
        if figures:
            for i, fig in enumerate(figures):
                fig.update_layout(height=320)
                st.plotly_chart(
                    fig,
                    width='stretch',
                    config={"displayModeBar": False},
                    key=f"llm_chart_{key_suffix or run_id}_{i}",
                )
            return  # success

        if error and attempt == 0:
            # Reflexion: send the error back, get a targeted fix
            with st.spinner("Fixing chart…"):
                fixed = _fix_code(code, error, list(data_vars.keys()))
            if fixed and fixed != code:
                code = fixed
                cache[run_id] = fixed  # persist fixed version
            # attempt 1 will re-execute the fixed code

    # Both attempts failed — mark so we don't retry on next rerender
    failed.add(run_id)
