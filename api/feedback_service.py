"""Feedback bundles — a snapshot of diagnostics + the user's report, for the
prototype-handout phase. Pressing the red feedback button captures log tails,
MLflow trace references (never the raw 128 MB mlflow.db), and the reporting
user's own state (soul, goals, schedules, recent chats) into one JSON bundle on
disk, then best-effort emails a short notification to the admin.

Bundles are scoped to the REPORTING user's own data only — never another user's.
Auth material (``.tokens/``, ``.secrets/``, env values, Bearer/API-key lines in
logs) is never captured.
"""

from __future__ import annotations

import json
import platform
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from core import jsonstore

_ROOT = Path(__file__).resolve().parent.parent
_BUNDLE_DIR = _ROOT / "data" / "feedback"
_LOGS_DIR = _ROOT / ".logs"

_AGENT_INTERACTIONS_TAIL = 50_000
_PROCESS_LOG_TAIL = 20_000
_CHAT_TAIL = 100_000

_PROCESS_LOGS = {
    "mlflow": "/tmp/mlflow.log",
    "fitdash_api": "/tmp/fitdash_api.log",
    "telegram_bridge": "/tmp/telegram_bridge.log",
    "agent_orchestrator": "/tmp/agent_orchestrator.log",
    "agent_recovery": "/tmp/agent_recovery.log",
    "agent_load": "/tmp/agent_load.log",
    "agent_context": "/tmp/agent_context.log",
    "agent_route": "/tmp/agent_route.log",
    "agent_fitness": "/tmp/agent_fitness.log",
    "mcp_strava": "/tmp/mcp_strava.log",
    "mcp_garmin": "/tmp/mcp_garmin.log",
    "mcp_calendar": "/tmp/mcp_calendar.log",
    "mcp_weather": "/tmp/mcp_weather.log",
    "mcp_routes": "/tmp/mcp_routes.log",
    "mcp_flythrough": "/tmp/mcp_flythrough.log",
    "mcp_google_maps": "/tmp/mcp_google_maps.log",
}

_BUNDLE_ID_RE = re.compile(r"^[0-9TZ:.\-]+_[a-f0-9]{8}$")
# `.+` (not `\S+`) to consume the REST of the line — a value like "Bearer sk-xyz"
# or "Bearer <token>" is multiple space-separated tokens; matching only the first
# token would redact the word "Bearer" and leave the actual secret exposed. `.`
# doesn't match newlines (no re.DOTALL), so this never bleeds into the next line.
_REDACT_RE = re.compile(
    r"(?i)(authorization|bearer|api[_-]?key|token|password|secret)\s*[:=]\s*.+")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _bundle_path(bundle_id: str) -> Optional[Path]:
    if not _BUNDLE_ID_RE.fullmatch(bundle_id or ""):
        return None
    return _BUNDLE_DIR / f"{bundle_id}.json"


def _tail_text(path: Path, max_bytes: int) -> str:
    """The last max_bytes of a text file, redacted. "" if missing/unreadable."""
    try:
        data = path.read_bytes()
    except OSError:
        return ""
    if len(data) > max_bytes:
        data = data[-max_bytes:]
    text = data.decode("utf-8", errors="replace")
    return _REDACT_RE.sub(r"\1: [REDACTED]", text)


def _capture_process_logs() -> Dict[str, str]:
    logs: Dict[str, str] = {}
    for name, path in _PROCESS_LOGS.items():
        content = _tail_text(Path(path), _PROCESS_LOG_TAIL)
        if content:
            logs[name] = content
    return logs


_MLFLOW_CAPTURE_TIMEOUT = 4.0  # seconds — a diagnostic capture must never stall the button


def _capture_mlflow_refs(user: str) -> Dict[str, Any]:
    """Reference-only — tracking uri + experiment names + a handful of recent run
    ids. NEVER copies mlflow.db/mlartifacts. Best-effort with a HARD wall-clock
    timeout: mlflow's REST client retries with backoff against an unreachable
    tracking server (can take minutes), which must never delay feedback submission.
    The timeout is enforced from the outside via a DAEMON thread (not
    ThreadPoolExecutor — its workers are non-daemon and get joined at interpreter
    shutdown regardless of shutdown(wait=False), so an abandoned retry-storm thread
    would still hang process exit; a plain daemon thread is killed outright)."""
    import threading

    def _do() -> Dict[str, Any]:
        result: Dict[str, Any] = {"tracking_uri": None, "experiments": [], "recent_runs": []}
        from core.tracing import experiment, tracking_uri
        from core.user_tracking import experiment_name
        result["tracking_uri"] = tracking_uri()
        exp_names = [experiment(), experiment_name(user)]
        result["experiments"] = exp_names
        from mlflow.tracking import MlflowClient
        client = MlflowClient(tracking_uri=tracking_uri())
        for name in exp_names:
            try:
                exp = client.get_experiment_by_name(name)
                if not exp:
                    continue
                runs = client.search_runs([exp.experiment_id], max_results=5,
                                          order_by=["attribute.start_time DESC"])
                for run in runs:
                    result["recent_runs"].append({
                        "experiment": name,
                        "run_id": run.info.run_id,
                        "status": run.info.status,
                        "start_time": run.info.start_time,
                    })
            except Exception:  # noqa: BLE001 — one bad experiment shouldn't kill the rest
                continue
        return result

    empty: Dict[str, Any] = {"tracking_uri": None, "experiments": [], "recent_runs": []}
    box: Dict[str, Any] = {}
    done = threading.Event()

    def _worker() -> None:
        try:
            box["result"] = _do()
        except Exception:  # noqa: BLE001
            box["result"] = empty
        finally:
            done.set()

    threading.Thread(target=_worker, daemon=True).start()
    if not done.wait(timeout=_MLFLOW_CAPTURE_TIMEOUT):
        return empty  # gave up — the daemon thread dies with the process, never blocks it
    return box.get("result", empty)


def _capture_user_state(user: str) -> Dict[str, Any]:
    """This user's own soul/goals/schedules/deep-jobs/outbox/recent chats. Never
    another user's data; never .tokens/.secrets/env values."""
    state: Dict[str, Any] = {}
    slug = jsonstore.slugify(user)

    try:
        from core.user_memory import get_user_memory
        state["soul"] = get_user_memory(user).read_soul()
    except Exception:  # noqa: BLE001
        state["soul"] = None

    try:
        from core import goal_store
        state["goals"] = goal_store.list_goals(user)
    except Exception:  # noqa: BLE001
        state["goals"] = []

    try:
        from core import schedule_store
        state["schedules"] = schedule_store.list_for(user)
    except Exception:  # noqa: BLE001
        state["schedules"] = []

    state["deep_jobs"] = _glob_json(_ROOT / "data" / "deep_jobs" / slug)
    state["proactive_outbox"] = _glob_json(_ROOT / "data" / "proactive" / "outbox" / slug)

    try:
        from core import chat_store
        summaries = chat_store.list_chats(user)
        chats: List[dict] = []
        # The pinned Coach chat first (if present), then up to 2 more recent chats.
        coach = next((c for c in summaries if c.get("special") == "coach"), None)
        picks = ([coach] if coach else []) + [c for c in summaries if c is not coach][:2]
        for c in picks:
            full = chat_store.get_chat(user, c["id"])
            if full is None:
                continue
            blob = json.dumps(full, ensure_ascii=False)
            if len(blob) > _CHAT_TAIL:
                blob = blob[-_CHAT_TAIL:]
            chats.append({"id": c["id"], "title": c.get("title"), "content": blob})
        state["recent_chats"] = chats
    except Exception:  # noqa: BLE001
        state["recent_chats"] = []

    return state


def _glob_json(d: Path) -> List[Any]:
    if not d.exists():
        return []
    out = []
    for p in sorted(d.glob("*.json")):
        obj = jsonstore.read_json(p)
        if obj is not None:
            out.append(obj)
    return out


def create_bundle(user: str, text: str, context: Optional[dict] = None) -> Optional[str]:
    """Build + persist one feedback bundle. Returns the bundle_id, or None on
    catastrophic failure (never raises)."""
    ts = datetime.now(timezone.utc)
    bundle_id = f"{ts.strftime('%Y%m%dT%H%M%SZ')}_{uuid.uuid4().hex[:8]}"
    bundle = {
        "bundle_id": bundle_id,
        "created_at": _now_iso(),
        "user": user,
        "feedback_text": (text or "").strip(),
        "client_context": context or {},
        "meta": {
            "os": platform.system(),
            "os_version": platform.release(),
            "python_version": platform.python_version(),
        },
        "logs": {
            "agent_interactions": _tail_text(_LOGS_DIR / "agent_interactions.jsonl",
                                             _AGENT_INTERACTIONS_TAIL),
            "process_logs": _capture_process_logs(),
        },
        "mlflow": _capture_mlflow_refs(user),
        "user_state": _capture_user_state(user),
    }
    try:
        _BUNDLE_DIR.mkdir(parents=True, exist_ok=True)
        jsonstore.atomic_write(_BUNDLE_DIR / f"{bundle_id}.json", bundle)
        return bundle_id
    except OSError as exc:
        print(f"[feedback_service] bundle write failed: {exc}", flush=True)
        return None


def list_bundles() -> List[Dict[str, Any]]:
    """Summaries (not full bundles) for the admin list view."""
    if not _BUNDLE_DIR.exists():
        return []
    out = []
    for p in sorted(_BUNDLE_DIR.glob("*.json"), reverse=True):
        obj = jsonstore.read_json(p)
        if not isinstance(obj, dict):
            continue
        out.append({
            "bundle_id": obj.get("bundle_id"),
            "created_at": obj.get("created_at"),
            "user": obj.get("user"),
            "text_preview": (obj.get("feedback_text") or "")[:200],
        })
    return out


def get_bundle(bundle_id: str) -> Optional[Dict[str, Any]]:
    path = _bundle_path(bundle_id)
    if path is None or not path.exists():
        return None
    return jsonstore.read_json(path)


def notify_admin(bundle_id: str, user: str, text: str) -> None:
    """Best-effort short email — feedback text + bundle id ONLY, never the bundle."""
    try:
        from api.email_service import EmailError, email_ready, send_email
        import os
        if not email_ready():
            return
        admin = os.getenv("ADMIN_EMAIL", "kit.aiss2026@gmail.com").strip()
        body = f"From: {user}\n\n{(text or '').strip()}\n\nBundle: {bundle_id}"
        try:
            send_email(to=admin, subject=f"[FitDash feedback] {bundle_id}", body_text=body)
        except EmailError as exc:
            print(f"[feedback_service] admin notification failed: {exc}", flush=True)
    except Exception as exc:  # noqa: BLE001 — notification must never break feedback
        print(f"[feedback_service] notify_admin skipped: {exc}", flush=True)
