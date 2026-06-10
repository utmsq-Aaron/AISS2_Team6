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
        try:
            await event.respond(chunk, parse_mode="md", link_preview=False)
        except Exception:
            try:
                await event.respond(chunk, parse_mode=None, link_preview=False)
            except Exception:
                log.exception("failed to send a reply chunk")


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

    try:
        png = await loop.run_in_executor(None, render_route_image, route_data)
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
            await event.client.send_file(event.chat_id, bio, caption=caption, force_document=False)

        # No photo (or caption too long) → send the link on its own so it's still tappable.
        if link_line and not link_inline:
            await event.respond(link_line, parse_mode=None, link_preview=True)

        if gpx:
            gio = io.BytesIO(gpx)
            gio.name = "route.gpx"
            await event.client.send_file(
                event.chat_id, gio, force_document=True,
                caption="Exact route as GPX — open in OsmAnd, Komoot, Organic Maps, Garmin or Strava.",
            )
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
    raw = _get_orchestrator().host.call_tool("strava__get_activity_streams", {"activity_id": activity_id})
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

    await _send_text(event, f"🎬 Rendering flythrough for *{name}*… (this may take ~30 s)")

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
        await event.client.send_file(
            event.chat_id, bio,
            caption=f"🎬 {name}",
            force_document=False,
        )
        log.info("flythrough MP4 sent (%d bytes) for activity %s", len(mp4_bytes), activity_id)
    except Exception:
        log.exception("failed to send flythrough MP4 to Telegram")


# ── Message handler ──────────────────────────────────────────────────────────────

async def _handle_message(event) -> None:
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

    # Flythrough: render MP4 server-side and send as video
    ft_action = next(
        (a for a in ((trace or {}).get("actions") or []) if a.get("type") == "flythrough"),
        None,
    )
    if ft_action:
        await _send_flythrough(event, ft_action)

    log.info("→ chat=%s: replied (%d chars)%s%s",
             chat_id, len(answer or ""),
             " +route" if route_data else "",
             " +flythrough" if ft_action else "")


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
        n_tools = await loop.run_in_executor(None, lambda: len(_get_orchestrator()._discover()))
        log.info("Agent ready — %d tools discovered.", n_tools)
    except Exception:
        log.exception("tool discovery failed — are the MCP servers running? continuing anyway")

    client.add_event_handler(_handle_message, events.NewMessage(incoming=True))
    log.info("Bridge is up. Send your Telegram account a message to talk to the agent. Ctrl-C to stop.")
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
