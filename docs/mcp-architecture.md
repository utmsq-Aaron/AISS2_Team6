# FitDash — MCP-Architektur (Zielbild, umgesetzt)

**Stand:** Branch `feature/mcp-standard-architecture`
**Zweck dieses Dokuments:** Die *neue*, standardisierte Architektur sauber beschreiben — wie sie heute im Code steht, warum sie dem Anthropic-/MCP-Standard folgt und wie sie sich um **externe MCP-Server** erweitern lässt.

> **Verhältnis zu den anderen Docs**
> - [`docs/architecture-review.md`](architecture-review.md) — das *Warum* (kritisches Review der alten `main`-Architektur, das den Umbau begründet).
> - [`ARCHITECTURE.md`](../ARCHITECTURE.md) — **Legacy.** Beschreibt das abgelöste `BaseMCPServer`+Registry-Muster (noch am FastAPI-`/chat` und an den Daten-Tabs).
> - **Dieses Dokument** — das *Was/Wie* der neuen Architektur. Für neue Server gilt dieses Dokument.

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
  servers/calendar_mcp.py    (eigene FastMCP-Services)     (Nutzer, gleich behandelt)
```

### `core/config.py` — die Registry
Eine deklarative Tabelle `name → URL`. Eigene und externe Server haben dieselbe Form; einziger Unterschied ist die URL. **Einen Server hinzufügen = eine Zeile** (oder eine Env-Variable). Jede URL ist per Env überschreibbar: `WEATHER_MCP_URL=http://weather-mcp:8101/mcp` (z. B. im docker-compose, wo der Servicename der Host ist).

```python
MCP_SERVERS = {
    "weather":  _url("weather",  8101),
    "routes":   _url("routes",   8102),
    "strava":   _url("strava",   8103),   # noch Legacy-Server, sobald als FastMCP migriert hier erreichbar
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
python -m servers.calendar_mcp    # :8105   (Google read-only)

# Oder containerisiert: ein Image, SERVER-Env wählt das Modul
docker compose up --build weather-mcp routes-mcp calendar-mcp
```

Die App (`ToolHost`) läuft auf dem Host und erreicht die Server über die veröffentlichten `localhost`-Ports. Um die App *innerhalb* von compose zu betreiben, die `*_MCP_URL` auf die Servicenamen zeigen und `allowed_hosts` der Server weiten (siehe Kommentare in [`docker-compose.yml`](../docker-compose.yml)).

| Server | Port | Backend | Auth |
|---|---|---|---|
| `weather` | 8101 | Open-Meteo | keine (kostenlos) |
| `routes` | 8102 | OpenRouteService + Overpass | `ORS_API_KEY` |
| `calendar` | 8105 | Google Calendar (read-only) | Bearer (Header oder Token-Datei) |
| `strava` / `garmin` | 8103 / 8104 | noch Legacy-Server | Token-Vault (offen) |

---

## 6. Status & nächste Schritte

**Umgesetzt:** uniformer MCP-Host (`ToolHost`), tool-agnostischer Kern (`orchestrator`), eigene Server als native FastMCP-Services (weather/routes/calendar), vendor-neutrale LLM-Naht, Tool-Namespacing, One-Host-Deployment.

**Noch offen** (Details in [`docs/architecture-review.md`](architecture-review.md) §5):
- `strava` / `garmin` von Legacy-`BaseMCPServer` auf native FastMCP migrieren.
- Mandanten-/Sicherheitsschicht (Pro-Nutzer-Identität, verschlüsselter Token-/Secret-Vault, Session-Isolation).
- Sandboxing/Allowlist/Egress-Kontrolle für nutzer-hinzugefügte Server; Tool-Output als untrusted behandeln.
- Frontend-Split: FastAPI-`/chat` und die Daten-Tabs hängen noch am Legacy-Pfad (`servers/registry.py`).
