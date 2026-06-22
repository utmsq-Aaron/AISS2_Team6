"""Chat endpoint — streams the orchestrator's run() over Server-Sent Events.

The orchestrator is synchronous and emits progress + token callbacks. We run it
in a worker thread (serialized by orchestrator_lock) and bridge its callbacks
onto an asyncio.Queue via loop.call_soon_threadsafe, then yield SSE frames.

SSE event types:
  status  {"message": str}        live step updates (progress_cb)
  token   {"delta": str}          streamed answer chunk (text_cb)
  reset   {}                       clear streamed answer so far (text_cb(None))
  trace   {<full trace dict>}      final trace incl. "answer", "route_data", …
  error   {"message": str}
  done    {}
"""

import asyncio
import json
import threading
from typing import Any, Dict, List

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from api.auth import current_user
from api.deps import get_orchestrator, orchestrator_lock
from core import chat_store

router = APIRouter()


class ChatRequest(BaseModel):
    message: str
    history: List[Dict[str, Any]] = []  # prior [{role, content}], excluding this message
    chat_id: str | None = None          # when set, history is loaded + the turn persisted here


def _sse(event: str, data: Any) -> str:
    payload = json.dumps(data, ensure_ascii=False, default=str)
    return f"event: {event}\ndata: {payload}\n\n"


@router.post("/chat")
async def chat(req: ChatRequest, user: str = Depends(current_user)):
    loop = asyncio.get_running_loop()
    q: "asyncio.Queue[tuple]" = asyncio.Queue()

    def emit(event: str, data: Any) -> None:
        loop.call_soon_threadsafe(q.put_nowait, (event, data))

    def progress_cb(msg: str) -> None:
        emit("status", {"message": msg})

    def text_cb(delta) -> None:
        if delta is None:
            emit("reset", {})
        else:
            emit("token", {"delta": delta})

    # When tied to a stored chat, history is server-authoritative (survives restarts
    # and any client). Otherwise fall back to the client-supplied history (legacy).
    history = chat_store.history_messages(user, req.chat_id) if req.chat_id else req.history

    def worker() -> None:
        orch = get_orchestrator()
        with orchestrator_lock:
            answer, trace = "", {}
            try:
                answer, trace = orch.run(req.message, history, progress_cb, text_cb, user=user)
                trace = dict(trace or {})
                trace.setdefault("question", req.message)
                trace.setdefault("answer", answer)
                emit("trace", trace)
            except Exception as exc:  # noqa: BLE001 — surface to the client
                answer = answer or f"⚠ {type(exc).__name__}: {exc}"
                trace = trace or {"error": str(exc), "answer": answer}
                emit("error", {"message": f"{type(exc).__name__}: {exc}"})
            finally:
                # Persist the turn (best-effort) so it survives restarts / reloads.
                if req.chat_id:
                    try:
                        chat_store.append_message(user, req.chat_id, "user", req.message)
                        chat_store.append_message(
                            user, req.chat_id, "assistant",
                            (trace.get("answer") if isinstance(trace, dict) else None) or answer or "",
                            trace=trace if isinstance(trace, dict) else None,
                        )
                    except Exception:  # noqa: BLE001 — persistence must not break the stream
                        pass
                emit("done", {})

    threading.Thread(target=worker, daemon=True).start()

    async def gen():
        while True:
            event, data = await q.get()
            yield _sse(event, data)
            if event == "done":
                break

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # tell any proxy not to buffer
        },
    )


@router.post("/chat/refresh-tools")
def refresh_tools():
    """Re-discover tools (used when MCP servers were started after the app)."""
    return {"count": get_orchestrator().refresh_tools()}
