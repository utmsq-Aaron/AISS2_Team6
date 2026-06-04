"""Setup wizard — guided first-time configuration for HealthBot."""

import os
import threading
import time
import urllib.parse

import streamlit as st
from dotenv import load_dotenv

from ui.shared import garmin_connected, strava_connected
from ui.styles import ACCENT, BG_CARD, BORDER, C_AMBER, C_GREEN, TEXT_MUTED, TEXT_PRIMARY

load_dotenv()


# ── State helpers ─────────────────────────────────────────────────────────────

def _ok(key: str) -> bool:
    val = os.getenv(key, "")
    if not val:
        return False
    lower = val.lower()
    # Treat .env.example placeholder values as unset
    return "your" not in lower and not lower.endswith("_here") and "example" not in lower

def _mock() -> bool:
    return os.getenv("GARMIN_MOCK_HEALTH", "").lower() in ("1", "true")

def _step1_done() -> bool:
    base = all(_ok(k) for k in ("CLIENT_ID", "CLIENT_SECRET", "OPENAI_API_KEY", "AGENT_MODEL"))
    garmin_ok = _mock() or _ok("GARMIN_EMAIL") and _ok("GARMIN_PASSWORD")
    return base and garmin_ok

def _step2_done() -> bool:
    return strava_connected()

def _step3_done() -> bool:
    return _mock() or garmin_connected()

def _all_done() -> bool:
    return _step1_done() and _step2_done() and _step3_done()


# ── Visual helpers ─────────────────────────────────────────────────────────────

def _progress_bar(steps: list[tuple[str, bool]]) -> None:
    """Render a horizontal step-progress indicator."""
    n = len(steps)
    done_count = sum(1 for _, ok in steps if ok)

    # Build HTML for each step node + connector
    nodes = []
    for i, (label, ok) in enumerate(steps):
        color  = C_GREEN  if ok  else (ACCENT if i == done_count else "#3A3A5C")
        border = "none"   if ok  else f"2px solid {color}"
        bg     = color    if ok  else ("transparent" if i != done_count else "transparent")
        icon   = "✓"      if ok  else str(i + 1)
        text_c = TEXT_PRIMARY if i <= done_count else TEXT_MUTED

        node_html = f"""
        <div style="display:flex;flex-direction:column;align-items:center;gap:6px;flex:1">
          <div style="width:32px;height:32px;border-radius:50%;background:{bg};
                      border:{border};display:flex;align-items:center;justify-content:center;
                      font-size:13px;font-weight:700;color:{color};">{icon}</div>
          <span style="font-size:11px;color:{text_c};font-weight:{'600' if i==done_count else '400'};
                       text-align:center;white-space:nowrap">{label}</span>
        </div>"""
        nodes.append(node_html)

        if i < n - 1:
            line_color = C_GREEN if ok else BORDER
            nodes.append(
                f'<div style="flex:2;height:2px;background:{line_color};margin-top:15px;'
                f'border-radius:2px"></div>'
            )

    st.markdown(
        f'<div style="display:flex;align-items:flex-start;padding:8px 0 20px 0">'
        + "".join(nodes)
        + "</div>",
        unsafe_allow_html=True,
    )


def _step_header(num: int, title: str, done: bool) -> None:
    badge_bg  = C_GREEN if done else ACCENT
    badge_txt = "✓" if done else str(num)
    status    = f'<span style="color:{C_GREEN};font-size:12px;font-weight:600">Erledigt</span>' \
                if done else \
                f'<span style="color:{C_AMBER};font-size:12px;font-weight:600">Ausstehend</span>'
    st.markdown(
        f'<div style="display:flex;align-items:center;gap:10px;margin-bottom:4px">'
        f'<div style="width:26px;height:26px;border-radius:50%;background:{badge_bg};'
        f'display:flex;align-items:center;justify-content:center;font-size:12px;'
        f'font-weight:700;color:#fff;flex-shrink:0">{badge_txt}</div>'
        f'<span style="font-size:1.05rem;font-weight:700;color:{TEXT_PRIMARY}">{title}</span>'
        f'<span style="margin-left:auto">{status}</span>'
        f'</div>',
        unsafe_allow_html=True,
    )


def _env_row(key: str, label: str, hint: str) -> None:
    ok = _ok(key)
    color = C_GREEN if ok else "#EF4444"
    icon  = "✓" if ok else "✗"
    val   = f'<code style="color:{TEXT_MUTED};font-size:11px">{os.getenv(key, "")[:4]}…</code>' \
            if ok else \
            f'<span style="color:{TEXT_MUTED};font-size:11px">{hint}</span>'
    st.markdown(
        f'<div style="display:flex;align-items:center;gap-x:8px;padding:5px 0;'
        f'border-bottom:1px solid {BORDER}">'
        f'<span style="color:{color};font-weight:700;font-size:13px;width:16px">{icon}</span>'
        f'<code style="color:{TEXT_PRIMARY};font-size:13px;flex:1;margin-left:8px">{key}</code>'
        f'<span style="margin-left:8px">{val}</span>'
        f'</div>',
        unsafe_allow_html=True,
    )


# ── Main render ───────────────────────────────────────────────────────────────

def render_setup() -> None:
    st.markdown("## 🛠️ Setup")

    if _all_done():
        st.success("**Alles eingerichtet** — HealthBot ist einsatzbereit. 🎉")
        st.divider()

    steps = [
        (".env",   _step1_done()),
        ("Strava", _step2_done()),
        ("Garmin", _step3_done()),
    ]
    _progress_bar(steps)

    # ── Schritt 1: .env ───────────────────────────────────────────────────────
    with st.container(border=True):
        _step_header(1, ".env konfigurieren", _step1_done())
        st.caption("Kopiere `.env.example` → `.env` im Projektverzeichnis und trage deine Werte ein.")

        st.markdown("<div style='margin-top:12px'></div>", unsafe_allow_html=True)

        # Strava credentials
        st.markdown(
            f'<p style="font-size:11px;font-weight:600;color:{TEXT_MUTED};'
            f'text-transform:uppercase;letter-spacing:.7px;margin:0 0 4px 0">Strava</p>',
            unsafe_allow_html=True,
        )
        _env_row("CLIENT_ID",     "CLIENT_ID",     "Strava App ID")
        _env_row("CLIENT_SECRET", "CLIENT_SECRET", "Strava App Secret")

        st.markdown("<div style='margin-top:10px'></div>", unsafe_allow_html=True)

        # AI credentials
        st.markdown(
            f'<p style="font-size:11px;font-weight:600;color:{TEXT_MUTED};'
            f'text-transform:uppercase;letter-spacing:.7px;margin:0 0 4px 0">KI / OpenAI</p>',
            unsafe_allow_html=True,
        )
        _env_row("OPENAI_API_KEY", "OPENAI_API_KEY", "API-Key")
        _env_row("AGENT_MODEL",    "AGENT_MODEL",    "z.B. azure.gpt-4.1")

        st.markdown("<div style='margin-top:10px'></div>", unsafe_allow_html=True)

        # Garmin credentials (conditional)
        st.markdown(
            f'<p style="font-size:11px;font-weight:600;color:{TEXT_MUTED};'
            f'text-transform:uppercase;letter-spacing:.7px;margin:0 0 4px 0">Garmin (optional)</p>',
            unsafe_allow_html=True,
        )
        if _mock():
            st.markdown(
                f'<div style="padding:6px 10px;background:rgba(34,197,94,.1);'
                f'border-left:3px solid {C_GREEN};border-radius:4px;font-size:13px">'
                f'Mock-Modus aktiv — <code>GARMIN_MOCK_HEALTH=true</code></div>',
                unsafe_allow_html=True,
            )
        else:
            _env_row("GARMIN_EMAIL",    "GARMIN_EMAIL",    "Garmin-Konto E-Mail")
            _env_row("GARMIN_PASSWORD", "GARMIN_PASSWORD", "Garmin-Konto Passwort")

        if not _step1_done():
            st.markdown("<div style='margin-top:12px'></div>", unsafe_allow_html=True)
            st.info(
                "Nach dem Ausfüllen der `.env`: **App neu starten** damit die Werte geladen werden.",
                icon="ℹ️",
            )

    # ── Schritt 2: Strava ─────────────────────────────────────────────────────
    with st.container(border=True):
        _step_header(2, "Strava verbinden", _step2_done())

        if _step2_done():
            st.caption("OAuth-Token gespeichert. Strava-Daten werden automatisch abgerufen.")

        else:
            has_creds = _ok("CLIENT_ID") and _ok("CLIENT_SECRET")

            if not has_creds:
                st.markdown(
                    f'<p style="color:{TEXT_MUTED};font-size:13px;margin:8px 0 4px 0">'
                    f'Strava benötigt eine eigene API-App. Einmalig, dauert ca. 2 Minuten.</p>',
                    unsafe_allow_html=True,
                )
                st.info(
                    "**Voraussetzung:** Du brauchst ein aktives Strava-Konto unter "
                    "[strava.com](https://www.strava.com). Kostenlos reicht aus.",
                    icon="ℹ️",
                )

                st.markdown("<div style='margin-top:10px'></div>", unsafe_allow_html=True)

                for i, label in enumerate([
                    "Strava API-Seite öffnen",
                    "App erstellen & Formular ausfüllen",
                    "Client ID + Client Secret in .env eintragen",
                    "App neu starten → Schritt 2b: Token holen",
                ]):
                    active = i == 0
                    num_color = ACCENT if active else TEXT_MUTED
                    txt_color = TEXT_PRIMARY if active else TEXT_MUTED
                    st.markdown(
                        f'<div style="display:flex;align-items:center;gap:10px;'
                        f'padding:7px 0;border-bottom:1px solid {BORDER}">'
                        f'<span style="color:{num_color};font-weight:700;font-size:13px;'
                        f'width:18px;flex-shrink:0">{i+1}.</span>'
                        f'<span style="font-size:13px;color:{txt_color}">{label}</span>'
                        f'</div>',
                        unsafe_allow_html=True,
                    )

                st.markdown("<div style='margin-top:14px'></div>", unsafe_allow_html=True)
                st.link_button(
                    "→ Strava API-Seite öffnen  (strava.com/settings/api)",
                    "https://www.strava.com/settings/api",
                    type="primary",
                    use_container_width=True,
                )

                with st.expander("Formular: Was genau wo eintragen?"):
                    st.markdown("""
| Feld | Was eintragen |
|---|---|
| **Application Name** | frei wählbar, z.B. `HealthBot` |
| **Category** | `Health and Fitness` |
| **Club** | leer lassen |
| **Website** | `http://localhost:8501` ← Port deiner Streamlit-App |
| **Application Description** | kurze Beschreibung, z.B. `Personal sports dashboard` |
| **Authorization Callback Domain** | `localhost` ← nur die Domain, **kein** Port, **kein** http:// |

> **Warum zwei verschiedene Ports?**
> - Port **8501** = Streamlit-App (dein Browser)
> - Port **8080** = temporärer OAuth-Callback-Server (nur während der Erstanmeldung aktiv)
> Strava leitet nach der Autorisierung zu `localhost:8080/callback` weiter — das läuft automatisch beim Setup.

**Nach dem Speichern** erscheinen **Client ID** und **Client Secret** direkt auf derselben Seite unter dem App-Namen.

Trage sie in `.env` ein:
```
CLIENT_ID=123456
CLIENT_SECRET=abc123def456...
```

Dann App neu starten und in diesem Tab Schritt 2b ausführen.
""")

            else:
                # Has creds but not yet authorized → show auth options
                st.markdown(
                    f'<p style="color:{TEXT_MUTED};font-size:13px;margin:8px 0 12px 0">'
                    f'Credentials sind gesetzt. Jetzt noch den Zugriff auf Strava autorisieren.</p>',
                    unsafe_allow_html=True,
                )

                tab_terminal, tab_browser = st.tabs(["💻  Terminal (empfohlen)", "🌐  Browser-Link"])

                with tab_terminal:
                    st.markdown("""
**Einmalig im Terminal ausführen** (aus dem Projektverzeichnis):

```
python auth/strava_oauth.py
```

Das Script öffnet automatisch einen Browser, du autorisierst HealthBot auf Strava,
und der Token wird lokal gespeichert. Danach ist Strava dauerhaft verbunden.
""")

                with tab_browser:
                    cid = os.getenv("CLIENT_ID", "")
                    auth_url = (
                        "https://www.strava.com/oauth/authorize?"
                        + urllib.parse.urlencode({
                            "client_id":       cid,
                            "response_type":   "code",
                            "redirect_uri":    "http://localhost:8080/callback",
                            "approval_prompt": "force",
                            "scope":           "read,activity:read_all,activity:write",
                        })
                    )
                    st.markdown("""
**Nur funktionsfähig wenn `python auth/strava_oauth.py` gleichzeitig im Terminal läuft**
(der Callback-Server auf Port 8080 muss aktiv sein).

1. Terminal öffnen → `python auth/strava_oauth.py` starten
2. Dann diesen Link klicken:
""")
                    st.link_button("→ Strava autorisieren", auth_url, use_container_width=True)
                    st.caption("Nach der Autorisierung erscheint 'Authorization successful' im Terminal.")

                st.markdown("<div style='margin-top:10px'></div>", unsafe_allow_html=True)
                if st.button("🔄  Status prüfen", key="check_strava"):
                    st.rerun()

    # ── Schritt 3: Garmin ─────────────────────────────────────────────────────
    with st.container(border=True):
        _step_header(3, "Garmin verbinden  (optional)", _step3_done())
        st.caption("Aktiviert den Health-Tab mit Schlaf, HRV, Body Battery und Trainingsdaten.")

        if _mock():
            st.markdown(
                f'<div style="margin-top:10px;padding:10px 14px;'
                f'background:rgba(34,197,94,.08);border-left:3px solid {C_GREEN};'
                f'border-radius:6px">'
                f'<b>Mock-Modus aktiv</b> — synthetische Gesundheitsdaten werden verwendet.<br>'
                f'<span style="color:{TEXT_MUTED};font-size:12px">'
                f'Kein echtes Garmin-Konto nötig. Ideal zum Entwickeln und Testen.</span></div>',
                unsafe_allow_html=True,
            )

        elif garmin_connected():
            st.markdown(
                f'<div style="margin-top:10px;padding:10px 14px;'
                f'background:rgba(34,197,94,.08);border-left:3px solid {C_GREEN};'
                f'border-radius:6px">'
                f'<b>Garmin verbunden</b> — Token gefunden, Health-Tab ist aktiv.</div>',
                unsafe_allow_html=True,
            )

        else:
            st.markdown("<div style='margin-top:8px'></div>", unsafe_allow_html=True)

            tab_mock, tab_real = st.tabs(["🧪  Mock-Modus", "👤  Echtes Konto"])

            with tab_mock:
                st.markdown("""
**Für Entwickler & Tester** — kein Garmin-Konto erforderlich.

Setze in `.env`:
```
GARMIN_MOCK_HEALTH=true
```
Dann App neu starten. Der Health-Tab zeigt realistische Beispieldaten.
""")

            with tab_real:
                garmin_ready = _ok("GARMIN_EMAIL") and _ok("GARMIN_PASSWORD")

                if not garmin_ready:
                    st.warning(
                        "`GARMIN_EMAIL` und `GARMIN_PASSWORD` müssen zuerst in `.env` eingetragen sein (Schritt 1).",
                        icon="⚠️",
                    )
                else:
                    st.markdown(f"""
**Einmalig im Terminal ausführen** (aus dem Projektverzeichnis):

```
python auth/garmin_setup.py
```

Das Script liest die Credentials aus `.env`, speichert den Token lokal und fragt
bei aktivem MFA nach einem OTP-Code. Danach ist der Health-Tab dauerhaft aktiv.
""")
                    if st.button("🔄  Token-Status prüfen", key="check_garmin"):
                        st.rerun()

    # ── Abschluss ─────────────────────────────────────────────────────────────
    if _all_done():
        st.divider()
        col1, col2, col3 = st.columns(3)
        col1.metric("Schritt 1", "✅ .env")
        col2.metric("Schritt 2", "✅ Strava")
        col3.metric("Schritt 3", "✅ Garmin")
    else:
        done_n = sum([_step1_done(), _step2_done(), _step3_done()])
        st.markdown(
            f'<p style="text-align:center;color:{TEXT_MUTED};font-size:13px;margin-top:16px">'
            f'{done_n} von 3 Schritten abgeschlossen</p>',
            unsafe_allow_html=True,
        )
