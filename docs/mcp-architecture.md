# FitDash — MCP-Architektur

**Zweck dieses Dokuments:** Die aktuelle Architektur sauber beschreiben — wie sie heute im Code steht, warum sie dem Anthropic-/MCP-Standard folgt und wie sie sich um **externe MCP-Server** erweitern lässt.

> **Verhältnis zu den anderen Docs**
> - [`docs/architecture-review.md`](architecture-review.md) — das *Warum* (kritisches Review der alten Architektur, das den Umbau begründet). Dient als historische Referenz.
> - [`ARCHITECTURE.md`](../ARCHITECTURE.md) — Kurzübersicht / Redirect auf dieses Dokument.
> - **Dieses Dokument** — das maßgebliche *Was/Wie*. Für alle neuen Server gilt dieses Dokument.

---

## 1. Designprinzipien (Anthropic-/MCP-Standard)

Die Architektur folgt bewusst dem Modell, das Anthropic für MCP-Hosts beschreibt: **ein** uniformer Client spricht **viele** unabhängige Server, Tools werden **entdeckt statt verdrahtet**, und **Auth ist von der Tool-Deklaration getrennt**.

| Prinzip | Umsetzung im Code |
|---|---|
| **Tool-agnostisch** — kein Code kennt ein Tool beim Namen | `core/orchestrator.py`: das Modell entdeckt Tools per `list_tools()` und entscheidet selbst, was es ruft. |
| **Ein Aufrufpfad** — eigene = externe Server | `core/host.ToolHost.call_tool()` / `list_tools()` — die *einzige* Tool-Fläche für UI, API und Agenten. |
| **Server = eigenständige Services** | `servers/*_mcp.py`: native FastMCP-Server über Streamable HTTP, je eigener Prozess/Port/Container. |
| **Entdeckung statt Hardcoding** | Tools kommen aus den Servern; ein nicht erreichbarer Server wird übersprungen, nie hartkodiert. |
| **Namespacing** | Tool-Namen sind `server__tool` (OpenAI-function-name-safe; Trenner `SEP = "__"`). |
| **Auth getrennt von der Deklaration (Vault-Muster)** | Credentials sind **Connection-Header** pro Server, nie Tool-Argument und nie im Modell-Kontext. |
| **Vendor-neutral** | `core/llm.py`: Provider/Modell aus Config/Env; Provider-Wechsel = Config-Änderung, kein Code. |

---

## 1a. Agentenschicht — LangGraph + A2A

Der Chat-Motor ist heute ein **Multi-Agenten-System** auf Basis von **LangGraph** und dem **A2A-Protokoll** (offizielles `a2a-sdk`, pydantic-/Tutorial-API). Die MCP-Schicht und die Prinzipien aus §1 bleiben unverändert — die Agenten sind nur eine neue Ebene **oberhalb** von `ToolHost`.

- **Orchestrator-Agent** (`core/orchestrator_agent.py`, A2A-Server `:9000`): LangGraph-Agent (`langchain.agents.create_agent`), dessen einzige Tools `ask_<spezialist>` sind — jeder Aufruf ist eine A2A-Anfrage an einen Spezialisten. Er zerlegt die Anfrage, delegiert (parallel, wenn das Modell mehrere Tool-Calls ausgibt), sammelt die DataPart-Artefakte der Spezialisten und baut die `trace` via `core/agent_trace.build_trace`. **Kein** eigener MCP-Zugriff.
- **Spezialisten** (`agents/{recovery,load,context,route}_agent.py`, `:9001`–`:9004`): je ein LangGraph-ReAct-Agent über einen **auf seine MCP-Server beschränkten ToolHost** (`core/mcp_langchain.scoped_host`; Scope-Map in `core/config.AGENT_MCP_SCOPE`): recovery→garmin, load→strava+garmin, context→weather+calendar, route→routes. Tools werden weiterhin **entdeckt, nie hartkodiert** — nur pro Agent verengt. Jeder liefert seine rohen MCP-Ergebnisse (vollständig, als JSON-String) als DataPart-Artefakt zurück, damit der Orchestrator Karten/Charts/Trace bauen kann.
- **`core/orchestrator.py`** ist jetzt ein dünner **A2A-Client-Adapter** zum Orchestrator-Agenten und erhält den öffentlichen Vertrag `run()/refresh_tools()` — UI, FastAPI-SSE und Telegram-Bridge bleiben unverändert.
- **Registry & Betrieb**: `core/config.A2A_AGENTS` (name → URL, env-überschreibbar wie `RECOVERY_A2A_URL=…`); jeder Agent ist ein eigener Prozess/Port/Container mit Agent Card unter `/.well-known/agent-card.json`. Modell-Override für die Agentenschicht: `AGENT_LLM_MODEL` (empfohlen `kit.gpt-4.1`; `glm-4.7` ist für die Mehrfach-Calls unzuverlässig). Agenten laufen **non-streaming** (`ainvoke`); Fortschritt kommt als A2A-Status-Update, nicht als Token-Stream.

Datenpfad weiterhin: **Agent → `ToolHost` → MCP-Server**. Chat-Pfad: **UI → `FitDashOrchestrator` → (A2A) Orchestrator `:9000` → (A2A) Spezialisten `:9001`–`:9004` → `ToolHost` → MCP**.

---

## 2. Komponenten

```
                ┌──────────────── Frontends ────────────────┐
                │  Streamlit Chat-Tab   ·   (FastAPI / CLI)  │
                └─────────────────────┬──────────────────────┘
                                      │
                ┌─────────────────────▼──────────────────────┐
                │  core/  — Streamlit-frei, vendor-neutral    │
                │                                             │
                │  orchestrator.py  Tool-Use-Loop (agnostisch)│
                │  llm.py           LLM-Naht (Provider/Config)│
                │  host.py          ToolHost  list/call_tool  │
                │  config.py        Registry: name → MCP-URL  │
                └─────────────────────┬──────────────────────┘
                                      │  uniformer MCP-Client (Streamable HTTP)
        ┌─────────────────────────────┼─────────────────────────────┐
        ▼                             ▼                             ▼
  servers/weather_mcp.py     servers/routes_mcp.py        externe MCP-Server
  servers/strava_mcp.py      servers/garmin_mcp.py         (Nutzer, gleich behandelt)
  servers/calendar_mcp.py    (eigene FastMCP-Services)
```

### `core/config.py` — die Registry
Eine deklarative Tabelle `name → URL`. Eigene und externe Server haben dieselbe Form; einziger Unterschied ist die URL. **Einen Server hinzufügen = eine Zeile** (oder eine Env-Variable). Jede URL ist per Env überschreibbar: `WEATHER_MCP_URL=http://weather-mcp:8101/mcp` (z. B. im docker-compose, wo der Servicename der Host ist).

```python
MCP_SERVERS = {
    "weather":  _url("weather",  8101),
    "routes":   _url("routes",   8102),
    "strava":   _url("strava",   8103),
    "garmin":   _url("garmin",   8104),
    "calendar": _url("calendar", 8105),
}
```

### `core/host.py` — `ToolHost`
Der **einzige** MCP-Client der App. Eine uniforme Code-Bahn für jedes Tool, egal welcher Server es liefert:

- `alist_tools()` / `list_tools()` — entdeckt jedes Tool jedes **erreichbaren** Servers im OpenAI-Tool-Format; Namen werden `server__tool` genamespaced. Ein Server, der nicht läuft / nicht autorisiert / unerreichbar ist, wird **übersprungen** — er bricht nie die anderen.
- `acall_tool(name, args)` / `call_tool(...)` — zerlegt `server__tool`, routet an den Server, gibt Text/JSON zurück; Tool-Fehler werden als `{"error": ...}` zurückgegeben, nicht als Exception.
- **Async-Kern, Sync-Fassade:** Die echte Implementierung ist async (`mcp.client`); `_run()` überbrückt sie für den heutigen synchronen Streamlit-/Agenten-Code (frischer Event-Loop pro Aufruf, auch in ThreadPool-Workern sicher).
- **Auth pro Server:** `headers={"calendar": {"Authorization": "Bearer …"}}` wird als Connection-Header übergeben — getrennt von der Tool-Deklaration, nie im Tool-Kontext. `default_host` nutzt die globalen Server; **Pro-Nutzer-Hosts** werden explizit mit zusätzlichen Servern + Headern konstruiert.

### `core/llm.py` — die LLM-Naht
Eine Stelle, die den Chat-Client baut und das Modell auflöst — beides aus Env (`OPENAI_API_KEY`, `OPENAI_BASE_URL`, `AGENT_MODEL`). Heute ein OpenAI-kompatibler Endpoint (KIT-Gateway). Importiert bewusst **kein** Streamlit, damit der Kern standalone läuft (CLI, API, Tests, separater Service).

### `core/orchestrator.py` — der tool-agnostische Loop
**Ein** nativer Tool-Use-Loop ersetzt die alte 4-Agenten-Pipeline:

1. Tools einmal entdecken (gecacht), System-Prompt + (gekürzte) Historie + User-Input aufbauen.
2. Bis zu `MAX_ROUNDS` (6): Modell rufen mit `tools=…, tool_choice="auto"`. Liefert es Tool-Calls, werden alle über `ToolHost.call_tool()` ausgeführt und die Ergebnisse zurückgespeist; liefert es keine, ist das die Antwort.
3. Große Arrays (`points`, `waypoints`, `segments`, …) werden vor der Rückgabe ans Modell kompaktiert (`_clip`), damit der Kontext nicht zuläuft — die vollen Daten rendert das UI separat.
4. Es wird ein `trace` für das bestehende Streamlit-Debug-Panel und den Karten-Renderer gebaut. `ROUTE_TOOLS` dient **ausschließlich** dem UI (welches Ergebnis als Karte gezeichnet wird) — es steuert **nicht** die Tool-Auswahl.

---

## 3. Eigenen MCP-Server hinzufügen (das `*_mcp.py`-Muster)

Ein eigener Server ist eine in sich geschlossene Datei — kein `BaseMCPServer`, keine Dispatch-Indirektion, keine Registry-Klasse. Vorlage: `servers/weather_mcp.py`.

```python
# servers/example_mcp.py
import os
from mcp.server.fastmcp import FastMCP

mcp = FastMCP(
    "example",
    instructions="Kurz, was dieser Server kann.",
    host=os.getenv("EXAMPLE_MCP_HOST", "127.0.0.1"),
    port=int(os.getenv("EXAMPLE_MCP_PORT", "8106")),
    stateless_http=True,
)

@mcp.tool()
def do_something(value: str) -> dict:
    """Prägnante, präskriptive Beschreibung — das Modell wählt das Tool allein anhand
    dieses Texts. Sag, WANN es zu rufen ist und was die Argumente bedeuten."""
    return {"echo": value}

if __name__ == "__main__":
    mcp.run(transport="streamable-http")
```

Dann **eine Zeile** in `core/config.py`:

```python
"example": _url("example", 8106),
```

Starten: `python -m servers.example_mcp`. Mehr braucht es nicht — `ToolHost` entdeckt die Tools beim nächsten `list_tools()`, der Orchestrator kann sie sofort rufen. **Kein** Code in Host, Orchestrator oder UI nennt das neue Tool.

**Konventionen** (vgl. weather/routes/calendar):
- Tools sind möglichst **read-only** und geben Dicts zurück (FastMCP serialisiert sie als JSON-Text).
- Fehler als `{"error": "…"}` zurückgeben, nicht raisen.
- **Auth nie als Tool-Argument.** Per-Request-Token aus dem `Authorization`-Header der Verbindung lesen (siehe `servers/calendar_mcp.py::_bearer_from_request`) oder im Single-User-Dev aus einer Token-Datei.
- Minimale Scopes (Calendar nutzt nur `calendar.readonly`).

---

## 4. Externe MCP-Server einhängen (die Erweiterung)

Der entscheidende Vorteil dieser Standardisierung: **ein externer, nutzer-hinzugefügter Server ist für den Host nichts Besonderes** — er ist genau wie ein eigener nur ein weiterer Streamable-HTTP-Endpoint mit optionalen Auth-Headern.

```python
from core.host import ToolHost
from core.config import MCP_SERVERS

# Pro-Nutzer-Host: globale eigene Server + die vom Nutzer hinzugefügten externen
user_host = ToolHost(
    servers={**MCP_SERVERS, "notion": "https://mcp.example.com/notion/mcp"},
    headers={"notion": {"Authorization": f"Bearer {user_token}"}},
)
```

Der `FitDashOrchestrator` nimmt einen Host im Konstruktor (`FitDashOrchestrator(host=user_host)`) — derselbe Loop, dieselbe Tool-Fläche, der Nutzer bekommt zusätzlich die externen Tools, ohne dass eine Codezeile im Kern den neuen Server kennt. Im Mehrnutzerbetrieb wird `servers`/`headers` pro Nutzer aus einer Config/DB bzw. einem Secret-Vault befüllt statt aus dem globalen Default.

> ⚠️ **Sicherheit ist hier noch nicht fertig.** Externe Server sind eine große Angriffsfläche (SSRF, Daten-Exfiltration, Prompt-Injection über Tool-*Beschreibungen* und -*Outputs*). Allowlist/Genehmigungs-Flow, Sandboxing, Egress-Limit und „Tool-Output = untrusted" sind **noch offen** — siehe [`docs/architecture-review.md`](architecture-review.md) §3 (C-3) und Phase 4. Der externe Pfad oben ist die *Mechanik*; vor öffentlichem Launch braucht es die Mandanten-/Sicherheitsschicht davor.

---

## 5. Betrieb / Deployment

Jeder eigene Server ist ein eigenständiger FastMCP-Service — heute auf einem Host, später beliebig verschiebbar (nur die `*_MCP_URL` ändert sich, kein Code).

```bash
# Lokal, jeder Server in eigenem Prozess
python -m servers.weather_mcp     # :8101
python -m servers.routes_mcp      # :8102   (braucht ORS_API_KEY)
python -m servers.strava_mcp      # :8103   (braucht CLIENT_ID + CLIENT_SECRET)
python -m servers.garmin_mcp      # :8104   (braucht GARMIN_EMAIL + GARMIN_PASSWORD)
python -m servers.calendar_mcp    # :8105   (Google read-only)

# Oder containerisiert:
docker compose up --build weather-mcp routes-mcp strava-mcp garmin-mcp calendar-mcp
```

Die App (`ToolHost`) läuft auf dem Host und erreicht die Server über die veröffentlichten `localhost`-Ports. Um die App *innerhalb* von compose zu betreiben, die `*_MCP_URL` auf die Servicenamen zeigen und `allowed_hosts` der Server weiten (siehe Kommentare in [`docker-compose.yml`](../docker-compose.yml)).

| Server | Port | Backend | Auth |
|---|---|---|---|
| `weather` | 8101 | Open-Meteo | keine (kostenlos) |
| `routes` | 8102 | OpenRouteService + Overpass | `ORS_API_KEY` |
| `strava` | 8103 | Strava v3 REST API | OAuth2 (`.tokens/strava.json`) |
| `garmin` | 8104 | Garmin Connect (garminconnect) | Session-Token (`.tokens/`) |
| `calendar` | 8105 | Google Calendar (read-only) | Bearer (Header oder Token-Datei) |

---

## 6. Status & nächste Schritte

**Umgesetzt:** uniformer MCP-Host (`ToolHost`), tool-agnostischer Kern (`orchestrator`), alle fünf Server als native FastMCP-Services (weather/routes/strava/garmin/calendar), vendor-neutrale LLM-Naht, Tool-Namespacing, One-Host-Deployment, vollständige Legacy-Entfernung (Registry, BaseMCPServer, agents-Pipeline).

**Noch offen** (Details in [`docs/architecture-review.md`](architecture-review.md) §5 Phase 4–5):
- Mandanten-/Sicherheitsschicht (Pro-Nutzer-Identität, verschlüsselter Token-/Secret-Vault, Session-Isolation).
- Sandboxing/Allowlist/Egress-Kontrolle für nutzer-hinzugefügte Server; Tool-Output als untrusted behandeln.
- Logging statt `except: pass` an einigen Stellen; Contract-Tests an den Nähten.
