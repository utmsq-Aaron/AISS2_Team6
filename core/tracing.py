"""MLflow tracing seam — turn on autologging once per process.

The chat engine is several independent processes (orchestrator + four specialists,
each its own A2A server, plus the FastAPI seam). Each calls ``setup_tracing(name)``
once at startup; from then on every LangGraph / LangChain ``ainvoke`` — and every
raw OpenAI-SDK call (the chart service) — is traced to the MLflow tracking server
as a span tree (the agent run, its LLM calls, and each tool / MCP call nested
underneath).

Each agent wraps its run in ``trace_span(name, **tags)`` so the autologged LLM /
tool spans nest under one labelled root span and the agent/question tags export
*with* the trace (atomic — no post-hoc server call that could race MLflow's async
export, and the request thread never blocks on the tracking server).

Like ``core.llm``, config is read live from ``.env`` so it reaches the separate
long-lived agent processes without a code change:

  MLFLOW_TRACKING_URI   default ``http://127.0.0.1:5001``  (the server the scripts start;
                        :5001 not :5000 — macOS Control Center/AirPlay squats on :5000)
  MLFLOW_EXPERIMENT     default ``fitdash``                (one shared experiment)
  MLFLOW_TRACING        set ``0`` / ``false`` / ``off`` to disable tracing entirely

Every call is best-effort: a missing ``mlflow`` package or an unreachable tracking
server is logged once and then ignored — tracing never takes the app down.
"""

from __future__ import annotations

from contextlib import contextmanager

from core.llm import _env

_active = False


def tracking_uri() -> str:
    return _env("MLFLOW_TRACKING_URI", "http://127.0.0.1:5001")


def experiment() -> str:
    return _env("MLFLOW_EXPERIMENT", "fitdash")


def enabled() -> bool:
    return _env("MLFLOW_TRACING", "1").strip().lower() not in ("0", "false", "no", "off")


def setup_tracing(service: str) -> bool:
    """Point this process at the MLflow server and enable autologging (idempotent).

    Returns True if tracing is active. Safe to call when MLflow is absent or the
    server is down — it degrades to a no-op and the rest of the process runs.
    """
    global _active
    if _active:
        return True
    if not enabled():
        print(f"[{service}] MLflow tracing disabled (MLFLOW_TRACING=off)", flush=True)
        return False
    try:
        import mlflow
        import mlflow.langchain

        mlflow.set_tracking_uri(tracking_uri())
        mlflow.set_experiment(experiment())
        # LangGraph / LangChain agents: one trace per ainvoke, with the LLM call and
        # every tool (specialist ask_* / MCP) call nested as child spans.
        mlflow.langchain.autolog()
        # The raw OpenAI-SDK path (chart service, model listing) — best-effort.
        try:
            import mlflow.openai

            mlflow.openai.autolog()
        except Exception:  # noqa: BLE001
            pass
        _active = True
        print(f"[{service}] MLflow tracing → {tracking_uri()} "
              f"(experiment={experiment()})", flush=True)
        return True
    except Exception as exc:  # noqa: BLE001 — tracing must never break the app
        print(f"[{service}] MLflow tracing unavailable: {type(exc).__name__}: {exc}",
              flush=True)
        return False


@contextmanager
def trace_span(name: str, **tags):
    """Wrap an agent run in a labelled root span and tag the trace.

    The autologged LLM / tool spans nest under this span, so each agent run is one
    trace named after the agent (``name``) and tagged (``service``, ``role``,
    ``question`` …). Tags are written via ``update_current_trace`` so they export
    together with the trace. A no-op when tracing is inactive, and any MLflow error
    degrades to running the body untraced — the body always executes exactly once.
    """
    if not _active:
        yield
        return
    try:
        import mlflow
        cm = mlflow.start_span(name=name)
        cm.__enter__()
    except Exception:  # noqa: BLE001 — never block the agent on tracing setup
        yield
        return
    try:
        try:
            clean = {k: str(v)[:250] for k, v in tags.items() if v not in (None, "")}
            if clean:
                mlflow.update_current_trace(tags=clean)
        except Exception:  # noqa: BLE001
            pass
        yield
    finally:
        try:
            cm.__exit__(None, None, None)
        except Exception:  # noqa: BLE001
            pass
