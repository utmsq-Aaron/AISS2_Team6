"""Vendor-neutral LLM seam.

One place to construct the chat client and resolve the model — provider and model
come from config/env, never hard-coded. The rest of the core (the tool-use loop,
the agents) depends only on this seam, so swapping provider/model/gateway is a
config change, not a code change.

Today this targets an OpenAI-compatible endpoint (the KIT gateway with glm-4.7).
It deliberately does NOT import Streamlit, so the core runs standalone (CLI, API,
tests, separate service) — not only inside the Streamlit app.
"""

from __future__ import annotations

import os
from functools import lru_cache
from typing import Tuple

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()


@lru_cache(maxsize=1)
def _client() -> OpenAI:
    return OpenAI(
        api_key=os.getenv("OPENAI_API_KEY") or "",
        base_url=os.getenv("OPENAI_BASE_URL") or None,
    )


def model() -> str:
    return os.getenv("AGENT_MODEL") or "gpt-4o"


def get_llm_client() -> Tuple[OpenAI, str]:
    """Return (client, model_name). The single LLM entry point for the core."""
    return _client(), model()
