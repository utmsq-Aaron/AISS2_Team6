#!/usr/bin/env python3
"""Training Copilot Telegram bridge — talk to the agent from a Telegram chat.

Every incoming Telegram message is forwarded to the SAME engine the Chat tab
uses (``core.orchestrator.FitDashOrchestrator``) and the agent's answer is sent
back. Voice memos are transcribed locally with Whisper (German/English, language
auto-detected) and handled exactly like a typed message. Route results are sent
three ways: a static map image, a tappable Google Maps link (opens the Maps app),
and a GPX file (exact track for OsmAnd/Komoot/…).
Each chat keeps its own short history, so multi-turn "chat back and forth"
replaces the web UI's interactive widgets (e.g. the agent lists trail options as
text and you pick one by replying).

Login: a Telegram user must sign in with the SAME email + OTP as the web app
before using the agent. Send ``/login`` → reply with your email → reply with the
emailed code. The Telegram id is then permanently linked to that account
(``core.telegram_link``, persisted), so the agent runs AS that email — same
Strava/Garmin connections and the same per-user memory as on the web — and you
never log in again until you send ``/logout``. Until logged in, any message gets
an automated "please /login" reply.

This runs as a **userbot**: it logs in as *your* Telegram account and replies
*as you* to whoever messages you. It is a long-running process, separate from
Streamlit and from the MCP servers.

Run (after the MCP servers are up, inside the app's Python env):

    python telegram_bridge.py            # start listening
    python telegram_bridge.py --login    # one-time: phone+code session (code arrives IN the Telegram app)
    python telegram_bridge.py --login-qr # one-time: QR session — scan with your phone, no code needed

Configuration (.env):
    TELEGRAM_API_ID, TELEGRAM_API_HASH          required (my.telegram.org/apps)
    TELEGRAM_SESSION_STRING                     reused by default (your existing login)
    TELEGRAM_BRIDGE_SESSION_STRING              optional dedicated login (see below)
    TELEGRAM_ALLOWED_USERS    comma-separated user IDs/@usernames; empty = anyone
    TELEGRAM_BRIDGE_ALLOW_GROUPS   "true" to also answer in groups (default: DMs only)
    TELEGRAM_BRIDGE_HISTORY        turns of history kept per chat (default 10)
    WHISPER_BACKEND / WHISPER_MODEL / WHISPER_LANGUAGE   voice transcription (core/transcribe.py)

Session note: the API id/hash are shared with everything else; the *session string*
is just a saved login. Reusing TELEGRAM_SESSION_STRING is fine on its own. The only
catch is running this bridge while the telegram-mcp proxy (servers/telegram_mcp.py)
is ALSO connected on that same session — two live clients on one login key can make
Telegram revoke it (AuthKeyDuplicatedError). If you want both running at once,
generate a second login with ``--login`` and set TELEGRAM_BRIDGE_SESSION_STRING.
"""

from __future__ import annotations

import asyncio
import io
import logging
import os
import shutil
import sys
import tempfile
import threading
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from typing import Deque, Dict, List, Optional

from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("fitdash.telegram")

# ── Configuration ───────────────────────────────────────────────────────────────
API_ID_RAW = os.getenv("TELEGRAM_API_ID", "").strip()
API_HASH = os.getenv("TELEGRAM_API_HASH", "").strip()
SESSION = (os.getenv("TELEGRAM_BRIDGE_SESSION_STRING") or os.getenv("TELEGRAM_SESSION_STRING") or "").strip()
_USING_SHARED_SESSION = (
    not os.getenv("TELEGRAM_BRIDGE_SESSION_STRING", "").strip()
    and bool(os.getenv("TELEGRAM_SESSION_STRING", "").strip())
)
ALLOW_GROUPS = os.getenv("TELEGRAM_BRIDGE_ALLOW_GROUPS", "false").strip().lower() in ("1", "true", "yes")
_ALLOW_RAW = os.getenv("TELEGRAM_ALLOWED_USERS", "").strip()
ALLOWLIST = {p.strip().lstrip("@").lower() for p in _ALLOW_RAW.split(",") if p.strip()}  # empty ⇒ anyone
HISTORY_TURNS = max(1, int(os.getenv("TELEGRAM_BRIDGE_HISTORY", "10") or "10"))
# Internal-only mode: only respond to self-messages (Saved Messages) — ignore external DMs
INTERNAL_ONLY = os.getenv("TELEGRAM_BRIDGE_INTERNAL_ONLY", "false").strip().lower() in ("1", "true", "yes")

TG_LIMIT = 4096  # Telegram per-message character cap

# ── Proactive scheduler (this process hosts the durable cross-chat scheduler) ──
SCHEDULE_POLL_SECONDS = max(15, int(os.getenv("SCHEDULE_POLL_SECONDS", "60") or "60"))
CAL_PRE_OFFSET_MIN = int(os.getenv("CAL_PRE_OFFSET_MIN", "60") or "60")
CAL_POST_OFFSET_MIN = int(os.getenv("CAL_POST_OFFSET_MIN", "30") or "30")
CAL_SCAN_SECONDS = max(300, int(os.getenv("CAL_SCAN_SECONDS", "3600") or "3600"))

# ── Goal-panel builds — a SEPARATE, faster loop from the scheduler above ────────
# A form-created/refreshed goal is ENQUEUED (never spawned in FastAPI, which runs
# under --reload and would kill an in-process daemon thread on the next code edit).
# This drains that queue on its own short cadence so "building…" actually resolves
# in seconds, without changing the 60s cadence the wake-up scheduler relies on.
GOAL_BUILD_POLL_SECONDS = max(2, int(os.getenv("GOAL_BUILD_POLL_SECONDS", "5") or "5"))
# Panels older than this are considered stale and re-enqueued by the hourly scan —
# the "daily-ish" auto-refresh (there is no literal daily tick; it rides the hourly
# calendar scan already in place).
GOAL_PANEL_STALE_HOURS = float(os.getenv("GOAL_PANEL_STALE_HOURS", "20") or "20")

# ── Per-process state ────────────────────────────────────────────────────────────
# History per chat: a flat deque of {"role", "content"} dicts (2 entries / turn).
_histories: Dict[int, Deque[Dict[str, str]]] = defaultdict(lambda: deque(maxlen=HISTORY_TURNS * 2))
# Serialize ALL agent runs: ToolHost is shared and not assumed thread-safe, and a
# personal userbot has tiny concurrency — one turn at a time is the safe choice.
_RUN_LOCK = asyncio.Lock()
# Separate lock guarding the shared Whisper model (also not assumed thread-safe).
_TRANSCRIBE_LOCK = asyncio.Lock()
_orchestrator = None  # lazily built singleton

# Echo-loop guard for Saved Messages: track IDs of messages the bridge itself
# sends so that when Telethon re-fires them as NewMessage(outgoing) events we
# simply skip them instead of feeding our own answers back to the orchestrator.
_skip_ids: set = set()

# In-flight email+OTP login state per Telegram user id (ephemeral — a half-finished
# login is forgotten on restart; the COMPLETED link is persisted via core.telegram_link).
#   {telegram_id: {"stage": "await_email"|"await_code", "email": <str>}}
_pending_login: Dict[int, Dict[str, str]] = {}


def _track(*msgs) -> None:
    """Register sent message IDs so they are ignored if re-fired as events."""
    for m in msgs:
        if m is not None and getattr(m, "id", None):
            _skip_ids.add(m.id)


def _get_orchestrator():
    global _orchestrator
    if _orchestrator is None:
        from core.orchestrator import FitDashOrchestrator
        _orchestrator = FitDashOrchestrator()
    return _orchestrator


def _api_id() -> int:
    try:
        return int(API_ID_RAW)
    except (TypeError, ValueError):
        raise SystemExit("TELEGRAM_API_ID is missing or not an integer — set it in .env")


# ── Helpers ──────────────────────────────────────────────────────────────────────

def _is_allowed(sender) -> bool:
    """True if this sender may use the agent (empty allowlist ⇒ everyone)."""
    if not ALLOWLIST:
        return True
    username = (getattr(sender, "username", "") or "").lower()
    uid = str(getattr(sender, "id", "") or "")
    return username in ALLOWLIST or uid in ALLOWLIST


def _chunk(text: str, limit: int = TG_LIMIT) -> List[str]:
    """Split text into <=limit pieces, preferring line boundaries."""
    text = text or ""
    if len(text) <= limit:
        return [text] if text else []
    parts: List[str] = []
    buf = ""
    for line in text.splitlines(keepends=True):
        while len(line) > limit:  # a single very long line
            if buf:
                parts.append(buf)
                buf = ""
            parts.append(line[:limit])
            line = line[limit:]
        if len(buf) + len(line) > limit:
            parts.append(buf)
            buf = ""
        buf += line
    if buf:
        parts.append(buf)
    return parts


async def _send_text(event, text: str) -> None:
    """Send a possibly-long reply; try Markdown, fall back to plain on parse errors."""
    for chunk in _chunk(text) or ["(no response)"]:
        sent = None
        try:
            sent = await event.respond(chunk, parse_mode="md", link_preview=False)
        except Exception:
            try:
                sent = await event.respond(chunk, parse_mode=None, link_preview=False)
            except Exception:
                log.exception("failed to send a reply chunk")
        _track(sent)


async def _send_route(event, route_data: Dict) -> None:
    """Send a route three ways (best-effort, each piece independent):

      • a static map image (see the route),
      • a Google Maps link in the caption (tap → opens the Maps app; approximate,
        since Google re-routes between points),
      • a GPX file (the exact planned track — OsmAnd, Komoot, Garmin, Strava, …).
    """
    loop = asyncio.get_running_loop()
    try:
        from core.route_export import google_maps_url, route_gpx
        from core.route_render import render_route_image
    except Exception:
        log.exception("route export imports failed")
        return

    # For activity GPS tools (get_activity_streams / get_activity_gps_track), the
    # viz_telegram chart renderer already sends a higher-quality HR/elevation-colored
    # map.  Skip the simpler staticmap PNG here so users don't get two nearly-identical
    # images; keep the GPX and Google Maps link (they're still useful).
    _ACTIVITY_TOOLS = {"get_activity_streams", "get_activity_gps_track"}
    skip_png = (route_data or {}).get("tool", "") in _ACTIVITY_TOOLS

    try:
        png = None if skip_png else await loop.run_in_executor(None, render_route_image, route_data)
    except Exception:
        log.exception("route image render failed")
        png = None
    gmaps = google_maps_url(route_data)
    gpx = route_gpx(route_data)

    link_line = f"📍 Open in Google Maps:\n{gmaps}" if gmaps else ""
    full_caption = f"🗺️ Route\n{link_line}" if link_line else "🗺️ Route"

    try:
        link_inline = False
        if png:
            if link_line and len(full_caption) <= 1024:  # Telegram caption cap
                caption, link_inline = full_caption, True
            else:
                caption = "🗺️ Route"
            bio = io.BytesIO(png)
            bio.name = "route.png"
            _track(await event.client.send_file(event.chat_id, bio, caption=caption, force_document=False))

        # No photo (or caption too long) → send the link on its own so it's still tappable.
        if link_line and not link_inline:
            _track(await event.respond(link_line, parse_mode=None, link_preview=True))

        if gpx:
            gio = io.BytesIO(gpx)
            gio.name = "route.gpx"
            _track(await event.client.send_file(
                event.chat_id, gio, force_document=True,
                caption="Exact route as GPX — open in OsmAnd, Komoot, Organic Maps, Garmin or Strava.",
            ))
    except Exception:
        log.exception("failed to send route artifacts")


def _has_audio(event) -> bool:
    """True if the message is a voice note or an audio file."""
    msg = getattr(event, "message", None)
    return bool(msg is not None and (getattr(msg, "voice", None) or getattr(msg, "audio", None)))


async def _transcribe_voice(event) -> Optional[str]:
    """Download the voice/audio to a temp file and transcribe it locally (Whisper)."""
    loop = asyncio.get_running_loop()
    tmpdir = tempfile.mkdtemp(prefix="fitdash_voice_")
    try:
        async with event.client.action(event.chat_id, "typing"):
            path = await event.message.download_media(file=tmpdir)
            if not path:
                return None
            from core.transcribe import transcribe
            async with _TRANSCRIBE_LOCK:  # protect the shared Whisper model
                result = await loop.run_in_executor(None, lambda: transcribe(path))
        text = (result or {}).get("text", "").strip()
        log.info("🎤 transcribed via %s (lang=%s) → %d chars",
                 result.get("backend"), result.get("language"), len(text))
        return text or None
    except Exception:
        log.exception("voice transcription failed")
        return None
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def _fetch_track_for_activity(activity_id: int) -> List[List[float]]:
    """Fetch GPS points via ToolHost and return [[lon, lat, ele, time_s], ...]."""
    import json as _json
    from core.host import default_host
    raw = default_host.call_tool("strava__get_activity_streams", {"activity_id": activity_id})
    data = _json.loads(raw)
    points = data.get("points", [])
    if not points:
        raise ValueError(f"No GPS stream data for activity {activity_id}")
    return [
        [p["lon"], p["lat"], p.get("ele") or 0.0, p.get("time_s")]
        for p in points
        if p.get("lat") is not None and p.get("lon") is not None
    ]


async def _send_flythrough(event, action: Dict) -> None:
    """Render flythrough MP4 server-side via Playwright and send to Telegram."""
    loop = asyncio.get_running_loop()
    activity_id = action.get("activity_id")
    name = action.get("activity_name", "Activity")
    if not activity_id:
        log.warning("flythrough action missing activity_id — skipping")
        return

    try:
        from ui.video_renderer import render_flythrough
    except ImportError:
        log.warning("ui.video_renderer not available (playwright not installed) — skipping flythrough")
        return

    await _send_text(
        event,
        f"🎬 Rendering flythrough for *{name}*… "
        "This encodes a full video — expect **2–10 minutes** depending on GPU availability. "
        "Progress is printed to the server terminal.",
    )

    try:
        track = await loop.run_in_executor(None, _fetch_track_for_activity, activity_id)
    except Exception:
        log.exception("failed to fetch GPS track for activity %s", activity_id)
        await _send_text(event, "⚠️ Could not fetch GPS track for this activity.")
        return

    try:
        mp4_bytes: Optional[bytes] = await loop.run_in_executor(
            None,
            lambda: render_flythrough(
                track=track,
                name=name,
                mode=action.get("mode", "satellite_3d"),
                duration_sec=int(action.get("duration_sec", 60)),
                orientation=action.get("orientation", "landscape"),
                resolution=action.get("resolution", "2K"),
            ),
        )
    except Exception:
        log.exception("flythrough render failed for activity %s", activity_id)
        await _send_text(event, "⚠️ Flythrough render failed. Make sure Playwright/Chromium is installed.")
        return

    if not mp4_bytes:
        await _send_text(event, "⚠️ Flythrough render returned no data.")
        return

    try:
        bio = io.BytesIO(mp4_bytes)
        bio.name = f"flythrough_{activity_id}.mp4"
        _track(await event.client.send_file(
            event.chat_id, bio,
            caption=f"🎬 {name}",
            force_document=False,
        ))
        log.info("flythrough MP4 sent (%d bytes) for activity %s", len(mp4_bytes), activity_id)
    except Exception:
        log.exception("failed to send flythrough MP4 to Telegram")


# ── Visualization delivery ────────────────────────────────────────────────────────

_CHART_LABELS = {
    "get_garmin_sleep":           "Sleep",
    "get_garmin_body_battery":    "Body Battery",
    "get_garmin_heart_rate_timeline": "Heart Rate",
    "get_garmin_steps_timeline":  "Steps",
    "get_garmin_stress_timeline": "Stress",
    "get_garmin_hrv_status":      "HRV Status",
    "get_garmin_daily_health":    "Daily Health",
    "get_garmin_training_metrics":"Training Metrics",
    "get_garmin_wellness_trends": "Wellness Trends",
    "get_garmin_activity_detail": "Activity Detail",
    "get_garmin_body_composition":"Body Composition",
    "get_activity_gps_track":     "GPS Track",
    "get_activities":             "Activities",
    "get_garmin_activities":      "Activities",
    "get_activity_streams":       "GPS Route",
    "analyze_performance_trends": "Performance Trends",
    "get_training_load":          "Training Load (ATL/CTL/TSB)",
    "get_training_trends":        "Weekly Training Volume",
    "get_yearly_breakdown":       "Year-over-Year Stats",
    "compare_activity_to_baseline": "Activity vs. Baseline",
    "get_activity_stats":          "All-Time Stats",
    "get_personal_bests":          "Personal Bests",
    "get_weather_forecast":        "Weather Forecast",
    "get_gear_info":               "Gear Mileage",
}


def _chart_caption(bare_tool: str, result_json: str) -> str:
    """Build a short Telegram photo caption from the tool name + key data fields."""
    label = _CHART_LABELS.get(bare_tool, bare_tool.replace("_", " ").title())
    try:
        import json as _json
        d = _json.loads(result_json) if result_json else {}
        date = d.get("date") or d.get("start_date") or ""
        name = d.get("name") or d.get("activity_name") or ""
        if name:
            return f"{label} — {name[:40]}"
        if date:
            return f"{label} — {str(date)[:10]}"
    except Exception:
        pass
    return label


async def _send_viz_charts(event, trace: Dict) -> int:
    """Render chart images for tool results and send them as Telegram photos.

    Uses core.viz_telegram (matplotlib, headless, no Streamlit). Each renderable
    tool result becomes one photo message. Returns the number of charts sent.
    """
    loop = asyncio.get_running_loop()
    try:
        from core.viz_telegram import can_render, render_chart_png
    except ImportError:
        log.debug("core.viz_telegram unavailable — skipping chart delivery")
        return 0

    tool_calls = trace.get("tool_calls") or []
    user_query = trace.get("user_input") or ""
    n_sent = 0

    # Deduplicate: only one chart per bare tool name (first successful result wins)
    seen: set = set()
    for tc in tool_calls:
        if tc.get("error"):
            continue
        bare = tc["tool"].split("__", 1)[-1] if "__" in tc["tool"] else tc["tool"]
        if bare in seen or not can_render(bare):
            continue
        seen.add(bare)

        result_json = tc.get("result", "")
        try:
            png: Optional[bytes] = await loop.run_in_executor(
                None, render_chart_png, tc["tool"], result_json, user_query
            )
        except Exception:
            log.exception("chart render failed for tool %s", tc["tool"])
            continue

        if not png:
            continue

        try:
            caption = _chart_caption(bare, tc.get("result", ""))
            bio = io.BytesIO(png)
            bio.name = f"chart_{bare}.png"
            _track(await event.client.send_file(
                event.chat_id, bio, caption=caption, force_document=False,
            ))
            n_sent += 1
        except Exception:
            log.exception("failed to send chart for tool %s", tc["tool"])

    return n_sent


async def _send_plotly_charts(event, trace: Dict) -> int:
    """Generate LLM Plotly charts (identical to the chat UI) and send as PNG photos.

    Uses the same _generate_code / _fix_code / _try_execute pipeline from
    ui.chart_gen, but provides the client from core.llm so that @st.cache_resource
    (in ui.shared.get_openai_client) is never touched from this headless context.
    Requires kaleido for fig.to_image() PNG export (pip install kaleido).
    """
    loop = asyncio.get_running_loop()

    try:
        from ui.chart_gen import (
            _generate_code, _fix_code, _try_execute, _extract_code,
            _compact, _STRAVA_DOMAIN_HINT, _SKIP_TOOLS,
        )
        import plotly.graph_objects as _go  # just to verify plotly import
        del _go
    except ImportError as _e:
        log.debug("plotly chart delivery skipped: %s", _e)
        return 0

    import json as _json

    run_id   = trace.get("run_id", "")
    question = trace.get("question") or trace.get("user_input", "")
    answer   = trace.get("answer", "")
    hints    = trace.get("chart_hints") or []

    if not question:
        return 0

    # Build data_vars — same logic as chart_gen.generate_and_render
    data_vars: Dict = {}
    var_lines: List[str] = []
    seen_vars: set = set()
    for tc in (trace.get("tool_calls") or []):
        if tc.get("error"):
            continue
        bare = tc["tool"].split("__", 1)[-1] if "__" in tc["tool"] else tc["tool"]
        if bare in _SKIP_TOOLS:
            continue
        try:
            data = _json.loads(tc["result"]) if isinstance(tc["result"], str) else tc["result"]
        except Exception:
            continue
        if not data or (isinstance(data, dict) and data.get("error")):
            continue
        var_name = f"data_{bare}"
        if var_name in seen_vars:
            var_lines = [ln for ln in var_lines if not ln.startswith(f"{var_name} =")]
        seen_vars.add(var_name)
        data_vars[var_name] = data
        var_lines.append(f"{var_name} = {_compact(data)}")

    if not data_vars:
        return 0

    # Use core.llm — safe for headless use (no st.cache_resource)
    from core.llm import get_llm_client
    llm_client, model_name = get_llm_client()

    # Generate code (run in thread — synchronous network call)
    try:
        raw = await loop.run_in_executor(
            None,
            lambda: _generate_code(
                question, answer, var_lines, chart_hints=hints,
                _client=llm_client, _model=model_name,
            ),
        )
    except Exception:
        log.exception("plotly chart code generation failed (run=%s)", run_id)
        return 0

    if not raw:
        return 0

    code = _extract_code(raw)
    if not code:
        return 0

    # Execute; one reflexion fix attempt on error
    figures, error = _try_execute(code, data_vars)
    if error and not figures:
        try:
            fixed = await loop.run_in_executor(
                None,
                lambda: _fix_code(
                    code, error, list(data_vars.keys()),
                    _client=llm_client, _model=model_name,
                ),
            )
            if fixed:
                figures, _ = _try_execute(fixed, data_vars)
        except Exception:
            pass

    if not figures:
        return 0

    n_sent = 0
    for i, fig in enumerate(figures):
        fig.update_layout(height=400, width=800, paper_bgcolor="rgb(17,17,17)")
        try:
            png_bytes: bytes = await loop.run_in_executor(
                None, lambda f=fig: f.to_image(format="png", scale=1.5)
            )
        except Exception:
            log.exception("fig.to_image failed (is kaleido installed?), chart %d", i)
            continue
        try:
            title = (fig.layout.title.text or question)[:60]
            bio = io.BytesIO(png_bytes)
            bio.name = f"chart_{run_id}_{i}.png"
            _track(await event.client.send_file(
                event.chat_id, bio,
                caption=f"📊 {title}",
                force_document=False,
            ))
            n_sent += 1
        except Exception:
            log.exception("failed to send plotly chart %d to Telegram", i)

    return n_sent


# ── Email + OTP login over Telegram ────────────────────────────────────────────
# A Telegram user must log in with the SAME email+OTP as the web app before using
# the agent. On success the Telegram id is linked to that account (core.telegram_link)
# so they never log in again — until /logout. The agent then runs as that email, so
# they get the same Strava/Garmin data and the same per-user memory as on the web.

_LOGIN_PROMPT = (
    "👋 *Welcome to the FitDash Training Copilot.*\n\n"
    "Before we start, please sign in with your email — the same account you use on "
    "the web app.\n\n"
    "Send */login* and I'll email you a one-time code. (Already have an account or "
    "not — either way, the code both registers and logs you in.)"
)


async def _start_otp(event, uid: int, raw_email: str) -> None:
    """Validate the email, send a one-time code, and await it."""
    from api import auth as A
    from api import email_service as mail

    em = A.normalize_email(raw_email)
    if not em:
        _pending_login.pop(uid, None)
        await _send_text(event, "That doesn't look like a valid email. Send */login* and reply with your email address.")
        return
    try:
        code, _new = A.request_otp(em)  # may raise HTTPException(429) on rate limit
    except Exception as exc:  # noqa: BLE001
        detail = getattr(exc, "detail", None) or str(exc)
        await _send_text(event, f"⚠️ {detail}")
        return
    loop = asyncio.get_running_loop()
    try:
        await loop.run_in_executor(None, mail.send_otp_email, em, code)
    except Exception as exc:  # noqa: BLE001 — email send failed (Gmail not connected etc.)
        await _send_text(event, f"⚠️ Couldn't send the code: {exc}\nThe admin may need to connect Google/Gmail first.")
        return
    _pending_login[uid] = {"stage": "await_code", "email": em}
    await _send_text(event, f"📨 I sent a 6-digit code to *{em}*. Reply with the code to finish signing in.")


async def _handle_auth(event, uid: int, text: str) -> Optional[str]:
    """Process login/logout + the OTP flow.

    Returns the linked account email if the user is logged in and ``text`` is a
    normal message to forward to the agent; otherwise returns None (this function
    has already replied — a command, a login step, or the not-logged-in prompt).
    """
    from api import auth as A
    from core import telegram_link

    cmd = (text.split() or [""])[0].lower()

    if cmd == "/logout":
        telegram_link.unlink(uid)
        _pending_login.pop(uid, None)
        await _send_text(event, "🔓 Logged out. Send */login* whenever you want to sign in again.")
        return None

    if cmd == "/login":
        parts = text.split(maxsplit=1)
        if len(parts) == 2 and A.normalize_email(parts[1]):
            await _start_otp(event, uid, parts[1])
        else:
            _pending_login[uid] = {"stage": "await_email"}
            await _send_text(event, "📧 Please reply with your email address to receive a login code.")
        return None

    email = telegram_link.get_email(uid)

    # Mid-login (not yet linked): interpret the message as the email or the code.
    if email is None and uid in _pending_login:
        stage = _pending_login[uid].get("stage")
        if stage == "await_email":
            await _start_otp(event, uid, text)
            return None
        if stage == "await_code":
            pending_email = _pending_login[uid].get("email", "")
            if A.verify_otp(pending_email, text.strip()):
                telegram_link.link(uid, pending_email)
                A.register_or_touch(pending_email)
                _pending_login.pop(uid, None)
                await _send_text(
                    event,
                    f"✅ Signed in as *{pending_email}*. You won't need to log in again on this "
                    "Telegram account. Ask me anything about your training! (Send */logout* to unlink.)",
                )
            else:
                await _send_text(event, "❌ That code is invalid or expired. Reply with the code again, or send */login* to restart.")
            return None

    if email is None:
        await _send_text(event, _LOGIN_PROMPT)
        return None

    return email  # logged in → caller forwards `text` to the agent as this user


# ── Message handler ──────────────────────────────────────────────────────────────

async def _handle_message(event) -> None:
    # Skip messages the bridge itself sent (prevents echo-loops in Saved Messages).
    if event.message.id in _skip_ids:
        _skip_ids.discard(event.message.id)
        return

    # Scope: DMs only unless groups are explicitly enabled.
    if not event.is_private and not ALLOW_GROUPS:
        return

    sender = await event.get_sender()
    if getattr(sender, "bot", False):
        return  # never converse with other bots (loop guard)
    if not _is_allowed(sender):
        log.info("ignoring message from disallowed user id=%s @%s",
                 getattr(sender, "id", "?"), getattr(sender, "username", ""))
        return

    text = (event.raw_text or "").strip()
    chat_id = event.chat_id

    # Voice / audio memo → transcribe locally with Whisper, then treat it as text.
    if not text and _has_audio(event):
        transcript = await _transcribe_voice(event)
        if not transcript:
            await _send_text(event, "🎤 Could not understand the voice message.")
            return
        await _send_text(event, f'🎤 Heard: "{transcript}"')  # echo what was heard
        text = transcript

    if not text:
        await _send_text(event, "I can only process text and voice messages right now. 📝")
        return

    # Email+OTP gate: handle /login, /logout and the OTP steps. Only a logged-in
    # user gets their account email back; every other case is already handled
    # (a command, a login step, or the not-logged-in prompt).
    uid = int(getattr(sender, "id", 0) or 0)
    email = await _handle_auth(event, uid, text)
    if email is None:
        return

    log.info("← chat=%s @%s (%s): %s", chat_id, getattr(sender, "username", ""), email, text[:120])
    history_before = list(_histories[chat_id])

    try:
        async with event.client.action(chat_id, "typing"):
            async with _RUN_LOCK:
                loop = asyncio.get_running_loop()
                # Run AS the linked account → same data + same per-user memory.
                answer, trace = await loop.run_in_executor(
                    None, lambda: _get_orchestrator().run(text, history_before, user=email)
                )
    except Exception as exc:
        log.exception("orchestrator run failed")
        await _send_text(event, f"⚠️ Error processing your message: {exc}")
        return

    # Record the turn only after a successful run.
    _histories[chat_id].append({"role": "user", "content": text})
    _histories[chat_id].append({"role": "assistant", "content": answer or ""})

    # Mirror the turn into the web-visible, pinned "Coach" chat so the Telegram
    # conversation shows up in the React chat list. No unread bump — the user is
    # actively chatting here on Telegram (proactive deliveries do bump unread).
    try:
        from core import coach_mirror
        coach_mirror.append(email, "user", text, bump_unread=False)
        coach_mirror.append(email, "assistant", answer or "", trace=trace, bump_unread=False)
    except Exception:  # noqa: BLE001 — mirroring must never break a reply
        pass

    # Track this turn in the user's own MLflow experiment (best-effort).
    try:
        from core import user_tracking
        user_tracking.log_turn(email, f"tg-{chat_id}", len(history_before) // 2,
                               text, answer or "", trace or {})
    except Exception:  # noqa: BLE001 — telemetry must never break a reply
        pass

    await _send_text(event, answer or "(no response)")

    route_data = (trace or {}).get("route_data")
    if route_data:
        await _send_route(event, route_data)

    # Visualizations: matplotlib pre-defined charts (core.viz_telegram)
    n_charts = await _send_viz_charts(event, trace or {})

    # Visualizations: LLM-generated Plotly charts (same as chat UI)
    n_plotly = await _send_plotly_charts(event, trace or {})

    # Flythrough: render MP4 server-side and send as video
    ft_action = next(
        (a for a in ((trace or {}).get("actions") or []) if a.get("type") == "flythrough"),
        None,
    )
    if ft_action:
        await _send_flythrough(event, ft_action)

    log.info("→ chat=%s: replied (%d chars)%s%s%s%s",
             chat_id, len(answer or ""),
             " +route"               if route_data else "",
             f" +{n_charts}charts"   if n_charts else "",
             f" +{n_plotly}plotly"   if n_plotly else "",
             " +flythrough"          if ft_action else "")


# ── Proactive scheduler ────────────────────────────────────────────────────────
# The bridge is the only always-on process, so it owns the durable, cross-chat
# scheduler: it polls core.schedule_store for due wake-ups, composes each by running
# the note back through the orchestrator (fresh data), coalesces a user's due
# wake-ups into ONE message, and delivers via core.delivery (Telegram push + the
# web Coach-chat mirror). It also drains the pre-composed outbox (deep reports) and,
# hourly, auto-schedules pre/post-event nudges from the calendar + a weekly goal
# check-in. Every step is wrapped so a scheduler error never disturbs inbound chat.

async def _compose_wakeup(email: str, entry: dict) -> Optional[str]:
    """Run the entry's note back through the engine (fresh data). Holds _RUN_LOCK
    around the agent run only (ToolHost isn't thread-safe); returns the answer."""
    note = (entry.get("note") or "").strip()
    if not note:
        return None
    async with _RUN_LOCK:
        loop = asyncio.get_running_loop()
        answer, _ = await loop.run_in_executor(
            None, lambda: _get_orchestrator().run(note, [], user=email))
    return answer or note


def _recently_active(email: str) -> bool:
    """True if the user already exchanged a message (web or Telegram) recently — the
    adaptive-skip signal for the daily check-in."""
    from core import chat_store
    skip_hours = int(os.getenv("DAILY_CHECKIN_SKIP_HOURS", "8") or "8")
    try:
        return chat_store.last_user_message_ts(email, within_hours=skip_hours) is not None
    except Exception:  # noqa: BLE001 — never let this block a check-in
        return False


async def _fire_due(client, now: datetime) -> None:
    from core import delivery, goal_store, schedule_store
    from core.schedule_store import _BERLIN
    # Group all due entries per user so we can coalesce into ONE delivery.
    by_email: Dict[str, List[dict]] = defaultdict(list)
    for email, entry in schedule_store.due(now):
        by_email[email].append(entry)

    for email, entries in by_email.items():
        parts: List[str] = []
        fired: List[dict] = []
        for e in entries:
            stale = schedule_store.is_stale(e, now)
            # A pre-event nudge for an event that's already started is noise — drop it
            # (still record it fired so it never re-fires), but keep late goal check-ins.
            if stale and e.get("kind") == "calendar_pre":
                schedule_store.mark_fired(email, e, now)
                continue
            # Adaptive skip: the user already chatted recently — skip this daily
            # check-in silently (mark_fired re-arms it for tomorrow; no delivery).
            if e.get("kind") == "daily_checkin" and _recently_active(email):
                schedule_store.mark_fired(email, e, now)
                continue
            # Monday: weave in a quick goal-progress review, but only in the composed
            # message for THIS fire — mark_fired never persists "note" back to disk,
            # so the stored daily_checkin note stays generic for every other day.
            # Derived from the tick's own `now` (not a fresh datetime.now() call) so
            # this is deterministic given the tick, not a wall-clock race.
            if e.get("kind") == "daily_checkin" and now.astimezone(_BERLIN).weekday() == 0:
                try:
                    has_goal = goal_store.has_active_goal(email)
                except Exception:  # noqa: BLE001
                    has_goal = False
                if has_goal:
                    e = {**e, "note": (e.get("note") or "") + "\n\n" + MONDAY_GOAL_REVIEW_NOTE}
            try:
                msg = await _compose_wakeup(email, e)
            except Exception:  # noqa: BLE001 — leave un-fired → retry next tick
                log.exception("compose wakeup failed (%s / %s)", email, e.get("reason_key"))
                continue
            if msg:
                parts.append(("(delayed) " if stale else "") + msg)
                fired.append(e)
        if parts:
            try:
                await delivery.deliver_async(email, "\n\n".join(parts),
                                             kind="proactive", tg_client=client,
                                             on_sent=_track)
            except Exception:  # noqa: BLE001
                log.exception("delivery failed for %s", email)
                continue
            for e in fired:
                schedule_store.mark_fired(email, e, now)


async def _drain_outbox(client) -> None:
    """Deliver pre-composed messages (deep-analysis reports) and remove them."""
    from core import delivery, proactive_outbox
    for path, rec in proactive_outbox.pending():
        email, body = rec.get("user"), (rec.get("body") or "")
        if not email or not body.strip():
            proactive_outbox.remove(path)
            continue
        title = rec.get("title")
        text = f"**{title}**\n\n{body}" if title else body
        try:
            await delivery.deliver_async(email, text, trace=rec.get("trace"),
                                         kind=rec.get("kind") or "deep_report", tg_client=client,
                                         on_sent=_track)
        except Exception:  # noqa: BLE001
            log.exception("outbox delivery failed for %s", email)
            continue
        proactive_outbox.remove(path)


def _all_account_emails() -> List[str]:
    import json
    from pathlib import Path
    p = Path(__file__).resolve().parent / "data" / "accounts.json"
    try:
        return list(json.loads(p.read_text(encoding="utf-8")).keys())
    except Exception:  # noqa: BLE001
        return []


def _proactive_emails() -> set:
    """Users eligible for proactive scheduling: Telegram-linked ∪ has-an-active-goal."""
    from core import goal_store, telegram_link
    emails = set()
    try:
        for rec in (telegram_link._load() or {}).values():
            if isinstance(rec, dict) and rec.get("email"):
                emails.add(rec["email"])
    except Exception:  # noqa: BLE001
        pass
    for email in _all_account_emails():
        try:
            if goal_store.has_active_goal(email):
                emails.add(email)
        except Exception:  # noqa: BLE001
            pass
    return emails


def _next_morning_berlin(hour: Optional[int] = None) -> str:
    from core.schedule_store import _BERLIN
    if hour is None:
        hour = int(os.getenv("DAILY_CHECKIN_HOUR", "9") or "9")
    now_local = datetime.now(_BERLIN)
    target = now_local.replace(hour=hour, minute=0, second=0, microsecond=0)
    if target <= now_local:
        target = target + timedelta(days=1)
    return target.isoformat()


DAILY_CHECKIN_NOTE = (
    "Morning check-in — text the user like a friend, NOT a report: 1–3 sentences max, "
    "their first name if you have it, no headers or bullet lists. Glance at today's "
    "weather and mention it only if it actually affects training (rain, heat, etc — "
    "skip it if it's unremarkable). Ask how they're feeling and whether today's session "
    "is on. Keep it light and genuine, like you're checking in on a friend."
)

MONDAY_GOAL_REVIEW_NOTE = (
    "It's Monday — also weave in a brief, casual look at how the week's shaping up "
    "against their active goal(s): on track, slipping, or crushing it. Still 1–3 "
    "sentences total, still a text from a friend, not a status report."
)


def _run_calendar_autoschedule() -> None:
    """Hourly: pre/post-event nudges from the calendar + a daily check-in.
    Sync (runs in an executor). Best-effort per user; a calendar error skips it."""
    import json
    from core import schedule_store
    from core.host import default_host

    now = datetime.now(timezone.utc)
    tmin = now.isoformat()
    tmax = (now + timedelta(days=2)).isoformat()

    for email in _proactive_emails():
        # Calendar-driven pre/post nudges (calendar tokens are single-user today).
        try:
            raw = default_host.call_tool("calendar__list_events",
                                         {"time_min": tmin, "time_max": tmax})
            data = json.loads(raw)
            events = data.get("events") if isinstance(data, dict) and "error" not in data else None
        except Exception:  # noqa: BLE001 — calendar unreachable → skip this user
            events = None
        for ev in (events or []):
            eid = ev.get("id") or ""
            summary = ev.get("summary") or "your event"
            sdt = (ev.get("start") or {}).get("dateTime")
            edt = (ev.get("end") or {}).get("dateTime") or sdt
            if not sdt:  # all-day event → no meaningful pre/post minute offset
                continue
            start = schedule_store.to_utc(sdt)
            if start:
                pre = start - timedelta(minutes=CAL_PRE_OFFSET_MIN)
                if pre > now:
                    schedule_store.upsert(
                        email, f"cal:pre:{eid}", pre.isoformat(),
                        f"Text the user like a friend before '{summary}' ({sdt}): 1–3 "
                        f"sentences — hype them up a little, remind them what to bring, "
                        f"maybe a quick fueling/warm-up tip. No report formatting.",
                        kind="calendar_pre", source="calendar_auto")
            end = schedule_store.to_utc(edt)
            if end:
                post = end + timedelta(minutes=CAL_POST_OFFSET_MIN)
                if post > now:
                    schedule_store.upsert(
                        email, f"cal:post:{eid}", post.isoformat(),
                        f"Text the user like a friend after '{summary}': how did it go? "
                        f"1–3 sentences, casual — ask, don't report. Nudge them to log it "
                        f"if they haven't.",
                        kind="calendar_post", source="calendar_auto")

        # Daily buddy check-in (replaces the old weekly goal_checkin — a buddy checks
        # in every day, not once a week; the Monday fire adds a goal review, see
        # _fire_due). Migration: cancel any leftover weekly entry once, idempotently.
        try:
            existing = {e.get("reason_key") for e in schedule_store.list_for(email)}
            if "goal_checkin" in existing:
                schedule_store.cancel(email, "goal_checkin")
            if "daily_checkin" not in existing:
                schedule_store.upsert(
                    email, "daily_checkin", _next_morning_berlin(), DAILY_CHECKIN_NOTE,
                    kind="daily_checkin", source="calendar_auto",
                    recurrence={"every_days": 1})
        except Exception:  # noqa: BLE001
            pass

        # "Daily-ish" panel refresh: re-enqueue any active goal whose panel is stale
        # (there's no literal daily tick — this rides the existing hourly scan).
        try:
            _enqueue_stale_goal_panels(email, now)
        except Exception:  # noqa: BLE001
            pass


def _enqueue_stale_goal_panels(email: str, now: datetime) -> None:
    from core import goal_build_queue, goal_store
    stale_after = timedelta(hours=GOAL_PANEL_STALE_HOURS)
    for g in goal_store.list_goals(email):
        if g.get("status") != "active" or g.get("panel_status") == "building":
            continue
        stamp = g.get("panel_updated_at")
        is_stale = True
        if stamp:
            try:
                age = now - datetime.fromisoformat(stamp.replace("Z", "+00:00"))
                is_stale = age > stale_after
            except ValueError:
                is_stale = True
        if is_stale:
            goal_build_queue.enqueue(email, g["id"])


async def _scheduler_loop(client) -> None:
    """The durable poll loop (an asyncio task on the Telethon event loop)."""
    import time as _time
    log.info("Proactive scheduler started (poll=%ss, calendar scan=%ss).",
             SCHEDULE_POLL_SECONDS, CAL_SCAN_SECONDS)
    last_cal = 0.0
    while True:
        try:
            now = datetime.now(timezone.utc)
            await _fire_due(client, now)
            await _drain_outbox(client)
            if _time.monotonic() - last_cal > CAL_SCAN_SECONDS:
                last_cal = _time.monotonic()
                loop = asyncio.get_running_loop()
                await loop.run_in_executor(None, _run_calendar_autoschedule)
        except Exception:  # noqa: BLE001 — a scheduler error must never kill the bridge
            log.exception("scheduler tick failed")
        await asyncio.sleep(SCHEDULE_POLL_SECONDS)


async def _goal_build_loop() -> None:
    """Drains core.goal_build_queue on its OWN short cadence — independent of
    _scheduler_loop's 60s cadence — so a form-created/refreshed goal's "building…"
    state actually resolves in seconds rather than up to a minute. Spawns
    core.goal_panel.build_panel per queued item on a daemon thread (build_panel
    itself is bounded: recursion_limit + wall-clock timeout + a concurrency
    semaphore), then removes the queue entry immediately — a build that crashes
    mid-way is caught by the hourly staleness sweep, never silently lost."""
    from core import goal_build_queue, goal_panel
    log.info("Goal-panel build loop started (poll=%ss).", GOAL_BUILD_POLL_SECONDS)
    while True:
        try:
            for path, rec in goal_build_queue.pending():
                goal_build_queue.remove(path)
                threading.Thread(target=goal_panel.build_panel,
                                 args=(rec["user"], rec["goal_id"]), daemon=True).start()
        except Exception:  # noqa: BLE001 — must never kill the bridge
            log.exception("goal build drain failed")
        await asyncio.sleep(GOAL_BUILD_POLL_SECONDS)


# ── Startup ──────────────────────────────────────────────────────────────────────

def _build_client():
    from telethon import TelegramClient
    from telethon.sessions import StringSession
    return TelegramClient(StringSession(SESSION), _api_id(), API_HASH)


async def _run_headless_worker() -> None:
    """No Telegram configured — run ONLY the durable loops (proactive scheduler +
    goal-panel build queue). Deliveries still mirror into the web Coach chat
    (delivery handles tg_client=None); only the Telegram push is skipped. Without
    this, a machine without Telegram had NO queue drainer at all and every
    form-created goal panel stayed on "building…" forever."""
    log.warning(
        "No Telegram config (TELEGRAM_API_ID/TELEGRAM_API_HASH/session) — running "
        "HEADLESS: proactive scheduler + goal-panel builds only, no Telegram chat. "
        "Set the env vars and restart to enable the Telegram userbot."
    )
    scheduler_task = asyncio.create_task(_scheduler_loop(None))
    goal_build_task = asyncio.create_task(_goal_build_loop())
    try:
        await asyncio.gather(scheduler_task, goal_build_task)
    finally:
        scheduler_task.cancel()
        goal_build_task.cancel()


async def _run_bridge() -> None:
    from telethon import events

    if not API_HASH or not SESSION:
        await _run_headless_worker()
        return
    if _USING_SHARED_SESSION:
        log.warning(
            "Reusing TELEGRAM_SESSION_STRING — fine on its own. Just don't keep the "
            "telegram-mcp proxy connected on this SAME session at the same time, or "
            "Telegram may revoke the key. To run both at once, make a dedicated login: "
            "python telegram_bridge.py --login → TELEGRAM_BRIDGE_SESSION_STRING"
        )

    client = _build_client()
    await client.connect()
    if not await client.is_user_authorized():
        await client.disconnect()
        raise SystemExit(
            "Session string is not authorized. Generate a fresh one with:\n"
            "    python telegram_bridge.py --login"
        )

    me = await client.get_me()
    log.info(
        "Listening as @%s (id=%s) · scope=%s · access=%s",
        me.username, me.id,
        "private+groups" if ALLOW_GROUPS else "private only",
        ("allowlist: " + ", ".join(sorted(ALLOWLIST))) if ALLOWLIST else "anyone",
    )

    # Warm the tool registry up-front so the first message isn't slow / fails loudly
    # if the MCP servers aren't running.
    try:
        loop = asyncio.get_running_loop()
        reachable = await loop.run_in_executor(None, _get_orchestrator().refresh_tools)
        if reachable:
            log.info("Agent layer reachable — orchestrator (:9000) is up.")
        else:
            log.warning("Orchestrator agent (:9000) not reachable — start it with "
                        "`python -m core.orchestrator_agent` (and the specialists). Continuing anyway.")
    except Exception:
        log.exception("agent-layer reachability check failed — continuing anyway")

    # Always handle self-messages (Saved Messages / self-chat).
    # The skip-id guard in _handle_message prevents the bridge's own replies from
    # re-triggering the orchestrator.
    client.add_event_handler(_handle_message, events.NewMessage(outgoing=True, chats=[me.id]))
    if INTERNAL_ONLY:
        log.info(
            "Bridge is up — INTERNAL ONLY mode. "
            "Write to your own Saved Messages on Telegram to talk to the agent. Ctrl-C to stop."
        )
    else:
        # Also listen to incoming DMs (and groups if ALLOW_GROUPS is set)
        client.add_event_handler(_handle_message, events.NewMessage(incoming=True))
        log.info(
            "Bridge is up — PUBLIC mode (accessible to %s). "
            "Write your Telegram account a message to talk to the agent. Ctrl-C to stop.",
            ("allowlist: " + ", ".join(sorted(ALLOWLIST))) if ALLOWLIST else "anyone",
        )
    # Start the durable proactive scheduler on this event loop (cross-chat wake-ups,
    # outbox delivery, calendar auto-schedule). It shares _RUN_LOCK with inbound chat.
    scheduler_task = asyncio.create_task(_scheduler_loop(client))
    # A separate, faster loop just for goal-panel builds (form-created/refreshed
    # goals) — independent cadence so "building…" resolves in seconds, not up to a
    # minute. Needs no Telethon client (panel builds never send Telegram messages).
    goal_build_task = asyncio.create_task(_goal_build_loop())
    try:
        await client.run_until_disconnected()
    finally:
        scheduler_task.cancel()
        goal_build_task.cancel()


async def _login() -> None:
    """Interactive one-time login → prints a StringSession to put in .env."""
    from telethon import TelegramClient
    from telethon.sessions import StringSession

    if not API_HASH:
        raise SystemExit("Set TELEGRAM_API_ID and TELEGRAM_API_HASH in .env first.")
    print("\nTraining Copilot Telegram bridge — session login")
    print("Enter your phone in full international format, e.g. +49170…")
    print("⚠ The login code is sent INSIDE Telegram (the 'Telegram' service chat on your")
    print("  already-logged-in app/desktop) — NOT by SMS. If no code arrives, try --login-qr.\n")
    client = TelegramClient(StringSession(), _api_id(), API_HASH)
    await client.start()  # prompts for phone / code / password as needed
    session_string = client.session.save()
    await client.disconnect()
    print("\n✅ Login successful. Add this line to your .env:\n")
    print(f"TELEGRAM_BRIDGE_SESSION_STRING={session_string}\n")
    print("Then start the bridge with:  python telegram_bridge.py")


def _print_qr(url: str) -> None:
    """Render a scannable QR for the login URL (falls back to printing the URL)."""
    try:
        import qrcode
        qr = qrcode.QRCode(border=1)
        qr.add_data(url)
        qr.make(fit=True)
        qr.print_ascii(invert=True)
    except Exception:  # noqa: BLE001 — qrcode not installed
        print("(Tip: `pip install qrcode` to render a scannable QR right here.)")
        print("Otherwise paste this URL into any QR generator and scan the image in Telegram:\n")
        print(url + "\n")


async def _login_qr() -> None:
    """QR-code login — no SMS / app code needed; scan with your phone.

    In Telegram: Settings → Devices → Link Desktop Device, then scan the QR.
    """
    from telethon import TelegramClient
    from telethon.errors import SessionPasswordNeededError
    from telethon.sessions import StringSession

    if not API_HASH:
        raise SystemExit("Set TELEGRAM_API_ID and TELEGRAM_API_HASH in .env first.")
    print("\nTraining Copilot Telegram bridge — QR login")
    print("On your phone: Telegram → Settings → Devices → Link Desktop Device, then scan:\n")
    client = TelegramClient(StringSession(), _api_id(), API_HASH)
    await client.connect()
    qr = await client.qr_login()
    _print_qr(qr.url)
    print("Waiting for the scan… (the QR auto-refreshes; Ctrl-C to abort)")
    while True:
        try:
            await qr.wait(timeout=30)
            break
        except asyncio.TimeoutError:
            await qr.recreate()
            print("\n…QR expired — fresh one:\n")
            _print_qr(qr.url)
        except SessionPasswordNeededError:
            import getpass
            await client.sign_in(password=getpass.getpass("Two-step (2FA) password: "))
            break
    session_string = client.session.save()
    await client.disconnect()
    print("\n✅ Login successful. Add this line to your .env:\n")
    print(f"TELEGRAM_BRIDGE_SESSION_STRING={session_string}\n")
    print("Then start the bridge with:  python telegram_bridge.py")


def main() -> None:
    args = sys.argv[1:]
    if "--login-qr" in args:
        asyncio.run(_login_qr())
        return
    if "--login" in args:
        asyncio.run(_login())
        return
    try:
        asyncio.run(_run_bridge())
    except KeyboardInterrupt:
        log.info("Stopped.")


if __name__ == "__main__":
    main()
