"""FitDash orchestrator — thin A2A client adapter over the multi-agent engine.

The old single tool-use loop is gone; the engine is now a LangGraph + A2A
multi-agent system (Orchestrator Agent on :9000 → specialist agents :9001–:9004).
This class is a thin client to that orchestrator, preserving the public contract
the UI, the FastAPI SSE layer and the Telegram bridge depend on:

    run(user_input, history, progress_cb=None, text_cb=None) -> (answer, trace)
    refresh_tools() -> int

The orchestrator agent assembles the full ``trace`` (route_data, chart_hints,
tool_calls, agents, …) and returns it as a DataPart; here we just relay it. The
synchronous facade (callers run this in threads) bridges to the async A2A client
via the same fresh-event-loop helper ToolHost uses.
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from core.a2a_client import call_agent, fetch_agent_card
from core.agent_trace import build_trace
from core.config import A2A_AGENTS
from core.host import _run

LOG_DIR = Path(".logs")
LOG_FILE = LOG_DIR / "agent_interactions.jsonl"
HISTORY_WINDOW = 8           # message pairs flattened into the A2A prompt
HISTORY_CHAR_LIMIT = 1500    # chars per history message before truncation


class FitDashOrchestrator:
    """Drop-in engine: same run()/refresh_tools() surface, A2A multi-agent inside."""

    def __init__(self, host: Optional[Any] = None) -> None:  # host accepted+ignored
        LOG_DIR.mkdir(exist_ok=True)
        self._url = A2A_AGENTS["orchestrator"]
        # UI reads len(self._tools) to decide the "no servers reachable" banner.
        # Populated lazily (a successful run or refresh_tools sets it non-empty) so
        # construction never blocks on the network.
        self._tools: List[Any] = []

    def refresh_tools(self) -> int:
        """Ping the orchestrator agent; return a positive count if it's reachable."""
        try:
            reachable = _run(_orchestrator_reachable(self._url))
        except Exception:
            reachable = False
        self._tools = [1] if reachable else []
        return len(self._tools)

    def run(
        self,
        user_input: str,
        history: List[Dict],
        progress_cb: Optional[Callable[[str], None]] = None,
        text_cb: Optional[Callable[[Optional[str]], None]] = None,
        user: Optional[str] = None,
    ) -> Tuple[str, Dict]:
        # Per-user memory (only when an authenticated user is known — the React
        # path passes it; Telegram/Streamlit single-user paths leave it None).
        mem = _get_memory(user)
        prompt = _flatten_history(history, user_input)
        if mem is not None:
            preamble = mem.context_block(user_input)
            if preamble:
                prompt = f"{preamble}\n\n---\n\n{prompt}"

        try:
            answer, artifacts = _run(
                call_agent(self._url, prompt, on_status=progress_cb, on_token=text_cb)
            )
        except Exception as exc:  # orchestrator unreachable / transport error
            trace = build_trace(
                user_input=user_input, run_id=uuid.uuid4().hex[:8],
                specialist_artifacts=[], answer=f"Orchestrator unavailable: {exc}",
                total_ms=0, error=str(exc),
            )
            return trace["answer"], trace

        # A successful round-trip means the agent layer is up → clear the banner.
        self._tools = [1]
        trace = _pick_trace(artifacts)
        if trace is None:
            trace = build_trace(
                user_input=user_input, run_id=uuid.uuid4().hex[:8],
                specialist_artifacts=[], answer=answer, total_ms=0, error=None,
            )
        # Keep the trace's question the *real* user message, not the memory-augmented
        # prompt we sent to the agent.
        trace["user_input"] = user_input
        trace.setdefault("question", user_input)
        if not trace.get("answer"):
            trace["answer"] = answer
        _write_log(trace)

        final = trace.get("answer") or answer
        if mem is not None and final and not trace.get("error"):
            mem.remember(user_input, final)  # best-effort; never raises
        return final, trace


# ── Helpers ───────────────────────────────────────────────────────────────────

def _get_memory(user: Optional[str]):
    """A UserMemory for ``user`` if memory is enabled, else None (best-effort)."""
    if not user:
        return None
    try:
        from core.user_memory import get_user_memory, memory_enabled
        return get_user_memory(user) if memory_enabled() else None
    except Exception as exc:  # noqa: BLE001 — memory must never break chat
        print(f"[orchestrator] user memory unavailable: {exc}", flush=True)
        return None


def _flatten_history(history: List[Dict], user_input: str) -> str:
    """A2A messages are single-shot; fold recent turns into one prompt."""
    lines: List[str] = []
    for m in (history or [])[-HISTORY_WINDOW:]:
        role = m.get("role")
        content = (m.get("content") or "")[:HISTORY_CHAR_LIMIT]
        if role in ("user", "assistant") and content:
            lines.append(f"{role}: {content}")
    convo = "\n".join(lines)
    if convo:
        return f"Conversation so far:\n{convo}\n\nCurrent user message:\n{user_input}"
    return user_input


def _pick_trace(artifacts: List[Dict]) -> Optional[Dict]:
    """Find the orchestrator's 'trace' DataPart among returned data artifacts."""
    for a in artifacts or []:
        if isinstance(a, dict) and "tool_calls" in a and "run_id" in a:
            return a
    return None


async def _orchestrator_reachable(url: str) -> bool:
    try:
        await fetch_agent_card(url, timeout=4.0)
        return True
    except Exception:
        return False


def _write_log(trace: Dict) -> None:
    """One summary line per turn — mirrors the old agent_interactions.jsonl."""
    try:
        tool_calls = trace.get("tool_calls") or []
        entry = {
            "run_id":       trace.get("run_id"),
            "ts":           trace.get("ts") or (datetime.utcnow().isoformat() + "Z"),
            "model":        os.getenv("AGENT_LLM_MODEL") or os.getenv("AGENT_MODEL", ""),
            "user_input":   trace.get("user_input"),
            "n_tool_calls": len(tool_calls),
            "tools":        [r.get("tool") for r in tool_calls],
            "agents":       [a.get("agent") for a in (trace.get("agents") or [])],
            "error":        trace.get("error"),
            "has_route":    bool(trace.get("route_data")),
            "answer":       trace.get("answer") or "",
        }
        with open(LOG_FILE, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        pass
