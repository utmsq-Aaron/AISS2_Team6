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

This runs as a **userbot**: it logs in as *your* Telegram account and replies
*as you* to whoever messages you. It is a long-running process, separate from
Streamlit and from the MCP servers.

Run (after the MCP servers are up, inside the app's Python env):

    python telegram_bridge.py            # start listening
    python telegram_bridge.py --login    # one-time: generate a session string

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
from collections import defaultdict, deque
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

    log.info("← chat=%s @%s: %s", chat_id, getattr(sender, "username", ""), text[:120])
    history_before = list(_histories[chat_id])

    try:
        async with event.client.action(chat_id, "typing"):
            async with _RUN_LOCK:
                loop = asyncio.get_running_loop()
                answer, trace = await loop.run_in_executor(
                    None, _get_orchestrator().run, text, history_before
                )
    except Exception as exc:
        log.exception("orchestrator run failed")
        await _send_text(event, f"⚠️ Error processing your message: {exc}")
        return

    # Record the turn only after a successful run.
    _histories[chat_id].append({"role": "user", "content": text})
    _histories[chat_id].append({"role": "assistant", "content": answer or ""})

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


# ── Startup ──────────────────────────────────────────────────────────────────────

def _build_client():
    from telethon import TelegramClient
    from telethon.sessions import StringSession
    return TelegramClient(StringSession(SESSION), _api_id(), API_HASH)


async def _run_bridge() -> None:
    from telethon import events

    if not API_HASH or not SESSION:
        raise SystemExit(
            "Missing Telegram config. Need TELEGRAM_API_ID, TELEGRAM_API_HASH and a "
            "session string (TELEGRAM_BRIDGE_SESSION_STRING, or TELEGRAM_SESSION_STRING).\n"
            "Generate one with:  python telegram_bridge.py --login"
        )
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
    await client.run_until_disconnected()


async def _login() -> None:
    """Interactive one-time login → prints a StringSession to put in .env."""
    from telethon import TelegramClient
    from telethon.sessions import StringSession

    if not API_HASH:
        raise SystemExit("Set TELEGRAM_API_ID and TELEGRAM_API_HASH in .env first.")
    print("\nTraining Copilot Telegram bridge — session login")
    print("You will be asked for your phone number, the login code, and 2FA password if set.\n")
    client = TelegramClient(StringSession(), _api_id(), API_HASH)
    await client.start()  # prompts for phone / code / password as needed
    session_string = client.session.save()
    await client.disconnect()
    print("\n✅ Login successful. Add this line to your .env:\n")
    print(f"TELEGRAM_BRIDGE_SESSION_STRING={session_string}\n")
    print("Then start the bridge with:  python telegram_bridge.py")


def main() -> None:
    if "--login" in sys.argv[1:]:
        asyncio.run(_login())
        return
    try:
        asyncio.run(_run_bridge())
    except KeyboardInterrupt:
        log.info("Stopped.")


if __name__ == "__main__":
    main()
