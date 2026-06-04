"""Shared utilities for HealthBot sub-agents.

Agents import from here instead of ui.shared so they can run standalone
(without a Streamlit context) as well as in-process inside the Streamlit app.
"""

import json
import os
import re
import sys
import time
import random
from typing import Optional


def get_llm_client():
    """Return (openai_client, model_name).  Prefers Streamlit-cached singleton."""
    try:
        from ui.shared import get_openai_client, MODEL
        return get_openai_client(), MODEL
    except Exception:
        from openai import OpenAI
        client = OpenAI(
            api_key  = os.getenv("OPENAI_API_KEY") or "",
            base_url = os.getenv("OPENAI_BASE_URL") or None,
        )
        return client, os.getenv("AGENT_MODEL") or "gpt-4o"


def llm_call(
    system: str,
    user: str,
    temperature: float = 0,
    json_mode: bool = False,
    history: Optional[list] = None,
    max_retries: int = 4,
) -> str:
    """Single LLM call with exponential backoff for rate-limit and transient errors."""
    client, model = get_llm_client()
    messages = [{"role": "system", "content": system}]
    for msg in (history or [])[-10:]:
        if msg.get("role") in ("user", "assistant"):
            content = msg["content"] or ""
            # Cap per-history-message length so bulky data answers don't pollute context.
            if len(content) > 1500:
                content = content[:1500] + "…[trimmed]"
            messages.append({"role": msg["role"], "content": content})
    messages.append({"role": "user", "content": user})

    kwargs = dict(model=model, messages=messages, temperature=temperature)
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}

    last_exc = None
    for attempt in range(max_retries):
        try:
            resp = client.chat.completions.create(**kwargs)
            return resp.choices[0].message.content or ""
        except Exception as exc:
            last_exc = exc
            status = getattr(getattr(exc, "response", None), "status_code", None)
            # Retry on rate-limit (429), server errors (5xx), and transient 404s
            if status in (429, 500, 502, 503, 504) or (status == 404 and attempt < 2):
                delay = (2 ** attempt) + random.uniform(0, 1)
                time.sleep(delay)
                continue
            # For json_mode failures, retry without it (some providers don't support it)
            if json_mode and attempt == 0:
                kwargs.pop("response_format", None)
                continue
            break

    raise RuntimeError(sanitize_error(f"LLM call failed after {max_retries} attempts: {last_exc}"))


_CREDENTIAL_PATTERNS = [
    (re.compile(r'Bearer [A-Za-z0-9_\-\.]{20,}'),  'Bearer [REDACTED]'),
    (re.compile(r'access_token=[^&\s"]+'),           'access_token=[REDACTED]'),
    (re.compile(r'refresh_token=[^&\s"]+'),          'refresh_token=[REDACTED]'),
    (re.compile(r'client_secret=[^&\s"]+'),          'client_secret=[REDACTED]'),
]


def sanitize_error(msg: str) -> str:
    """Strip OAuth tokens and secrets from error messages before logging or LLM context."""
    for pattern, replacement in _CREDENTIAL_PATTERNS:
        msg = pattern.sub(replacement, msg)
    return msg


def truncate(text: str, limit: int = 2000) -> str:
    if text and len(text) > limit:
        return text[:limit] + "…[truncated]"
    return text or ""


# Shared flythrough keyword set — used by the orchestrator (routing) and FlyoverAgent (detection).
# Both must agree on what constitutes a flythrough request so routing and agent logic stay in sync.
FLYTHROUGH_KEYWORDS: frozenset = frozenset({
    "flythrough", "flyover", "fly through", "fly over",
    "3d fly", "3d video", "route video", "flug", "überflug",
})


def extract_json(raw: str) -> dict:
    """Parse JSON even if wrapped in markdown code fences.

    Logs a warning to stderr when the LLM returned content but parsing failed,
    so production logs surface LLM misbehavior rather than silently dropping data.
    """
    if not raw or not raw.strip():
        return {}
    cleaned = raw.strip()
    start = cleaned.find("{")
    end   = cleaned.rfind("}")
    if start != -1 and end > start:
        cleaned = cleaned[start:end + 1]
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError as exc:
        print(f"[_base] extract_json failed ({exc}): {raw[:200]}", file=sys.stderr)
        return {}
