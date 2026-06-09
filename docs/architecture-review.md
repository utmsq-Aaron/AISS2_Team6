# FitDash — Architektur-Review & Umbau-Empfehlung

**Reviewer-Perspektive:** extern, kritisch
**Bewertungsmaßstab (Zielbild):** öffentliche Website · mehrere Nutzer · nutzer-erweiterbare MCP-Server · domänenübergreifender Lifestyle-Assistent (Training × Wetter × Kalender …)
**Bewerteter Stand:** Branch `main` (Stand *vor* dem Umbau)

> **Status-Hinweis:** Dieses Review bewertet die **alte** `main`-Architektur und begründet den Umbau. Auf der Branch `feature/mcp-standard-architecture` sind inzwischen umgesetzt: **uniformer MCP-Host** (`core/host.ToolHost`), **tool-agnostischer Kern** (`core/orchestrator`, native Tool-Use-Loop), **eigene Server als native FastMCP-Services** (weather/routes/calendar) und die **vendor-neutrale LLM-Naht** (`core/llm`). **Noch offen:** Mandanten-/Sicherheitsschicht (Backend-Auth, Pro-Nutzer-Vault), `strava`/`garmin` als FastMCP, Frontend-Split. Dieses Dokument bleibt als „Warum"/Vorher-Referenz erhalten.

---

## 1. Management Summary

| Frage | Antwort |
|---|---|
| Ist `main` als Prototyp/Lernstand brauchbar? | **Ja.** |
| Ist `main` ein tragfähiges Fundament für das Zielbild? | **Nein — drei grundlegende Umbauten nötig.** |
| Größtes Risiko | **Kein Mandanten-/Sicherheitsmodell** — heute nicht freigabefähig für eine öffentliche, nutzer-erweiterbare Plattform. |
| Häufiges Missverständnis | Das Problem ist **nicht** „if/else-Dispatching" (das nutzt auf `main` korrekt die Registry-Schleife), sondern **hardcodiertes Domänenwissen** + **fehlende Mandantenfähigkeit**. |

**Drei Umbau-Achsen:**
1. **Tool-agnostischer Kern** — kein Code darf Tools beim Namen kennen.
2. **Uniformer MCP-Host** — eigene = externe Server, ein Aufrufpfad.
3. **Mandanten- + Sicherheitsmodell** — Pro-Nutzer-Identität, Isolation, Sandboxing fremder Server.

---

## 2. Was gut ist (behalten)

- **Registry statt Hand-Verdrahtung** (`servers/registry.py`): `dispatch()` ist eine Namens-Schleife, kein if/else. Richtiges Muster — **nicht** löschen (wie es der `strava`-Branch tat).
- **MCP-förmige Tool-Specs** (`name/description/inputSchema`) + `to_openai_tools()` → anbieter-neutral nutzbar.
- **FastMCP** wird in den Agenten bereits verwendet → halber Weg zum echten MCP-Host.
- **Defensiver Orchestrator** (try/except + Timeouts pro Agent).

---

## 3. Befunde nach Schweregrad

### 🔴 KRITISCH — blockiert öffentlichen Multi-User-Betrieb

**C-1 · Keine Mandantentrennung (Identität/State).**
Tokens liegen als **einzelne, geteilte Dateien** (`.tokens/strava.json`, `.tokens/google.json`). Das ist *ein* Nutzer auf der Platte. Mehrere Website-Nutzer ⇒ Datenvermischung.
→ *Pro-Nutzer-Identität + Pro-Nutzer-Token/Secret-Vault. State niemals global auf Dateiebene.*

**C-2 · Keine Session-Isolation.**
Streamlit-Single-Prozess + `@st.cache_resource`-Singletons ⇒ Server-Instanzen werden **über alle Sessions geteilt**. Nutzer A bekäme Nutzer Bs Instanz/Daten.
→ *Kern als mandantenfähiger Service vom UI trennen; State pro Request/Nutzer.*

**C-3 · Keine Sicherheit für nutzer-hinzugefügte MCP-Server.**
Fremde Server = große Angriffsfläche: SSRF, bei stdio-Transport **Befehlsausführung**, Daten-Exfiltration, **Prompt-Injection** über Tool-*Beschreibungen* und Tool-*Outputs* (fließen ungefiltert in den LLM-Kontext). `main` hat **keine** Allowlist, **kein** Sandboxing, **keine** Egress-Kontrolle, behandelt Tool-Output **nicht** als untrusted.
→ *Genehmigungs-/Allowlist-Flow, Sandbox + Egress-Limit, Tool-Output grundsätzlich als untrusted behandeln, Injection-Abwehr.*

**C-4 · Secrets-Handling.**
OAuth-Client-Secrets in Env, Tokens im Klartext auf der Platte. Für Multi-User fehlt ein Pro-Nutzer-Secret-Store. (Siehe auch Kalender-Scope: über-privilegiert mit Schreib-Scope für read-only Features.)
→ *Verschlüsselter Pro-Nutzer-Vault; minimale OAuth-Scopes.*

### 🟠 HOCH — blockiert nutzer-erweiterbare Tools

**H-1 · Feste 4-Agenten-Pipeline.** (`ui/orchestrator.py`)
„Fetch → Viz∥Flyover → Chat" unterstellt: jede Anfrage = Fitnessdaten holen → visualisieren. Kalender-/Finanz-/Smart-Home-Tools passen nicht in diese Form; domänenübergreifendes Verketten ist nicht vorgesehen.
→ *Tool-agnostischer **Tool-Use-Loop** (Modell verkettet beliebige Tools selbst). Existiert bereits als Entwurf auf `feature/tool-use-loop`.*

**H-2 · Per-Tool-Code: `_extract_key_findings`.** (`servers/agents/fetching.py`, ~170 Z.)
Eine `elif tool == "get_garmin_sleep" …`-Kette. **Bricht bei jedem nutzer-hinzugefügten Tool** (unbekannt ⇒ keine findings).
→ *Generische, schema-getriebene Zusammenfassung — kein Code, der Tools beim Namen nennt.*

**H-3 · Per-Tool-Rendering.** (`ui/viz.py`)
Rendert nur bekannte Tool-Outputs. Nutzer-Tools rendern **nichts**.
→ *Schema-/typgetriebenes Rendering (z. B. Tabelle/Zeitreihe/Karte je nach Output-Form), generischer Fallback.*

**H-4 · Domänen-Sonderpfade.** Weather-Fast-Path (Keyword→feste Tools) und Flythrough-Routing (Spezialpfad für *ein* Feature) im Planner/Orchestrator.
→ *Entfernen; alles über den generischen Loop.*

**H-5 · Statische Server-Liste.** (`registry._setup()`)
Server sind im Code fest verdrahtet — Nutzer können zur Laufzeit nichts hinzufügen.
→ *Pro-Nutzer-Connection-Registry, zur Laufzeit befüllbar (Config/DB statt Code).*

### 🟡 MITTEL — Uniformität & Sauberkeit

**M-1 · Zwei Server-Stile.** `strava`/`garmin` = Legacy-Klassen, `routes`/`weather` = `BaseMCPServer`. Unterschiedliche Behandlung im Aufrufpfad.
→ *Eine Schnittstelle für alle; eigen und extern ununterscheidbar.*

**M-2 · Mehrere Aufruf-Flächen.** `shared.call_tool`, `registry.dispatch`, in `api.py` ein eigenes `_find_server_key`. Dieselbe Aufgabe an drei Stellen.
→ *Eine `ToolHost`-Fassade (`list_tools` / `call_tool`) für Agenten, API und UI.*

**M-3 · Streamlit-Kopplung des Kerns.** Agenten greifen über `ui.shared` in Streamlit. Kern nicht standalone/testbar/serverfähig.
→ *`core/`-Package, vendor-neutrale LLM-Naht, Streamlit nur als ein Frontend.*

**M-4 · Fehlende Tool-Namespaces.** Tool-Namen global flach (`get_activities`). Bei vielen (v. a. externen) Servern → Kollisionen.
→ *Namespacing `server.tool`.*

### 🟢 NIEDRIG — Qualität

- **L-1 · `except Exception: pass`** an vielen Stellen → verschluckte Fehler, schweres Debugging. → `logging`.
- **L-2 · Kaum Tests** (nur `test_routes.py`) → Refactor ohne Netz. → Contract-Tests an den Nähten.
- **L-3 · Progress-Callback dopplet** Status-Einträge (kosmetisch).

---

## 4. Zielarchitektur

```
                    ┌───────────── Frontends ─────────────┐
                    │  Streamlit-UI   ·   Web-Client/API   │
                    └───────────────────┬──────────────────┘
                                        │  (eine Fassade)
                    ┌───────────────────▼──────────────────┐
                    │      CORE (mandantenfähig, vendor-neutral)
                    │   • Tool-Use-Loop  (tool-AGNOSTISCH)  │
                    │   • LLM-Naht       (Provider per Config)
                    │   • ToolHost.call_tool / list_tools   │
                    └───────────────────┬──────────────────┘
                                        │  uniformer MCP-Client
        ┌───────────────────────────────┼───────────────────────────────┐
        ▼                                ▼                                ▼
  eigene MCP-Server            externe MCP-Server (Nutzer)        Pro-Nutzer-Registry
  (stdio / in-proc, schnell)   (HTTP + OAuth, gesandboxt)         + Token/Secret-Vault
```

**Prinzipien:** alle Server gleich (eigen = extern) · kein Code kennt Tool-Namen · State & Secrets pro Nutzer · Tool-Output = untrusted.

---

## 5. Priorisierter Umbauplan

> Regel: **erweitern, nie löschen** (Registry bleibt und wächst). Jede Phase hält den lauffähigen `inproc`-Pfad.

**Phase 0 — Sicherheitsnetz (sofort)**
- Contract-Tests an den Nähten: `call_tool`, `orchestrator.run`, LLM-Client. *(L-2)*

**Phase 1 — Tool-agnostischer Kern** *(H-1…H-4)*
- Tool-Use-Loop (von `feature/tool-use-loop`) als Standardpfad, vertragstreu.
- `_extract_key_findings` → generisch; Per-Tool-Rendering → schema-getrieben; Domänen-Sonderpfade raus.
- **Wirkung:** beliebige (auch unbekannte) Tools funktionieren end-to-end.

**Phase 2 — Entkopplung + eine Fassade** *(M-2, M-3)*
- `core/`-Package, vendor-neutrale LLM-Naht (kein `@st.cache_resource` im Kern).
- `core/host.py` (`ToolHost`) als **einzige** `list_tools`/`call_tool`-Fläche für Agenten, API, UI.

**Phase 3 — Uniformer MCP-Host** *(M-1, M-4, H-5)*
- `ServerEntry` → Verbindung (`transport=inproc|stdio|http`, endpoint, auth). Bestehende Server als `inproc` umhüllt.
- Tool-Namespacing. Externe Server via HTTP einhängbar — gleich behandelt wie eigene.

**Phase 4 — Mandanten- + Sicherheitsmodell** *(C-1…C-4, H-5)* — **Pflicht vor öffentlichem Launch**
- Pro-Nutzer-Identität; verschlüsselter Pro-Nutzer-Token/Secret-Vault; State pro Request statt global.
- Kern als eigenständiger Service vom Streamlit-Monolithen trennen (Session-Isolation).
- Sandboxing/Allowlist/Egress-Limit für nutzer-hinzugefügte Server; Tool-Output als untrusted; Prompt-Injection-Abwehr; minimale OAuth-Scopes.

**Phase 5 — Härtung** *(L-1, L-3)*
- `logging` statt `except: pass`; Observability; Progress-Dopplung fixen.

| Phase | Schwerpunkt | Blocker für … |
|---|---|---|
| 0 | Tests | sicheres Refactoring |
| 1 | tool-agnostischer Kern | nutzer-erweiterbare Tools |
| 2 | Entkopplung + Fassade | sauberer Aufruf |
| 3 | uniformer MCP-Host | eigene = externe Server |
| 4 | Mandanten + Sicherheit | **öffentlicher Launch** |
| 5 | Härtung | Betrieb |

---

## 6. Entscheidung: Umbau vs. Neuaufsatz

`main` enthält die richtigen **Bausteine** (Registry, MCP-Specs, FastMCP) — ein **inkrementeller Umbau** entlang Phase 0→5 ist machbar und risikoärmer als ein Neuaufsatz. **Aber** Phase 4 (Mandanten/Sicherheit) ist kein Nachgedanke, sondern die Bedingung dafür, dass das Produkt überhaupt öffentlich gehen darf — entsprechend früh einplanen, nicht ans Ende schieben.

**Empfehlung:** Inkrementeller Umbau, Reihenfolge 0 → 1 → 2 → 3 → **4** → 5. Phase 4 nicht als „später" behandeln — sie definiert, ob aus dem Prototyp ein Produkt werden kann.
