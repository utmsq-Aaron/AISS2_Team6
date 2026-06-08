# FitDash — Architektur & Entwicklungsguide

## Übersicht

Das System ist in drei unabhängige Schichten aufgebaut.
Jede Schicht kennt nur die Schicht darunter — nie umgekehrt.

```
┌─────────────────────────────────────────────────────────┐
│  Schicht 3: UI                                          │
│  app.py, ui/*.py                                        │
│  Zeigt Daten an, nimmt Nutzereingaben entgegen          │
└────────────────────────┬────────────────────────────────┘
                         │ ruft auf
┌────────────────────────▼────────────────────────────────┐
│  Schicht 2: Agent / Orchestrator                        │
│  ui/orchestrator.py + servers/agents/                   │
│  Plant Datenabrufe, analysiert, formuliert Antworten    │
└────────────────────────┬────────────────────────────────┘
                         │ ruft auf
┌────────────────────────▼────────────────────────────────┐
│  Schicht 1: MCP-Server (Datenquellen)                   │
│  servers/*.py                                           │
│  Jeder Server = eine externe Datenquelle                │
└─────────────────────────────────────────────────────────┘
```

**Regel:** Ein MCP-Server weiß nichts vom Agenten oder der UI.
Der Agent weiß nichts von der UI.
Jede Schicht ist unabhängig testbar.

---

## Schicht 1: MCP-Server

### Was ist ein MCP-Server?

Ein MCP-Server ist eine Python-Klasse, die eine externe Datenquelle
(API, Datenbank, Service) in strukturierte **Tools** verwandelt.
Der Agent ruft diese Tools per Name + Argumente auf und bekommt JSON zurück.

### Warum diese Architektur?

**Ohne gemeinsame Basisklasse** muss jeder neue Server manuell in
`shared.py`, `orchestrator.py` und der UI eingetragen werden —
4 Dateien, 4 potenzielle Merge-Konflikte wenn 5 Leute gleichzeitig arbeiten.

**Mit Basisklasse + Registry** reichen 2 Schritte:
1. Datei in `servers/` erstellen
2. Eine Zeile in `servers/registry.py` eintragen

Der Agent und die UI entdecken den Server **automatisch**.

### Die Basisklasse

Datei: `servers/_base_server.py`

```python
class BaseMCPServer(ABC):

    @abstractmethod
    def list_tools(self) -> List[Dict]:
        """Gibt alle Tool-Definitionen zurück."""
        ...

    @abstractmethod
    async def call_tool(self, name: str, args: Dict) -> str:
        """Führt ein Tool aus, gibt JSON-String zurück. Darf nie raisen."""
        ...
```

Die Basisklasse stellt kostenlos bereit:
- `.tools` Property (gecacht, liest aus `list_tools()`)
- `._dispatch()` mit Namens-Validierung und Fehlerbehandlung
- `.to_openai_tools()` Konvertierung für den Agenten

### Neuen Server erstellen — Schritt für Schritt

**Schritt 1:** Datei kopieren

```bash
cp servers/_template.py servers/calendar.py
```

**Schritt 2:** Klasse implementieren

```python
# servers/calendar.py

from servers._base_server import BaseMCPServer

class CalendarMCPServer(BaseMCPServer):

    def list_tools(self) -> list:
        return [
            {
                "name": "get_events_today",
                "description": "Gibt alle Kalendertermine für heute zurück.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "calendar_id": {
                            "type": "string",
                            "description": "Google Calendar ID (default: primary)"
                        }
                    },
                    "required": [],
                }
            },
            # weitere Tools...
        ]

    async def call_tool(self, name: str, args: dict) -> str:
        if name == "get_events_today":
            return await self._get_events_today(args)
        # Unbekannte Tools müssen nicht behandelt werden —
        # die Basisklasse fängt das ab.

    async def _get_events_today(self, args: dict) -> str:
        import json
        # ... echte API-Logik ...
        return json.dumps({"events": [...]})
```

**Schritt 3:** In Registry eintragen

```python
# servers/registry.py — NUR DIESE EINE ZEILE ERGÄNZEN:

from servers.calendar import CalendarMCPServer
register("calendar", CalendarMCPServer,
         required_env=["GOOGLE_CALENDAR_CREDENTIALS"],
         description="Google Calendar: Termine, freie Slots, Planung")
```

**Fertig.** Der Agent sieht das neue Tool automatisch.

### Tool-Definition — Regeln

Die `description` eines Tools ist das Wichtigste — sie entscheidet,
ob der Agent das Tool aufruft oder nicht.

```python
# SCHLECHT — zu vage:
"description": "Gibt Routen zurück"

# GUT — erklärt wann und warum:
"description": (
    "Plant eine Laufroute von A nach B via OpenRouteService. "
    "Gibt Distanz, Dauer, Höhenprofil und GPS-Wegpunkte zurück. "
    "Verwende dieses Tool wenn der Nutzer eine konkrete Strecke "
    "zwischen zwei Punkten plant."
)
```

Für `inputSchema`: Jeden Parameter mit `description` versehen.
Der Agent liest diese um korrekte Werte einzusetzen.

```python
"inputSchema": {
    "type": "object",
    "properties": {
        "start_lat": {
            "type": "number",
            "description": "Startpunkt Breitengrad (z.B. 49.0130 für KIT Karlsruhe)"
        },
    },
    "required": ["start_lat"],  # Pflichtfelder klar definieren
}
```

### Fehlerbehandlung

`call_tool()` darf **nie** eine Exception werfen.
Fehler als JSON zurückgeben:

```python
async def call_tool(self, name: str, args: dict) -> str:
    try:
        # ... Logik ...
    except requests.HTTPError as e:
        return json.dumps({"error": f"API-Fehler: {e.response.status_code}"})
    except Exception as e:
        return json.dumps({"error": str(e)})
```

---

## Die Registry

Datei: `servers/registry.py`

Zentrale Registrierung aller MCP-Server. Verwaltet:
- Welche Server existieren
- Welche Umgebungsvariablen sie brauchen
- Ob ein Server verfügbar ist (alle env vars gesetzt?)
- Automatische Tool-Aggregation für den Agenten

```python
# Verfügbare Tools abfragen (wird vom Agenten genutzt):
from servers.registry import all_openai_tools
tools = all_openai_tools()   # alle Tools aller verfügbaren Server

# Tool-Call routen (wird von shared.py genutzt):
from servers.registry import dispatch
result = await dispatch("plan_route", {"start_lat": 49.0, ...})

# Konfigurations-Status (wird von validate_config genutzt):
from servers.registry import config_status
for entry in config_status():
    print(entry["key"], "verfügbar:", entry["available"])
```

Die Registry ist **rückwärtskompatibel** — alte Server ohne Basisklasse
(z.B. `SimpleMCPServer`, `GarminMCPServer`) funktionieren weiterhin.

---

## Schicht 2: Agent

Der Agent in `servers/agents/` und `ui/orchestrator.py` fragt die Registry
nach allen verfügbaren Tools und entscheidet dynamisch welche er aufruft.

**Wichtig für Server-Entwickler:**
Der Agent liest nur `name` und `description` aus den Tool-Definitionen.
Je besser die Beschreibung, desto intelligenter die Tool-Auswahl.

### Wie der Agent Tools entdeckt

```python
# ui/shared.py
def get_all_openai_tools():
    from servers.registry import all_openai_tools
    return all_openai_tools()   # automatisch alle registrierten Server
```

Kein manuelles Eintragen nötig.

---

## Schicht 3: UI

Die UI in `ui/` greift nie direkt auf MCP-Server zu.
Sie ruft `call_tool(name, args)` aus `ui/shared.py` — die Registry
entscheidet welcher Server antwortet.

```python
# ui/shared.py
def call_tool(name: str, args: dict) -> str:
    from servers.registry import dispatch
    return run_async(dispatch(name, args))
```

---

## Bestehende Server (Referenz)

| Server | Klasse | Env-Vars | Stil |
|--------|--------|----------|------|
| `servers/strava.py` | `SimpleMCPServer` | `CLIENT_ID`, `CLIENT_SECRET` | Legacy |
| `servers/garmin.py` | `GarminMCPServer` | `GARMIN_EMAIL` | Legacy |
| `servers/routes.py` | `RoutesMCPServer` | `ORS_API_KEY` | BaseMCPServer ✓ |
| `servers/weather.py` | `WeatherMCPServer` | — (Open-Meteo, kein Key) | BaseMCPServer ✓ |

Legacy-Server funktionieren vollständig — kein Umbau nötig.
Neue Server sollten `BaseMCPServer` verwenden.

### Weather-Server Tools

| Tool | Was es liefert |
|------|----------------|
| `get_current_weather` | Aktuelles Wetter per GPS-Koordinate (WMO-Code, Temp, Wind, Niederschlag) |
| `get_pollen_levels` | Pollen-Belastung (Gräser, Birke, Erle, Beifuß) — Skala 0–5 |
| `get_uv_index` | UV-Index mit WHO-Kategorie (Low / Moderate / High / Very High / Extreme) |

---

## Geplante Server (offen)

| Server | Datenquelle | Env-Vars | Wer |
|--------|-------------|----------|-----|
| `servers/calendar.py` | Google Calendar API | `GOOGLE_CALENDAR_CREDENTIALS` | offen |
| `servers/nutrition.py` | Cronometer / eigenes | `CRONOMETER_KEY` | offen |
| `servers/tasks.py` | Todoist / Notion | `TODOIST_API_KEY` | offen |

---

## Checkliste für jeden neuen Server

```
[ ] servers/meinserver.py erstellt
[ ] Erbt von BaseMCPServer
[ ] list_tools() gibt alle Tools mit guten descriptions zurück
[ ] call_tool() fängt alle Exceptions ab (kein raise)
[ ] In servers/registry.py eingetragen (required_env korrekt)
[ ] Standalone testbar: python servers/meinserver.py
[ ] ORS_API_KEY / eigene Keys in .env.example dokumentiert
```

---

## Testen eines neuen Servers

Jeden Server unabhängig testen — ohne Agent, ohne UI:

```python
# test_myserver.py
import asyncio
from servers.myserver import MyMCPServer

async def main():
    server = MyMCPServer()
    print("Tools:", [t["name"] for t in server.tools])
    result = await server._dispatch("my_tool", {"param": "wert"})
    print("Ergebnis:", result)

asyncio.run(main())
```

---

## Umgebungsvariablen

Alle Keys kommen aus `.env` (nie hardcoden).
Neue Keys in `.env.example` dokumentieren:

```bash
# .env.example
ORS_API_KEY=              # OpenRouteService — kostenlos auf openrouteservice.org
GOOGLE_CALENDAR_CREDENTIALS=  # Google Cloud Console → OAuth2
CRONOMETER_KEY=           # Cronometer API
```

---

## Zusammenfassung: Was jeder wissen muss

1. **Neue Datenquelle?** → `servers/meinserver.py` + eine Zeile `registry.py`
2. **Tool descriptions** sind das Gehirn des Agenten — sorgfältig formulieren
3. **Nie raisen** in `call_tool()` — immer `{"error": "..."}` zurückgeben
4. **Unabhängig testen** bevor der Agent dran ist
5. **Env-Vars** in `.env.example` eintragen damit alle davon wissen
