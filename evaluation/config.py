"""Central config for the end-to-end evaluation framework.

This package is intentionally separate from the main FitDash codebase: it is a
*consumer* of `core.orchestrator`, never imported by it. Nothing here changes
the behaviour of the Training Copilot — it only drives it and scores the result.

The one piece of real wiring this module owns is **model routing**. The
evaluation uses two OpenAI models, both on the *official* OpenAI API:

  • simulator / report writer : gpt-5.4-mini-2026-03-17   (drives personas, writes the HTML report)
  • scorers / judges          : gpt-5.4-nano-2026-03-17   (the LLM judges)

MLflow resolves an ``openai:/<model>`` URI through its native OpenAI provider,
which reads ``OPENAI_API_KEY`` and (optionally) ``OPENAI_API_BASE`` /
``OPENAI_BASE_URL``. The repo's ``.env`` points those at the KIT gateway (which
does not serve these models), so :func:`apply_openai_routing` rewrites the
process environment to use the *official* key (``OPENAI_OFFICIAL_API_KEY``) with
the default api.openai.com base.

This only affects LLM calls made **in this process** (the simulator, the judges
and the report). The Copilot's own LLM calls happen in the separate A2A agent
server processes, which keep their own ``.env`` config — they are untouched.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import dotenv_values

# Log traces synchronously in this process. The simulator logs each conversation's
# `expectations` to its first trace immediately after the turn; with MLflow's
# default async trace export that REST write can race the not-yet-flushed trace
# ("RESOURCE_DOES_NOT_EXIST"), which aborts the conversation and yields no traces.
# Synchronous export removes the race. Set before any `import mlflow`.
os.environ.setdefault("MLFLOW_ENABLE_ASYNC_TRACE_LOGGING", "false")

# ── Paths ───────────────────────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parents[1]          # repo root (AISS2_Team6/)
ENV_PATH = ROOT / ".env"
REPORTS_DIR = Path(__file__).resolve().parent / "reports"

# ── Models (per the task brief) ───────────────────────────────────────────────
# The simulated persona "user" agent.
SIMULATOR_MODEL_RAW = "gpt-5.4-mini-2026-03-17"
# Generic report writer (used by the real-user report, run_users.py).
REPORT_MODEL_RAW = "gpt-5.4-mini-2026-03-17"
# The LLM judges that produce the scores.
JUDGE_MODEL_RAW = "gpt-5.4-nano-2026-03-17"
# The persona e2e report (report.py) fills a fixed HTML template with deterministic
# data + small, field-scoped model completions — those run on nano.
PERSONA_REPORT_MODEL_RAW = "gpt-5.4-nano-2026-03-17"

# MLflow-style URIs (``openai:/<model>``) consumed by the simulator + scorers.
SIMULATOR_MODEL = f"openai:/{SIMULATOR_MODEL_RAW}"
JUDGE_MODEL = f"openai:/{JUDGE_MODEL_RAW}"

DEFAULT_TRACKING_URI = "http://127.0.0.1:5001"
EXPERIMENT_PREFIX = "fitdash-e2e"

_ROUTED = False


def _env(cfg: dict, key: str) -> str:
    """Read a key from the parsed .env, falling back to the live environment."""
    val = cfg.get(key)
    if val is None:
        val = os.environ.get(key)
    return (val or "").strip()


def apply_openai_routing() -> str:
    """Point this process's ``openai:/`` model calls at the official OpenAI API.

    Idempotent and safe to call repeatedly (e.g. again right before evaluation,
    in case importing the Copilot re-loaded the KIT-gateway ``.env`` over us).

    Returns the resolved base URL (``""`` means api.openai.com default).
    """
    global _ROUTED
    cfg = dotenv_values(ENV_PATH) if ENV_PATH.exists() else {}

    official_key = _env(cfg, "OPENAI_OFFICIAL_API_KEY")
    if not official_key:
        raise RuntimeError(
            "OPENAI_OFFICIAL_API_KEY is not set in .env — the evaluation needs the "
            "official OpenAI key to reach gpt-5.4-mini / gpt-5.4-nano."
        )

    os.environ["OPENAI_API_KEY"] = official_key
    # Drop any KIT-gateway base so MLflow's openai provider hits api.openai.com.
    os.environ.pop("OPENAI_API_BASE", None)
    os.environ.pop("OPENAI_BASE_URL", None)
    base = _env(cfg, "OPENAI_OFFICIAL_BASE_URL")
    if base:
        os.environ["OPENAI_BASE_URL"] = base

    _ROUTED = True
    return base


def resolve_tracking_uri() -> str:
    """The MLflow tracking server to log experiments/traces to."""
    cfg = dotenv_values(ENV_PATH) if ENV_PATH.exists() else {}
    return _env(cfg, "MLFLOW_TRACKING_URI") or DEFAULT_TRACKING_URI


def openai_client():
    """A raw official-OpenAI SDK client (used by the report writer)."""
    if not _ROUTED:
        apply_openai_routing()
    from openai import OpenAI

    return OpenAI(
        api_key=os.environ["OPENAI_API_KEY"],
        base_url=os.environ.get("OPENAI_BASE_URL") or None,
    )
