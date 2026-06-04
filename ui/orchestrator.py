"""HealthBot Multi-Agent Orchestrator.

Agent pipeline — 4 specialized agents in 3 phases:

  Phase 1 — FetchingAgent (sequential, always runs first):
      • Receives: user query, today's date, conversation history
      • Calls Strava and Garmin MCP tools to retrieve the required data
      • Returns: structured JSON with results, reasoning, key findings
      • MUST NOT do: write responses, select charts, trigger flythrough render

  Phase 2 — VisualizationAgent + FlyoverAgent (parallel, after Phase 1):
      VisualizationAgent:
        • Receives: user query, FetchingAgent results
        • Selects which fetched results to render as charts
        • Returns: ordered list of viz_actions
        • MUST NOT do: fetch data, write responses, handle flythrough
      FlyoverAgent:
        • Receives: user query, FetchingAgent results
        • Detects if a flythrough was triggered (fast path) or can be triggered (LLM path)
        • Returns: flyover_action dict or null
        • MUST NOT do: fetch data, write responses, ask the user for missing params

  Phase 3 — ChatAgent (sequential, always runs last):
      • Receives: query, FetchingAgent results, viz context, flyover context, history
      • Writes the final natural-language answer
      • Knows what will auto-render below (charts + flythrough) from the context
      • MUST NOT do: call MCP tools, re-describe chart contents in detail

Routing rules:
  • Flythrough queries (keywords or launch_flythrough in results) skip VisualizationAgent
    and run FlyoverAgent exclusively — avoids LLM burst and conflicting UI actions.
  • Clarification-needed or all-fetches-failed → skip Phase 2 entirely; ChatAgent handles.

Inter-agent communication: structured JSON strings (MCP tool-call contract).
Traces appended to .logs/agent_interactions.jsonl.
"""

import json
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from servers.agents._base import FLYTHROUGH_KEYWORDS

# ── Config ────────────────────────────────────────────────────────────────────

LOG_DIR  = Path(".logs")
LOG_FILE = LOG_DIR / "agent_interactions.jsonl"

ROUTE_TOOLS = {"plan_route", "plan_circular_route", "explore_trails", "get_isochrone"}



# ── Orchestrator ──────────────────────────────────────────────────────────────

class HealthBotOrchestrator:
    """
    Stateless multi-agent orchestrator.

    Create once (e.g. via st.cache_resource) and call run() per message.
    Internally coordinates FetchingAgent → (VisualizationAgent ∥ FlyoverAgent) → ChatAgent.
    """

    def __init__(self) -> None:
        LOG_DIR.mkdir(exist_ok=True)

    def run(
        self,
        user_input: str,
        history: List[Dict],
        progress_cb: Optional[Callable[[str], None]] = None,
    ) -> Tuple[str, Dict]:
        """
        Execute one orchestration cycle.

        Returns:
            answer  — final assistant reply string
            trace   — full execution record (logged + used by UI debug panel)
        """
        # Lazy-import agents to avoid circular imports at module load time.
        from servers.agents.fetching      import call_sync as fetch
        from servers.agents.visualization import call_sync as visualize
        from servers.agents.flyover       import call_sync as flyover
        from servers.agents.chat          import call_sync as chat

        today  = datetime.now().strftime("%Y-%m-%d")
        run_id = str(uuid.uuid4())[:8]

        trace: Dict[str, Any] = {
            "run_id":     run_id,
            "ts":         datetime.utcnow().isoformat() + "Z",
            "user_input": user_input,
            "plan":       None,
            "tool_calls": [],
            "answer":     None,
            "timing":     {},
            "error":      None,
            "actions":    [],
            "agents":     [],   # per-agent phase records for the debug panel
        }

        try:
            # ── Phase 1: FetchingAgent ────────────────────────────────────────
            _cb(progress_cb, "Planning data retrieval…")
            t0 = time.perf_counter()
            data_json = fetch(
                query       = user_input,
                today       = today,
                history     = history[-10:],
                progress_cb = progress_cb,
            )
            fetch_ms = _ms(t0)
            trace["timing"]["fetch_ms"] = fetch_ms

            # Populate trace fields that the debug panel expects
            clarification_needed   = False
            clarification_question = ""
            data_summary           = ""
            fetched_tools: set     = set()
            fetch_data: Dict[str, Any] = {}
            try:
                fetch_data = json.loads(data_json)
                clarification_needed   = bool(fetch_data.get("clarification_needed", False))
                clarification_question = fetch_data.get("clarification_question", "")
                data_summary           = fetch_data.get("data_summary", "")
                fetched_tools          = {
                    r["tool"] for r in fetch_data.get("results", []) if not r.get("error")
                }
                trace["plan"] = {
                    "reasoning": fetch_data.get("reasoning", ""),
                    "steps": [
                        {"tool": r["tool"], "args": r.get("args", {}), "label": r["label"]}
                        for r in fetch_data.get("results", [])
                    ],
                }
                trace["tool_calls"] = fetch_data.get("results", [])
            except (json.JSONDecodeError, TypeError):
                pass

            trace["agents"].append({
                "agent": "FetchingAgent",
                "phase": 1,
                "duration_ms": fetch_ms,
                "clarification_needed": clarification_needed,
                "data_summary": data_summary,
            })

            # ── Phase 2: VisualizationAgent + FlyoverAgent ───────────────────
            # Routing rules (evaluated in order):
            #
            #   1. clarification_needed or all_fetches_failed
            #      → skip Phase 2 entirely; ChatAgent handles both cases.
            #
            #   2. is_flythrough_query (launch_flythrough succeeded OR user
            #      mentioned a flythrough keyword in their message)
            #      → FlyoverAgent ONLY. VisualizationAgent is skipped to avoid
            #        running two LLM calls in parallel (rate-limit risk) and to
            #        prevent charts appearing alongside a flythrough response.
            #
            #   3. Normal analytics query
            #      → both agents in parallel. FlyoverAgent exits quickly when
            #        no flythrough keyword is present.
            t0 = time.perf_counter()

            flythrough_resolved = "launch_flythrough" in fetched_tools
            flythrough_mentioned = any(k in user_input.lower() for k in FLYTHROUGH_KEYWORDS)
            is_flythrough_query  = flythrough_resolved or flythrough_mentioned

            all_fetches_failed = (
                bool(fetch_data.get("results"))
                and all(r.get("error") for r in fetch_data.get("results", []))
            )

            if clarification_needed or all_fetches_failed:
                viz_json     = '{"viz_actions": []}'
                fly_json     = '{"flyover_action": null}'
                phase2_label = "skipped (clarification needed or all sources failed)"
            elif is_flythrough_query:
                _cb(progress_cb, "Resolving flythrough…")
                viz_json     = '{"viz_actions": []}'
                try:
                    fly_json = flyover(query=user_input, data_results=data_json)
                except Exception:
                    fly_json = '{"flyover_action": null}'
                phase2_label = "FlyoverAgent only (flythrough query)"
            else:
                _cb(progress_cb, "Selecting charts and checking for flythrough…")
                trace["route_data"] = _extract_route_data(fetch_data.get("results", []))
                with ThreadPoolExecutor(max_workers=2) as pool:
                    viz_fut = pool.submit(visualize, query=user_input, data_results=data_json)
                    fly_fut = pool.submit(flyover,   query=user_input, data_results=data_json)
                    try:
                        viz_json = viz_fut.result(timeout=45)
                    except Exception:
                        viz_json = '{"viz_actions": []}'
                    try:
                        fly_json = fly_fut.result(timeout=45)
                    except Exception:
                        fly_json = '{"flyover_action": null}'
                phase2_label = "VisualizationAgent + FlyoverAgent (parallel)"

            analysis_ms = _ms(t0)
            trace["timing"]["analysis_ms"] = analysis_ms

            trace["agents"].append({
                "agent": phase2_label,
                "phase": 2,
                "duration_ms": analysis_ms,
            })

            # Parse UI actions from both Phase 2 agents
            trace["actions"] = _parse_actions(viz_json, fly_json)

            # ── Phase 3: ChatAgent ─────────────────────────────────────────────
            _cb(progress_cb, "Composing answer…")
            t0 = time.perf_counter()
            answer = chat(
                query                  = user_input,
                data_results           = data_json,
                viz_context            = viz_json,
                flyover_context        = fly_json,
                history                = history[-10:],
                today                  = today,
                clarification_question = clarification_question,
            )
            chat_ms = _ms(t0)
            trace["timing"]["chat_ms"] = chat_ms

            trace["agents"].append({
                "agent": "ChatAgent",
                "phase": 3,
                "duration_ms": chat_ms,
            })

        except Exception as exc:
            trace["error"] = str(exc)
            answer = f"Orchestrator error: {exc}"

        trace["answer"] = answer
        _write_log(trace)
        return answer, trace


# ── Action parsing ────────────────────────────────────────────────────────────

def _parse_actions(viz_json: str, fly_json: str) -> List[Dict]:
    """Merge UI actions from VisualizationAgent and FlyoverAgent."""
    actions: List[Dict] = []

    # Flyover action (at most one, flyover takes precedence at top)
    try:
        fly = json.loads(fly_json)
        fa  = fly.get("flyover_action")
        if fa and fa.get("type") == "flythrough":
            actions.append(fa)
    except (json.JSONDecodeError, TypeError):
        pass

    # Viz actions from VisualizationAgent — suppressed when a flythrough is active
    # (charts alongside a map render add noise, not insight)
    has_flythrough = any(a.get("type") == "flythrough" for a in actions)
    if not has_flythrough:
        try:
            viz = json.loads(viz_json)
            for va in (viz.get("viz_actions") or []):
                if va.get("type") == "viz":
                    actions.append(va)
        except (json.JSONDecodeError, TypeError):
            pass

    return actions


# ── Logging ───────────────────────────────────────────────────────────────────

def _write_log(trace: Dict) -> None:
    """Append a lean trace record to the JSONL log. Never raises."""
    try:
        entry = {
            "run_id":     trace["run_id"],
            "ts":         trace["ts"],
            "user_input": trace["user_input"],
            "agents":     trace.get("agents", []),
            "timing":     trace.get("timing", {}),
            "n_tool_calls": len(trace.get("tool_calls", [])),
            "n_actions":    len(trace.get("actions", [])),
            "error":        trace.get("error"),
            "answer_preview": (trace.get("answer") or "")[:300],
        }
        with open(LOG_FILE, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        pass


# ── Utilities ─────────────────────────────────────────────────────────────────

def _extract_route_data(results: List[Dict]) -> Optional[Dict]:
    """Return the first successful route-tool result, parsed from JSON."""
    for r in results:
        if r.get("tool") in ROUTE_TOOLS and not r.get("error") and r.get("result"):
            try:
                return {"tool": r["tool"], "data": json.loads(r["result"])}
            except Exception:
                pass
    return None


def _ms(t0: float) -> int:
    return int((time.perf_counter() - t0) * 1000)


def _cb(fn: Optional[Callable], msg: str) -> None:
    if fn:
        try:
            fn(msg)
        except Exception:
            pass
