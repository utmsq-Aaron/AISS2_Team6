"""A2A client helper — call a remote agent, surface progress, collect artifacts.

Used in two places:
  * the orchestrator delegates to each specialist (one call per ``ask_*`` tool);
  * ``core.orchestrator.FitDashOrchestrator`` calls the orchestrator agent.

Speaks the tutorial-canonical ``A2AClient`` + ``SendStreamingMessageRequest`` path
verified against a2a-sdk 0.3.x. The streaming response is demuxed into:
  * status-update messages (non-final)  → ``on_status`` (progress strings)
  * status-update (final/completed)      → the answer text (fallback)
  * artifact-update with text parts      → ``on_token`` (streamed answer chunks)
  * artifact-update with data parts       → returned ``data_artifacts``
"""

from __future__ import annotations

import uuid
from typing import Any, Callable, Dict, List, Optional, Tuple

import httpx

from a2a.client import A2ACardResolver, A2AClient
from a2a.types import (Message, MessageSendParams, Part, Role,
                       SendStreamingMessageRequest, TaskState, TextPart)
from a2a.utils import get_data_parts, get_message_text, get_text_parts

DEFAULT_TIMEOUT = 240.0
_FINAL_STATES = {TaskState.completed, TaskState.failed, TaskState.canceled, TaskState.rejected}


async def call_agent(
    url: str,
    prompt: str,
    *,
    on_status: Optional[Callable[[str], None]] = None,
    on_token: Optional[Callable[[str], None]] = None,
    timeout: float = DEFAULT_TIMEOUT,
    metadata: Optional[Dict[str, Any]] = None,
) -> Tuple[str, List[Dict[str, Any]]]:
    """Send ``prompt`` to the A2A agent at ``url``; return ``(answer, data_artifacts)``.

    ``data_artifacts`` is the list of DataPart payloads the agent attached (for a
    specialist: its ``{"agent", "tool_calls", ...}`` record; for the orchestrator:
    the assembled ``trace`` dict). ``metadata`` rides on the A2A Message (used to
    carry the delegation depth for the peer-to-peer mesh; read server-side via
    ``context.message.metadata``).
    """
    base = url.rstrip("/")
    token_chunks: List[str] = []
    data_artifacts: List[Dict[str, Any]] = []
    final_answer = ""

    async with httpx.AsyncClient(timeout=timeout) as hc:
        card = await A2ACardResolver(hc, base_url=base).get_agent_card()
        client = A2AClient(hc, agent_card=card)
        msg = Message(role=Role.user,
                      parts=[Part(root=TextPart(text=prompt))],
                      message_id=uuid.uuid4().hex,
                      metadata=metadata)
        req = SendStreamingMessageRequest(id=uuid.uuid4().hex,
                                          params=MessageSendParams(message=msg))

        async for chunk in client.send_message_streaming(req):
            r = chunk.root.result
            kind = getattr(r, "kind", None)

            if kind == "status-update":
                status = getattr(r, "status", None)
                state = getattr(status, "state", None)
                m = getattr(status, "message", None) if status else None
                text = get_message_text(m) if m else ""
                if bool(getattr(r, "final", False)) or state in _FINAL_STATES:
                    if text:
                        final_answer = text
                elif text and on_status:
                    on_status(text)

            elif kind == "artifact-update":
                art = getattr(r, "artifact", None)
                if not art:
                    continue
                datas = get_data_parts(art.parts)
                if datas:
                    data_artifacts.extend(datas)
                else:
                    for t in get_text_parts(art.parts):
                        if t:
                            token_chunks.append(t)
                            if on_token:
                                on_token(t)

            elif kind == "task":
                for a in (getattr(r, "artifacts", None) or []):
                    datas = get_data_parts(a.parts)
                    if datas:
                        data_artifacts.extend(datas)
                st = getattr(r, "status", None)
                m = getattr(st, "message", None) if st else None
                if m:
                    t = get_message_text(m)
                    if t:
                        final_answer = t

    answer = "".join(token_chunks) or final_answer
    return answer, data_artifacts


async def fetch_agent_card(url: str, timeout: float = 10.0):
    """Resolve an agent's card (used for health checks / refresh_tools)."""
    async with httpx.AsyncClient(timeout=timeout) as hc:
        return await A2ACardResolver(hc, base_url=url.rstrip("/")).get_agent_card()
