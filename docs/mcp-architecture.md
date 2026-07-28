# Training Copilot — MCP-Architektur

**Zweck dieses Dokuments:** Die aktuelle Architektur sauber beschreiben — wie sie heute im Code steht, warum sie dem Anthropic-/MCP-Standard folgt und wie sie sich um **externe MCP-Server** erweitern lässt.

> **Verhältnis zu den anderen Docs**
> - [`docs/architecture-review.md`](architecture-review.md) — das *Warum* (kritisches Review der alten Architektur, das den Umbau begründet). Dient als historische Referenz.
> - **Dieses Dokument** — das maßgebliche *Was/Wie*. Für alle neuen Server gilt dieses Dokument.

---

## 1. Designprinzipien (Anthropic-/MCP-Standard)

Die Architektur folgt bewusst dem Modell, das Anthropic für MCP-Hosts beschreibt: **ein** uniformer Client spricht **viele** unabhängige Server, Tools werden **entdeckt statt verdrahtet**, und **Auth ist von der Tool-Deklaration getrennt**.

| Prinzip | Umsetzung im Code |
|---|---|
| **Tool-agnostisch** — kein Code kennt ein Tool beim Namen | Jeder Spezialist in `agents/` entdeckt seine Tools per `list_tools()` (auf seinen Scope verengt) und entscheidet selbst, was er ruft. |
| **Ein Aufrufpfad** — eigene = externe Server | `core/host.ToolHost.call_tool()` / `list_tools()` — die *einzige* Tool-Fläche für Agenten, API und Bridge. |
| **Server = eigenständige Services** | `servers/*_mcp.py`: native FastMCP-Server über Streamable HTTP, je eigener Prozess/Port/Container. |
| **Entdeckung statt Hardcoding** | Tools kommen aus den Servern; ein nicht erreichbarer Server wird übersprungen, nie hartkodiert. |
| **Namespacing** | Tool-Namen sind `server__tool` (OpenAI-function-name-safe; Trenner `SEP = "__"`). |
| **Auth getrennt von der Deklaration (Vault-Muster)** | Credentials sind **Connection-Header** pro Server, nie Tool-Argument und nie im Modell-Kontext. |
| **Vendor-neutral** | `core/llm.py`: Provider/Modell aus Config/Env; Provider-Wechsel = Config-Änderung, kein Code. |

---

## 1a. Agentenschicht — LangGraph + A2A

Der Chat-Motor ist heute ein **Multi-Agenten-System** auf Basis von **LangGraph** und dem **A2A-Protokoll** (offizielles `a2a-sdk`, pydantic-/Tutorial-API). Die MCP-Schicht und die Prinzipien aus §1 bleiben unverändert — die Agenten sind nur eine neue Ebene **oberhalb** von `ToolHost`.

- **Orchestrator-Agent** (`core/orchestrator_agent.py`, A2A-Server `:9000`): LangGraph-Agent (`langchain.agents.create_agent`), dessen einzige Tools `ask_<spezialist>` sind — jeder Aufruf ist eine A2A-Anfrage an einen Spezialisten. Er zerlegt die Anfrage, delegiert (parallel, wenn das Modell mehrere Tool-Calls ausgibt), sammelt die DataPart-Artefakte der Spezialisten und baut die `trace` via `core/agent_trace.build_trace`. **Kein** eigener MCP-Zugriff.
- **Spezialisten** (`agents/{recovery,load,context,route,fitness,coach}_agent.py`, `:9001`–`:9006`): je ein LangGraph-ReAct-Agent über einen **auf seine MCP-Server beschränkten ToolHost** (`core/mcp_langchain.scoped_host`; Scope-Map in `core/config.AGENT_MCP_SCOPE`): recovery→garmin, load→strava+garmin+flythrough, context→weather+calendar, route→routes+google_maps, coach→athlete+strava+garmin, fitness→kein MCP (RAG-Vektorindex). Tools werden weiterhin **entdeckt, nie hartkodiert** — nur pro Agent verengt. Jeder liefert seine rohen MCP-Ergebnisse (vollständig, als JSON-String) als DataPart-Artefakt zurück, damit der Orchestrator Karten/Charts/Trace bauen kann.
- **`core/orchestrator.py`** ist jetzt ein dünner **A2A-Client-Adapter** zum Orchestrator-Agenten und erhält den öffentlichen Vertrag `run()/refresh_tools()` — FastAPI-SSE und Telegram-Bridge bleiben unverändert.
- **Registry & Betrieb**: `core/config.A2A_AGENTS` (name → URL, env-überschreibbar wie `RECOVERY_A2A_URL=…`); jeder Agent ist ein eigener Prozess/Port/Container mit Agent Card unter `/.well-known/agent-card.json`. Modell-Override für die Agentenschicht: `AGENT_LLM_MODEL` (empfohlen `kit.gpt-4.1`; `glm-4.7` ist für die Mehrfach-Calls unzuverlässig). Agenten laufen **non-streaming** (`ainvoke`); Fortschritt kommt als A2A-Status-Update, nicht als Token-Stream.

Datenpfad weiterhin: **Agent → `ToolHost` → MCP-Server**. Chat-Pfad: **React-UI → FastAPI → `FitDashOrchestrator` → (A2A) Orchestrator `:9000` → (A2A) Spezialisten `:9001`–`:9006` → `ToolHost` → MCP**.

---

## 2. Komponenten

```
        ┌────────────────────── Frontends ──────────────────────┐
        │  React-SPA (web/) → Node-BFF (server/)  ·  Telegram-   │
        │  Bridge  ·  Tests / CLI                                │
        └───────────────────────┬────────────────────────────────┘
                                │  HTTP  (api/ — FastAPI-Naht)
        ┌───────────────────────▼────────────────────────────────┐
        │  core/  — UI-framework-frei, vendor-neutral            │
        │                                                        │
        │  orchestrator.py       A2A-Client-Adapter (run/trace)  │
        │  orchestrator_agent.py LangGraph-Orchestrator  :9000   │
        │  llm.py                LLM-Naht (Provider/Config)      │
        │  host.py               ToolHost  list_tools/call_tool  │
        │  config.py             Registry: name → MCP-/A2A-URL   │
        └───────────────────────┬────────────────────────────────┘
                                │  A2A (Agent Cards, JSON-RPC)
        ┌───────────────────────▼────────────────────────────────┐
        │  agents/ — 6 Spezialisten  :9001–:9006                 │
        │  recovery · load · context · route · fitness · coach   │
        └───────────────────────┬────────────────────────────────┘
                                │  uniformer MCP-Client (Streamable HTTP)
        ┌───────────────────────┼────────────────────────────────┐
        ▼                       ▼                                ▼
  servers/*_mcp.py        servers/telegram_mcp.py        externe MCP-Server
  (8 native Server)       (Proxy auf stdio-Server)       (Nutzer, gleich behandelt)
```

### `core/config.py` — die Registry
Eine deklarative Tabelle `name → URL`. Eigene und externe Server haben dieselbe Form; einziger Unterschied ist die URL. **Einen Server hinzufügen = eine Zeile** (oder eine Env-Variable). Jede URL ist per Env überschreibbar: `WEATHER_MCP_URL=http://weather-mcp:8101/mcp` (z. B. im docker-compose, wo der Servicename der Host ist).

```python
# core/config.py — MCP_PORTS ist die einzige Zahlenquelle; MCP_SERVERS leitet ab.
MCP_PORTS: dict[str, int] = {
    "weather": 8101, "routes": 8102, "strava": 8103, "garmin": 8104,
    "calendar": 8105, "telegram": 8106, "flythrough": 8107,
    "google_maps": 8108, "athlete": 8109,
}

MCP_SERVERS = {name: _url(name) for name in MCP_PORTS}   # _url liest <NAME>_MCP_URL
```

Dieselbe Tabelle speist `ports.sh`, `web/vite.config.ts` und `docker-compose.yml` —
alle drei lesen sie live über `scripts/export_ports.py`, statt Ports zu kopieren.

### `core/host.py` — `ToolHost`
Der **einzige** MCP-Client der App. Eine uniforme Code-Bahn für jedes Tool, egal welcher Server es liefert:

- `alist_tools()` / `list_tools()` — entdeckt jedes Tool jedes **erreichbaren** Servers im OpenAI-Tool-Format; Namen werden `server__tool` genamespaced. Ein Server, der nicht läuft / nicht autorisiert / unerreichbar ist, wird **übersprungen** — er bricht nie die anderen.
- `acall_tool(name, args)` / `call_tool(...)` — zerlegt `server__tool`, routet an den Server, gibt Text/JSON zurück; Tool-Fehler werden als `{"error": ...}` zurückgegeben, nicht als Exception.
- **Async-Kern, Sync-Fassade:** Die echte Implementierung ist async (`mcp.client`); `_run()` überbrückt sie für synchrone Aufrufer (Agenten, Bridge, Tests) (frischer Event-Loop pro Aufruf, auch in ThreadPool-Workern sicher).
- **Auth pro Server:** `headers={"calendar": {"Authorization": "Bearer …"}}` wird als Connection-Header übergeben — getrennt von der Tool-Deklaration, nie im Tool-Kontext. `default_host` nutzt die globalen Server; **Pro-Nutzer-Hosts** werden explizit mit zusätzlichen Servern + Headern konstruiert.

### `core/llm.py` — die LLM-Naht
Eine Stelle, die den Chat-Client baut und das Modell auflöst — beides aus Env (`OPENAI_API_KEY`, `OPENAI_BASE_URL`, `AGENT_MODEL`). Heute ein OpenAI-kompatibler Endpoint (KIT-Gateway). Importiert bewusst **kein** UI-Framework, damit der Kern standalone läuft (CLI, API, Tests, separater Service).

### `core/orchestrator.py` — der tool-agnostische Loop
**Ein** nativer Tool-Use-Loop ersetzt die alte 4-Agenten-Pipeline:

1. Tools einmal entdecken (gecacht), System-Prompt + (gekürzte) Historie + User-Input aufbauen.
2. Bis zu `MAX_ROUNDS` (6): Modell rufen mit `tools=…, tool_choice="auto"`. Liefert es Tool-Calls, werden alle über `ToolHost.call_tool()` ausgeführt und die Ergebnisse zurückgespeist; liefert es keine, ist das die Antwort.
3. Große Arrays (`points`, `waypoints`, `segments`, …) werden vor der Rückgabe ans Modell kompaktiert (`_clip`), damit der Kontext nicht zuläuft — die vollen Daten rendert das UI separat.
4. Es wird ein `trace` für das Debug-Panel der React-UI und den Karten-Renderer gebaut. `ROUTE_TOOLS` dient **ausschließlich** dem UI (welches Ergebnis als Karte gezeichnet wird) — es steuert **nicht** die Tool-Auswahl.

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

Im Normalfall startet **ein** Skript den gesamten Stack — einzelne Prozesse zu starten
ist nur zum Debuggen nötig:

```bash
./run.sh                 # alles: MLflow, 8 MCP-Server, 7 Agenten, FastAPI, Bridge, Vite
./run.sh status          # was läuft gerade
./run.sh stop            # alles beenden

# Einzeln (nur zum Debuggen):
python -m servers.weather_mcp      # :8101
python -m servers.athlete_mcp      # :8109

# Vollständig containerisiert (alle 17 Services, App auf :3000):
./docker-up.sh up --build
```

Auf dem Host läuft `ToolHost` neben den Servern und erreicht sie über `localhost`.
In Compose adressieren sich die Services über ihre **Namen** — deshalb bindet dort
jeder Server `0.0.0.0` und jede `*_MCP_URL` / `*_A2A_URL` wird gesetzt. Genau dafür
ist die URL-basierte Registry in `core/config.py` da: derselbe Code, andere Adressen,
keine Codeänderung.

| Server | Port | Backend | Auth |
|---|---|---|---|
| `weather` | 8101 | Open-Meteo | keine (kostenlos) |
| `routes` | 8102 | OpenRouteService + Overpass | `ORS_API_KEY` |
| `strava` | 8103 | Strava v3 REST API | OAuth2 (`.tokens/strava.json`) |
| `garmin` | 8104 | Garmin Connect (garminconnect) | Session-Token (`.tokens/garmin_tokens.json`) |
| `calendar` | 8105 | Google Calendar | Bearer (Header oder `.tokens/google.json`) |
| `telegram` | 8106 | Proxy auf `chigwell/telegram-mcp` (stdio) | `TELEGRAM_*` (Session-String) |
| `flythrough` | 8107 | eigen (GPS-Track → 3D-Flug) | keine |
| `google_maps` | 8108 | Places (New) / Geocoding v4 / Routes API | `GOOGLE_MAPS_API_KEY` (Demo-Key reicht) |
| `athlete` | 8109 | eigen — strukturierter Athleten-Store + Trainingsmathematik | Nutzer via `X-FitDash-User`-Header |

---

## 6. Status & nächste Schritte

**Umgesetzt:** uniformer MCP-Host (`ToolHost`), tool-agnostischer Kern, **acht** native FastMCP-Server (weather/routes/strava/garmin/calendar/flythrough/google_maps/athlete) plus telegram als Proxy auf einen externen stdio-Server, die A2A-Agentenschicht (Orchestrator + sechs Spezialisten), vendor-neutrale LLM-Naht, Tool-Namespacing, Observability via MLflow (`core/tracing.py`), Mandanten-/Auth-Schicht (`api/auth.py`: E-Mail+OTP, signierte Tokens, Pro-Nutzer-State unter `data/user_memory/<slug>/`), vollständige Legacy-Entfernung (Registry, BaseMCPServer, agents-Pipeline, Streamlit-Frontend).

**Noch offen:**
- **Token-Vault:** Identität ist pro Nutzer, die Upstream-Tokens (`.tokens/`) sind es noch nicht — Strava/Garmin sind heute *ein* geteiltes Konto pro Deployment.
- **Sandboxing/Allowlist/Egress-Kontrolle** für nutzer-hinzugefügte MCP-Server; Tool-Output konsequent als untrusted behandeln.
- Einheitliches Logging (heute teils `print`, teils `logging`); Contract-Tests an den Nähten über `tests/unit/` hinaus — dort liegen heute die Agent-Trace-Kontrakte, die deterministische Trainingsmathematik und der Routen-Export, alle offline lauffähig via `pytest`.
