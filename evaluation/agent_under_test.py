"""Adapts the Training Copilot into a ``predict_fn`` for the conversation simulator.

``ConversationSimulator`` drives a multi-turn conversation: each turn it calls
``predict_fn(messages=..., mlflow_session_id=...)`` with the full conversation so
far (last message is the simulated user's latest turn) and uses the returned
text as the assistant reply. We map that onto
``FitDashOrchestrator.run(user_input, history)`` and trace the turn in MLflow.

We pass ``user=None`` to ``run()`` so the eval does not touch any real user's
per-user memory (identity-only memory stays clean) and does not run the in-process
soul-distillation LLM under the eval's OpenAI routing.
"""

from __future__ import annotations

import json
from typing import Any, Callable

import mlflow
from mlflow.entities import SpanType

from core.orchestrator import FitDashOrchestrator

_RESULT_CHARS = 4000  # clip a tool result before stashing it on a span

_ORCH: FitDashOrchestrator | None = None


def get_orchestrator() -> FitDashOrchestrator:
    """Lazily build the orchestrator and warm its tool list once."""
    global _ORCH
    if _ORCH is None:
        orch = FitDashOrchestrator()
        orch.refresh_tools()
        _ORCH = orch
    return _ORCH


def orchestrator_reachable(orch: FitDashOrchestrator | None = None) -> int:
    """Number of tools the orchestrator can see (0 ⇒ A2A stack is not up)."""
    orch = orch or get_orchestrator()
    try:
        return orch.refresh_tools()
    except Exception:
        return 0


def _emit_tool_span(tc: dict[str, Any]) -> None:
    """Recreate one MCP tool call as a TOOL span under the active span."""
    tool = tc.get("tool") or "tool"
    err = tc.get("error")
    with mlflow.start_span(name=tool, span_type=SpanType.TOOL) as span:
        try:
            span.set_inputs(tc.get("args") or {})
        except Exception:
            pass
        result = tc.get("result")
        if not isinstance(result, str):
            result = json.dumps(result, default=str)
        span.set_outputs(result[:_RESULT_CHARS])
        # Attributes the grounding scorer reads to judge real tool usage.
        span.set_attribute("fitdash.tool", tool)
        span.set_attribute("fitdash.tool_ok", err in (None, ""))
        span.set_attribute("fitdash.tool_error", err or "")
        span.set_attribute("fitdash.duration_ms", tc.get("duration_ms", ""))


def _emit_copilot_spans(trace: dict[str, Any]) -> None:
    """Reconstruct the Copilot's specialist + tool-call structure into this turn's trace.

    The deep spans the Copilot really produced live in the *agent server* processes
    (the ``fitdash`` experiment), out of reach of this process. But ``run()`` returns
    the full ``trace`` dict (every ``tool_calls`` record with args/result/error, plus
    the ``agents`` that ran), so we rebuild it here as real child spans. This is what
    makes tool calls visible in the e2e experiment and gives the grounding scorer
    something concrete to inspect.

    Tools are nested under the specialist that used them when that can be inferred
    from the agent's ``data_summary`` (which lists the bare tool names); any tool we
    can't attribute is emitted directly under the root span.
    """
    try:
        agents = trace.get("agents", []) or []
        tool_calls = trace.get("tool_calls", []) or []
        assigned = [False] * len(tool_calls)

        for ag in agents:
            summary = ag.get("data_summary") or ""
            with mlflow.start_span(
                name=f"agent:{ag.get('agent', '?')}", span_type=SpanType.AGENT
            ) as aspan:
                aspan.set_attribute("fitdash.agent", ag.get("agent", ""))
                aspan.set_attribute("fitdash.phase", ag.get("phase", ""))
                aspan.set_attribute("fitdash.duration_ms", ag.get("duration_ms", ""))
                aspan.set_attribute("fitdash.data_summary", summary)
                for i, tc in enumerate(tool_calls):
                    if assigned[i]:
                        continue
                    bare = (tc.get("tool") or "").split("__")[-1]
                    if bare and bare in summary:
                        assigned[i] = True
                        _emit_tool_span(tc)

        # Tools we couldn't tie to a specialist still get their own span.
        for i, tc in enumerate(tool_calls):
            if not assigned[i]:
                _emit_tool_span(tc)
    except Exception:
        pass  # span reconstruction is best-effort; never break a conversation over it


def make_predict_fn(orch: FitDashOrchestrator) -> Callable[..., str]:
    """Return a traced ``predict_fn`` bound to the given orchestrator."""

    @mlflow.trace(name="fitdash_copilot", span_type="AGENT")
    def predict_fn(messages: list[dict[str, Any]], mlflow_session_id: str | None = None, **_: Any) -> str:
        # The simulator passes the whole conversation; the final message is the
        # user's current turn, everything before it is prior history.
        latest = messages[-1].get("content", "") if messages else ""
        history = [
            {"role": m.get("role", "user"), "content": m.get("content", "")}
            for m in messages[:-1]
        ]

        answer, trace = orch.run(latest, history, user=None)

        # Rebuild the specialist/tool-call spans so they're visible in MLflow and
        # inspectable by the grounding scorer.
        _emit_copilot_spans(trace)

        # Tag the trace with Copilot-side facts so the report can aggregate them
        # (which specialists ran, how many tools, latency, whether it errored).
        try:
            tools = [tc.get("tool") for tc in trace.get("tool_calls", []) if tc.get("tool")]
            agents = [a.get("agent") for a in trace.get("agents", []) if a.get("agent")]
            mlflow.update_current_trace(
                tags={
                    "fitdash.n_tool_calls": str(len(tools)),
                    "fitdash.tools": ",".join(tools)[:480],
                    "fitdash.agents": ",".join(agents)[:480],
                    "fitdash.latency_ms": str(trace.get("timing", {}).get("total_ms", "")),
                    "fitdash.error": "true" if trace.get("error") else "false",
                }
            )
        except Exception:
            pass  # tagging is best-effort; never break a conversation over telemetry

        return answer or ""

    return predict_fn
