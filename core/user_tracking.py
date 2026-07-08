"""Per-user MLflow experiment tracking.

Every chat turn a logged-in user has is logged as one MLflow **trace** into that
user's *own* experiment — ``fitdash-user-<slug>`` — so each account can be tracked
and scored independently (see ``evaluation/run_users.py`` for the report). The
trace is routed to the per-user experiment via ``start_span(trace_destination=…)``
(MLflow 3), which is race-free and independent of whatever experiment the process's
global tracing is pointed at (the deep agent spans still go to ``fitdash``).

Each turn's trace mirrors the e2e-evaluation shape so the same scorers/report logic
applies to real users:
  * a root ``fitdash_copilot`` span whose inputs carry the user message and whose
    output is the assistant answer,
  * the Copilot's MCP tool calls reconstructed as ``TOOL`` child spans (with the
    ``fitdash.tool*`` attributes the grounding scorer reads),
  * trace tags: ``session_id`` (= chat id), ``turn``, ``user``, and the
    ``fitdash.*`` rollup tags (tools, agents, latency, error).

Everything is best-effort: missing mlflow / unreachable server / any error degrades
to a no-op and never affects the chat response.
"""

from __future__ import annotations

import json
import re
import threading
from typing import Any, Dict, Optional

from core.llm import _env
from core.tracing import enabled, tracking_uri

_RESULT_CHARS = 4000
_exp_ids: Dict[str, str] = {}      # experiment name → id (cached)
_exp_lock = threading.Lock()


def _slug(user: str) -> str:
    return re.sub(r"[^a-z0-9_-]+", "-", (user or "").strip().lower()).strip("-") or "anon"


def experiment_prefix() -> str:
    return _env("USER_EXPERIMENT_PREFIX", "fitdash-user")


def experiment_name(user: str) -> str:
    return f"{experiment_prefix()}-{_slug(user)}"


def _experiment_id(user: str) -> Optional[str]:
    """Get-or-create the user's experiment, returning its id (cached)."""
    name = experiment_name(user)
    cached = _exp_ids.get(name)
    if cached:
        return cached
    try:
        import mlflow
        from mlflow import MlflowClient

        mlflow.set_tracking_uri(tracking_uri())
        client = MlflowClient()
        with _exp_lock:
            cached = _exp_ids.get(name)
            if cached:
                return cached
            exp = client.get_experiment_by_name(name)
            exp_id = exp.experiment_id if exp else client.create_experiment(name)
            _exp_ids[name] = exp_id
            return exp_id
    except Exception as exc:  # noqa: BLE001 — tracking is best-effort
        print(f"[user_tracking] experiment unavailable for {name}: {exc}", flush=True)
        return None


def _emit_tool_span(mlflow, SpanType, tc: Dict[str, Any]) -> None:
    tool = tc.get("tool") or "tool"
    err = tc.get("error")
    with mlflow.start_span(name=tool, span_type=SpanType.TOOL) as span:
        try:
            span.set_inputs(tc.get("args") or {})
        except Exception:  # noqa: BLE001
            pass
        result = tc.get("result")
        if not isinstance(result, str):
            result = json.dumps(result, default=str)
        span.set_outputs((result or "")[:_RESULT_CHARS])
        span.set_attribute("fitdash.tool", tool)
        span.set_attribute("fitdash.tool_ok", err in (None, ""))
        span.set_attribute("fitdash.tool_error", err or "")
        span.set_attribute("fitdash.duration_ms", tc.get("duration_ms", ""))


def _emit_copilot_spans(mlflow, SpanType, trace: Dict[str, Any]) -> None:
    """Rebuild the specialist + tool-call structure as child spans (best-effort)."""
    try:
        agents = trace.get("agents", []) or []
        tool_calls = trace.get("tool_calls", []) or []
        assigned = [False] * len(tool_calls)
        for ag in agents:
            summary = ag.get("data_summary") or ""
            with mlflow.start_span(name=f"agent:{ag.get('agent', '?')}", span_type=SpanType.AGENT) as aspan:
                aspan.set_attribute("fitdash.agent", ag.get("agent", ""))
                aspan.set_attribute("fitdash.duration_ms", ag.get("duration_ms", ""))
                aspan.set_attribute("fitdash.data_summary", summary)
                for i, tc in enumerate(tool_calls):
                    if assigned[i]:
                        continue
                    bare = (tc.get("tool") or "").split("__")[-1]
                    if bare and bare in summary:
                        assigned[i] = True
                        _emit_tool_span(mlflow, SpanType, tc)
        for i, tc in enumerate(tool_calls):
            if not assigned[i]:
                _emit_tool_span(mlflow, SpanType, tc)
    except Exception:  # noqa: BLE001
        pass


def log_turn(user: str, chat_id: str, turn_index: int, question: str,
             answer: str, trace: Optional[Dict[str, Any]]) -> None:
    """Log one chat turn as a trace in the user's own experiment. Best-effort."""
    if not user or not enabled():
        return
    exp_id = _experiment_id(user)
    if not exp_id:
        return
    trace = trace or {}
    try:
        import mlflow
        from mlflow.entities import SpanType

        try:  # MLflow ≥3.5 location class; fall back to the older destination
            from mlflow.entities.trace_location import MlflowExperimentLocation
            dest = MlflowExperimentLocation(experiment_id=exp_id)
        except Exception:  # noqa: BLE001
            from mlflow.tracing.destination import MlflowExperiment
            dest = MlflowExperiment(experiment_id=exp_id)

        with mlflow.start_span(name="fitdash_copilot", span_type="AGENT",
                               trace_destination=dest) as root:
            try:
                root.set_inputs({"messages": [{"role": "user", "content": question}]})
                root.set_outputs(answer or "")
            except Exception:  # noqa: BLE001
                pass
            _emit_copilot_spans(mlflow, SpanType, trace)
            try:
                tools = [tc.get("tool") for tc in trace.get("tool_calls", []) if tc.get("tool")]
                agents = [a.get("agent") for a in trace.get("agents", []) if a.get("agent")]
                mlflow.update_current_trace(tags={
                    "user": user,
                    "session_id": chat_id or "adhoc",
                    "turn": str(turn_index),
                    "fitdash.n_tool_calls": str(len(tools)),
                    "fitdash.tools": ",".join(tools)[:480],
                    "fitdash.agents": ",".join(agents)[:480],
                    "fitdash.latency_ms": str((trace.get("timing") or {}).get("total_ms", "")),
                    "fitdash.error": "true" if trace.get("error") else "false",
                    "fitdash.has_route": "true" if trace.get("route_data") else "false",
                })
            except Exception:  # noqa: BLE001
                pass
    except Exception as exc:  # noqa: BLE001 — never break chat over telemetry
        print(f"[user_tracking] log_turn skipped: {type(exc).__name__}: {exc}", flush=True)
