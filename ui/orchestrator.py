"""FitDash Orchestrator — three-phase agentic engine.

Flow for every user message:
  1. Planner  — LLM produces a structured JSON execution plan
  2. Executor — all tool calls run in parallel (ThreadPoolExecutor)
  3. Synthesizer — LLM analyses all results and writes the final answer

Interactions are appended to .logs/agent_interactions.jsonl for debugging.
"""

import json
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from ui.shared import MODEL, call_tool, get_all_openai_tools, get_openai_client

# ── Config ────────────────────────────────────────────────────────────────────

LOG_DIR  = Path(".logs")
LOG_FILE = LOG_DIR / "agent_interactions.jsonl"

MAX_PLAN_STEPS = 60   # hard cap on planner output
MAX_WORKERS    = 5    # parallel tool-call threads (Garmin rate-limit friendly)
TOOL_TIMEOUT   = 45   # seconds per individual tool call

ROUTE_TOOLS = {"plan_route", "plan_circular_route", "explore_trails", "get_isochrone"}

# ── Prompts ───────────────────────────────────────────────────────────────────

_PLANNER_SYSTEM = """\
You are a data-retrieval planner for a fitness analytics assistant.
Your ONLY job: produce the minimal complete list of tool calls needed to \
fully answer the user question.

Today is {today}.

Rules:
- For sleep trends, multi-day comparisons, or any sleep question spanning > 3 days: \
prefer get_garmin_wellness_trends with start_date/end_date over individual \
get_garmin_sleep calls per day.
- For intraday data over a date range (heart_rate_timeline, steps_timeline, \
sleep, hrv_status, daily_health): generate ONE call per day — do not skip days.
- For range-based tools (wellness_trends, body_battery, activities, \
training_trends, yearly_breakdown): use a single call covering the full range.
- For "fastest/best/furthest/slowest/most/least" superlative queries about \
activities: always set start_date="2010-01-01" so the full history is searched.
- Correlations (e.g. "HR without steps"): include calls for BOTH data sources.
- Always use explicit YYYY-MM-DD date strings. Never use relative terms.
- Maximum {max_steps} steps. For ranges > {max_steps} days, prefer aggregate \
tools or reduce to the most relevant days.
- If the question needs no data (greeting, clarification, math), return an \
empty steps list.

Available tools:
{tool_descriptions}

Reply ONLY with valid JSON, exactly this schema:
{{
  "reasoning": "<1-2 sentences: what data is needed and why>",
  "steps": [
    {{"tool": "<tool_name>", "args": {{}}, "label": "<short human label>"}},
    ...
  ]
}}
"""

_SYNTHESIZER_SYSTEM = """\
You are a precise sports and health data analyst.
You have been given results from multiple data-retrieval calls about the user's \
fitness data.

Rules:
- Be concise and data-driven. Include units and clear rounding.
- For running activities, always show pace (min/km) formatted as M:SS — \
use the pace_min_per_km field if present, otherwise compute from avg_speed_kmh.
- Only reference figures present in the tool results. Never fabricate data.
- null/None sleep fields (total_sleep_h, deep_h, rem_h, etc.) mean no tracking data \
for that night — do NOT treat them as zero hours. Exclude those nights from averages \
and note them explicitly as "no data".
- If a tool returned an error or no data, state that plainly.
- For temporal patterns, call out specific dates and times.
- Skip motivational filler unless the user explicitly asks for encouragement.
- Answer in the same language the user wrote in.

Today is {today}.
"""


# ── Helpers ───────────────────────────────────────────────────────────────────

def _tool_descriptions(tools: List[Dict]) -> str:
    lines = []
    for t in tools:
        fn    = t["function"]
        props = fn.get("parameters", {}).get("properties", {})
        param_str = ", ".join(
            f'{k} ({v.get("type", "any")}): {v.get("description", "")}'
            for k, v in props.items()
        ) or "none"
        lines.append(f'- {fn["name"]}: {fn["description"]}\n  params: {param_str}')
    return "\n".join(lines)


def _truncate(text: Optional[str], limit: int = 600) -> Optional[str]:
    if text and len(text) > limit:
        return text[:limit] + "…"
    return text


# ── Orchestrator ──────────────────────────────────────────────────────────────

class FitDashOrchestrator:
    """Stateless orchestrator — create once (e.g. via st.cache_resource), call run() per message."""

    def __init__(self) -> None:
        LOG_DIR.mkdir(exist_ok=True)

    # ── Public API ────────────────────────────────────────────────────────────

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
            trace   — full execution record (logged + available for UI debug panel)
        """
        client = get_openai_client()
        tools  = get_all_openai_tools()
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
        }

        try:
            # ── 1. Plan ───────────────────────────────────────────────────────
            _cb(progress_cb, "Planning data retrieval…")
            t0 = time.perf_counter()
            plan = self._plan(client, tools, user_input, history, today, trace)
            trace["timing"]["plan_ms"] = _ms(t0)

            if not plan:
                # No tools needed — go straight to synthesis
                _cb(progress_cb, "Generating answer…")
                t0 = time.perf_counter()
                answer = self._synthesize(client, user_input, history, [], today, trace)
                trace["timing"]["synth_ms"] = _ms(t0)
            else:
                # ── 2. Execute ────────────────────────────────────────────────
                _cb(progress_cb, f"Fetching {len(plan)} data source(s) in parallel…")
                t0 = time.perf_counter()
                results = self._execute(plan, trace, progress_cb)
                trace["timing"]["exec_ms"] = _ms(t0)
                trace["route_data"] = _extract_route_data(results)

                # ── 3. Synthesize ─────────────────────────────────────────────
                _cb(progress_cb, "Analysing results…")
                t0 = time.perf_counter()
                answer = self._synthesize(client, user_input, history, results, today, trace)
                trace["timing"]["synth_ms"] = _ms(t0)

        except Exception as exc:
            trace["error"] = str(exc)
            answer = f"Orchestrator error: {exc}"

        trace["answer"] = answer
        self._write_log(trace)
        return answer, trace

    # ── Phase 1: Planner ──────────────────────────────────────────────────────

    def _plan(
        self,
        client,
        tools: List[Dict],
        user_input: str,
        history: List[Dict],
        today: str,
        trace: Dict,
    ) -> List[Dict]:
        system = _PLANNER_SYSTEM.format(
            today=today,
            max_steps=MAX_PLAN_STEPS,
            tool_descriptions=_tool_descriptions(tools),
        )
        messages = [{"role": "system", "content": system}]
        for msg in history[-6:]:  # last 6 messages (3 turns)
            if msg["role"] in ("user", "assistant"):
                messages.append({"role": msg["role"], "content": msg["content"]})
        messages.append({"role": "user", "content": user_input})

        try:
            resp = client.chat.completions.create(
                model=MODEL,
                messages=messages,
                response_format={"type": "json_object"},
                temperature=0,
            )
        except Exception:
            # Fallback for models that don't support response_format
            resp = client.chat.completions.create(
                model=MODEL,
                messages=messages,
                temperature=0,
            )
        raw = resp.choices[0].message.content or "{}"
        # Extract the JSON object even if the model wrapped it in code fences
        raw = raw.strip()
        start = raw.find('{')
        end   = raw.rfind('}')
        if start != -1 and end != -1 and end > start:
            raw = raw[start:end + 1]
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            parsed = {}

        reasoning = parsed.get("reasoning", "")
        steps     = (parsed.get("steps") or [])[:MAX_PLAN_STEPS]

        trace["plan"] = {"reasoning": reasoning, "steps": steps}
        return steps

    # ── Phase 2: Executor ─────────────────────────────────────────────────────

    def _execute(
        self,
        plan: List[Dict],
        trace: Dict,
        progress_cb: Optional[Callable[[str], None]],
    ) -> List[Dict]:
        total     = len(plan)
        completed = 0
        results: List[Dict] = []

        def _run_one(step: Dict) -> Dict:
            t0    = time.perf_counter()
            tool  = step.get("tool", "")
            args  = step.get("args") or {}
            label = step.get("label", tool)
            try:
                result_text = call_tool(tool, args)
                error = None
            except Exception as exc:
                result_text = f"Error: {exc}"
                error = str(exc)
            return {
                "label":       label,
                "tool":        tool,
                "args":        args,
                "result":      result_text,
                "result_len":  len(result_text) if result_text else 0,
                "duration_ms": _ms(t0),
                "error":       error,
            }

        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
            future_map = {pool.submit(_run_one, step): step for step in plan}
            for future in as_completed(future_map):
                try:
                    rec = future.result(timeout=TOOL_TIMEOUT)
                except Exception as exc:
                    step = future_map[future]
                    rec = {
                        "label":       step.get("label", step.get("tool", "?")),
                        "tool":        step.get("tool", ""),
                        "args":        step.get("args") or {},
                        "result":      f"Timeout/error: {exc}",
                        "result_len":  0,
                        "duration_ms": TOOL_TIMEOUT * 1000,
                        "error":       str(exc),
                    }
                # as_completed yields on the main thread — no lock needed
                results.append(rec)
                trace["tool_calls"].append(rec)
                completed += 1
                _cb(progress_cb, f"Fetching data — {completed}/{total} done…")

        return results

    # ── Phase 3: Synthesizer ──────────────────────────────────────────────────

    def _synthesize(
        self,
        client,
        user_input: str,
        history: List[Dict],
        results: List[Dict],
        today: str,
        trace: Dict,
    ) -> str:
        system = _SYNTHESIZER_SYSTEM.format(today=today)

        tool_sections = []
        for r in results:
            header = f"### {r['label']}  [{r['tool']}]"
            body   = f"ERROR: {r['error']}" if r.get("error") else (r.get("result") or "")
            tool_sections.append(f"{header}\n{body}")

        data_block = "\n\n".join(tool_sections) if tool_sections else "(no data fetched)"

        messages = [{"role": "system", "content": system}]
        for msg in history[-6:]:
            if msg["role"] in ("user", "assistant"):
                messages.append({"role": msg["role"], "content": msg["content"]})
        messages.append({
            "role": "user",
            "content": f"{user_input}\n\n---\nData retrieved:\n\n{data_block}",
        })

        resp = client.chat.completions.create(
            model=MODEL,
            messages=messages,
            temperature=0.3,
        )
        return resp.choices[0].message.content or ""

    # ── Logging ───────────────────────────────────────────────────────────────

    def _write_log(self, trace: Dict) -> None:
        """Append a lean trace record to the JSONL log file. Never raises."""
        try:
            log_entry = {
                "run_id":     trace["run_id"],
                "ts":         trace["ts"],
                "user_input": trace["user_input"],
                "plan": {
                    "reasoning": (trace.get("plan") or {}).get("reasoning", ""),
                    "steps":     (trace.get("plan") or {}).get("steps", []),
                },
                "tool_calls": [
                    {
                        "label":       c["label"],
                        "tool":        c["tool"],
                        "args":        c["args"],
                        "duration_ms": c["duration_ms"],
                        "error":       c.get("error"),
                        # Truncate result body to keep log files manageable
                        "result_preview": _truncate(c.get("result"), 500),
                    }
                    for c in trace.get("tool_calls", [])
                ],
                "timing":  trace.get("timing", {}),
                "error":   trace.get("error"),
                "answer_preview": _truncate(trace.get("answer"), 300),
            }
            with open(LOG_FILE, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(log_entry, ensure_ascii=False) + "\n")
        except Exception:
            pass  # logging must never crash the main flow


# ── Small utilities ───────────────────────────────────────────────────────────

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
