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

from typing import Any, Callable

import mlflow

from core.orchestrator import FitDashOrchestrator

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
