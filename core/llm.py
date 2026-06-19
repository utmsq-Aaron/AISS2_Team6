"""Vendor-neutral LLM seam.

One place to construct the chat client and resolve the model — provider and model
come from config/env, never hard-coded. The rest of the core (the agents, the
chart service) depends only on this seam, so swapping provider/model/gateway is a
config change, not a code change.

Providers (``LLM_PROVIDER`` env):
  * ``openai`` (default) — any OpenAI-compatible endpoint, i.e. the **KIT gateway**
    via ``OPENAI_BASE_URL`` / ``OPENAI_API_KEY`` / ``AGENT_MODEL`` (+ ``AGENT_LLM_MODEL``).
  * ``openai_official`` — **official OpenAI** (api.openai.com) via ``OPENAI_OFFICIAL_API_KEY``
    / ``OPENAI_MODEL`` (+ optional ``OPENAI_OFFICIAL_BASE_URL`` for an Azure/proxy).
  * ``gemini`` / ``google`` — Google Gemini. The LangChain agent path uses the native
    ``langchain-google-genai`` client; the raw OpenAI-SDK path (chart service) uses
    Gemini's OpenAI-compatible endpoint. Key from ``GEMINI_API_KEY`` (or
    ``GOOGLE_API_KEY``), model from ``GEMINI_MODEL`` (default a free flash).

**Live config:** every resolution re-reads the ``.env`` file, so changing the
provider/model from the Settings UI takes effect on the next request — even
though the agents run as separate long-lived processes. (We intentionally do NOT
cache the client; construction is cheap and makes no network call.) Values set
only in the shell environment (not in ``.env``) are still honoured as a fallback.

Deliberately imports NO Streamlit, so the core runs standalone.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Tuple

from dotenv import dotenv_values, load_dotenv
from openai import OpenAI

_ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(_ENV_PATH)

# Gemini's OpenAI-compatible base URL (for the raw OpenAI-SDK path).
_GEMINI_OPENAI_BASE = "https://generativelanguage.googleapis.com/v1beta/openai/"
_OPENAI_OFFICIAL_BASE = "https://api.openai.com/v1"  # explicit so the SDK doesn't fall back to OPENAI_BASE_URL
_DEFAULT_GEMINI_MODEL = "gemini-2.0-flash"   # free-tier flash; override with GEMINI_MODEL
_DEFAULT_OPENAI_MODEL = "gpt-4o-mini"        # official OpenAI; override with OPENAI_MODEL


def _env(key: str, default: str = "") -> str:
    """Read ``key`` preferring the live ``.env`` file, then the process env.

    Reading the file each call is what lets a Settings-UI edit reach the separate
    agent processes without a restart. Shell-exported vars not present in ``.env``
    still work via the os.getenv fallback.
    """
    try:
        file_val = dotenv_values(_ENV_PATH).get(key)
    except Exception:
        file_val = None
    val = file_val if (file_val not in (None, "")) else os.getenv(key)
    return val if (val not in (None, "")) else default


def provider() -> str:
    """The active model provider, normalised. Default: KIT/OpenAI-compatible.

    Returns one of: ``openai`` (KIT / any OpenAI-compatible gateway),
    ``openai_official`` (api.openai.com), ``gemini``.
    """
    p = _env("LLM_PROVIDER", "openai").strip().lower()
    if p in ("gemini", "google"):
        return "gemini"
    if p in ("openai_official", "official", "oai"):
        return "openai_official"
    return "openai"


def _gemini_key() -> str:
    return _env("GEMINI_API_KEY") or _env("GOOGLE_API_KEY")


def model() -> str:
    """Model for the raw OpenAI-SDK path (chart service), per provider."""
    p = provider()
    if p == "gemini":
        return _env("GEMINI_MODEL", _DEFAULT_GEMINI_MODEL)
    if p == "openai_official":
        return _env("OPENAI_MODEL", _DEFAULT_OPENAI_MODEL)
    return _env("AGENT_MODEL", "gpt-4o")


def _agent_model() -> str:
    """Model for the LangGraph agent layer, per provider.

    For KIT, AGENT_LLM_MODEL overrides AGENT_MODEL (the agents make many more calls
    and benefit from a stable model, e.g. kit.gpt-4.1).
    """
    p = provider()
    if p == "gemini":
        return _env("GEMINI_MODEL", _DEFAULT_GEMINI_MODEL)
    if p == "openai_official":
        return _env("OPENAI_MODEL", _DEFAULT_OPENAI_MODEL)
    return _env("AGENT_LLM_MODEL") or _env("AGENT_MODEL", "gpt-4o")


# ── Raw OpenAI-SDK client (chart service) ─────────────────────────────────────

def client_for(prov: str) -> OpenAI:
    """Build a raw OpenAI-SDK client for an EXPLICIT provider, using its own creds.

    Used to list models for whatever provider the Settings UI currently shows,
    which may differ from the active LLM_PROVIDER.
    """
    p = (prov or "").strip().lower()
    if p in ("gemini", "google"):
        return OpenAI(api_key=_gemini_key(), base_url=_GEMINI_OPENAI_BASE)
    if p in ("openai_official", "official", "oai"):
        return OpenAI(api_key=_env("OPENAI_OFFICIAL_API_KEY"),
                      base_url=_env("OPENAI_OFFICIAL_BASE_URL") or _OPENAI_OFFICIAL_BASE)
    return OpenAI(api_key=_env("OPENAI_API_KEY"), base_url=_env("OPENAI_BASE_URL") or None)


def _client() -> OpenAI:
    return client_for(provider())


def get_llm_client() -> Tuple[OpenAI, str]:
    """Return (client, model_name) for the raw OpenAI-SDK path."""
    return _client(), model()


# ── LangChain chat model (the LangGraph agent layer) ──────────────────────────

def get_chat_model():
    """The single LangChain chat-model entry point for the agent layer.

    Rebuilt per call (no cache) so a provider/model change from the Settings UI
    applies on the next request. LangChain imports are lazy so importing core.llm
    doesn't pull them into processes that only need the raw OpenAI client.
    """
    p = provider()
    if p == "gemini":
        from langchain_google_genai import ChatGoogleGenerativeAI
        return ChatGoogleGenerativeAI(
            model=_agent_model(),
            google_api_key=_gemini_key(),
            temperature=0,
            max_retries=3,
        )
    from langchain_openai import ChatOpenAI
    if p == "openai_official":
        return ChatOpenAI(
            model=_agent_model(),
            base_url=_env("OPENAI_OFFICIAL_BASE_URL") or _OPENAI_OFFICIAL_BASE,
            api_key=_env("OPENAI_OFFICIAL_API_KEY"),
            temperature=0,
            timeout=120,
            max_retries=3,
        )
    return ChatOpenAI(
        model=_agent_model(),
        base_url=_env("OPENAI_BASE_URL") or None,
        api_key=_env("OPENAI_API_KEY"),
        temperature=0,
        timeout=120,
        max_retries=3,
    )
