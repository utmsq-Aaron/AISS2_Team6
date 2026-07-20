# Trainingsregeln — faktenbasierte Grundlage der Coach-/Athlete-Logik

Dieses Dokument ist die **Single Source of Truth** für alle Trainings-Berechnungen
und -Strukturen in `servers/athlete_mcp.py` und der Coach-Prompt-Logik. Jede Regel
ist gegen die ins Fitness-RAG aufgenommenen **deutschen Fachbücher** belegt (Buch +
Seite + Originalzitat). Ziel (Entscheidung vom 2026-07-20): **Alles basiert auf
diesen Werken, nicht auf angloamerikanischen Open-Source-Quellen** (Riegel/Friel/
Daniels wurden verworfen — sie stehen nicht in unserem Korpus).

Quellen-Kurzform (siehe `data/fitness_library/QUELLEN.txt`):
- **Ferrauti** = Ferrauti (Hrsg.), *Trainingswissenschaft für die Sportpraxis*, Springer (ISBN 978-3-662-69524-1)
- **Güllich** = Güllich/Krüger (Hrsg.), *Handbuch Sport und Sportwissenschaft*, Springer (ISBN 978-3-662-53410-6)
- **König** = König/Carlsohn (Hrsg.), *Praxis der Sporternährung*, Springer (ISBN 978-3-662-68974-5)
- **Dransmann** = Dransmann, *HIIT vs. extensive Dauermethode*, Springer (ISBN 978-3-658-29154-9)
- **Engel** = Engel, *Physiologische Reaktionen auf HIIT …* (Dissertation)

---

## 1. Maximale Herzfrequenz (HFmax)

**Regel:** HFmax ist individuell, genetisch festgelegt, nicht trainierbar, sinkt mit
dem Alter. **Messen** ist immer besser als schätzen. Wenn geschätzt werden muss:
**HFmax = 208 − (0,7 × Alter)** — nicht die ältere Formel „220 − Alter".

> Güllich, S.771: „Anhand der Formel: 208 – (0,7 × Lebensalter in Jahren) (Tanaka et al.
> 2001) lässt sich im Vergleich zur älteren Formel 220-Lebensalter recht gut die maximale
> Herzfrequenz abschätzen. Da die maximale Herzfrequenz individuell ist, wird die
> Belastungsintensität zumeist anhand von Prozent der maximalen Herzfrequenz beschrieben."

> Güllich, S.771: „Die maximale Herzfrequenz ist individuell und genetisch festgelegt.
> Sie ist höchst reliabel und durch Training nicht beeinflussbar."

**Code-Konsequenz:** `compute_zones` verlangt weiterhin echte `max_hr`. Nur wenn keine
vorliegt, Fallback `208 − 0.7·age`. **Nie** `220 − age`.

---

## 2. Intensitätssteuerung & Trainingsbereiche (Zonen)

**Gewählte Basis (Entscheidung 2026-07-20): %HFmax — KEIN Laktat/v4.** Da wir aus
Garmin/Strava keine Laktatwerte messen können, steuern wir Intensität über **% der
maximalen Herzfrequenz** (Garmin liefert HF + Ruhe-HF). Zonen-Nomenklatur: das deutsche
Schema **ReKom / GA 1 / GA 2 / WSA** (Ferrauti S.442ff.).

> Ferrauti, S.442: „Das Ausdauertraining wird … in vier Trainingsbereiche unterteilt:
> Regeneration/Kompensation (ReKom), Grundlagenausdauer 1 (GA 1), Grundlagenausdauer 2 (GA 2)
> und wettkampfspezifisches Ausdauertraining (WSA). Synonym … wird auch von Zonen … gesprochen."

### 2a. HF-Zonen über %HFmax bzw. %HFR/Karvonen (Ferrauti S.459) — PRIMÄR

| Bereich | %HFmax | %HFR (Karvonen) | Reizcharakter |
|---|---|---|---|
| **ReKom** | **50–60 %** | 35–50 % | Regeneration/Kompensation |
| **GA 1** | **60–80 %** | 50–70 % | aerobe Grundlagenausdauer (größter Umfang) |
| **GA 2** | **80–90 %** | 70–85 % | aerob-anaerober Entwicklungsbereich |
| **WSA** | **90–100 %** | 85–100 % | wettkampfspezifisch/anaerob |

> Ferrauti, S.459: „ReKom 50–60 % von HFmax bzw. 35–50 % HFR … GA 1 60–80 % von HFmax bzw.
> 50–70 % HFR … GA 2 80–90 % von HFmax bzw. 70–85 % HFR … WSA 90–100 % von HFmax bzw.
> 85–100 % HFR". Und: „Insbesondere bei niedriger oder moderater Intensität verspricht die
> Herzfrequenzreserve gegenüber der Orientierung an der maximalen Herzfrequenz Vorteile."

**Karvonen/HFR:** Ziel-HF = Ruhe-HF + %HFR × (HFmax − Ruhe-HF). Wird ausgegeben, sobald eine
Ruhe-HF vorliegt (Garmin) — laut Buch bei ReKom/GA1 präziser.

### 2b. Pace-Zonen aus der Renn-/Zielleistung (Ferrauti S.462, Tab. 7.7, Joch 2004)

Ohne Laktat werden Tempobereiche „über eine prozentuale Berechnung anhand der
**10-km-Bestleistung**" bzw. der angestrebten Leistung abgeleitet. Beispiel Tab. 7.7 für
ein 10-km-Ziel von 50 min (= 5:00/km):

| Bereich | Tempo [min/km] | ≈ Faktor × 10-km-Renntempo |
|---|---|---|
| ReKom | ~07:09 | ~1,43 |
| GA 1 | ~05:53–06:40 | ~1,18–1,33 |
| GA 2 | ~05:16–05:33 | ~1,05–1,11 |
| WSA | ~04:46–05:00 | ~0,95–1,00 |

> Ferrauti, S.462: „Dies erfolgt beim Laufen … über eine prozentuale Berechnung anhand der
> 10-km-Bestleistung (Hottenrott & Zülch, 1998; Joch, 2004) oder der Angabe von
> Trainingsbereichen je nach angestrebter Leistung (Steffny, 2004; Tab. 7.7)."

**Code-Konsequenz:** `_hr_zones` von Friel-%LTHR auf **%HFmax + Karvonen (2a)** umstellen
(Labels ReKom/GA1/GA2/WSA). `_pace_zones` statt Daniels-VDOT als **Faktor × Renntempo (2b)**
aus einem realen 10-km-nahen Wettkampf/der Zielleistung. Kein Laktat/v4.

---

## 3. Wettkampfprognose / „bin ich auf Kurs?"

**Regel:** Die deutsche Sportwissenschaft prognostiziert Leistung **nicht** über
Distanz-Extrapolation (Riegel), sondern **datenbasiert über Leistungsdiagnostik**: aus
der individuellen Schwellengeschwindigkeit (v4) bzw. Dauerleistungsgrenze wird das
realistische Wettkampftempo abgeleitet und gegen die Zielzeit gestellt.

> Ferrauti, S.50: „Dies kann beim Ausdauertraining eines Freizeitläufers in der
> Vorbereitung auf einen Marathon die Ableitung seiner Trainingsgeschwindigkeit von den
> Ergebnissen einer zuvor durchgeführten Leistungsdiagnostik bedeuten."

**Code-Konsequenz:** `_riegel` entfernen. „on_track" wird nur bestimmt, wenn ein **realer
Benchmark-Wettkampf nahe der Zieldistanz** vorliegt: Vergleich des tatsächlichen Renntempos
mit dem für die Zielzeit nötigen Tempo. Fehlt ein vergleichbarer Wettkampf, gibt der Coach
ehrlich zurück, dass für eine Prognose eine Leistungsdiagnostik / ein Benchmark-Lauf nötig
ist (Buch: messen statt extrapolieren) — **keine** Distanz-Extrapolation (Riegel/`d^1.06`).

---

## 4. Trainingsplan-Struktur (6-Stufen-Programm)

**Regel:** Ein evidenzbasierter Plan entsteht in 6 Stufen (Ferrauti Tab. 1.2, S.39):

1. **Trainingsziel** — SMART (spezifisch, messbar, achievable/reasonable, time-bound),
   z. B. „Marathon unter 3 h".
2. **Literaturrecherche** — wiss. Evidenz + Lehrbücher (→ unser RAG).
3. **Trainingsrahmen** — Gesamtdauer, Anzahl+Dauer der Mikrozyklen, Anzahl Trainingseinheiten.
4. **Trainingsinhalte** — geeignete Inhalte/Testmethoden nach Evidenz.
5. **Langzeitplan/Progression** — Umfang, Belastungsverteilung + Progression im Längsschnitt.
6. **Kurzzeittrainingsplan** — Wochen/Einheiten, laufend an aktuelles Athleten-Monitoring angepasst.

> Ferrauti, Tab. 1.2 (S.39): „1. Trainingsziel … (z. B. Marathon unter 3 h) … 3. Trainingsrahmen:
> Festlegung von konkreten Zeitabschnitten (Gesamtdauer, Anzahl und Dauer der Mikrozyklen,
> Anzahl der Trainingseinheiten) … 5. Langzeitplan/Progression … 6. Kurzzeittrainingsplan:
> Planung von Trainingswochen und Trainingseinheiten … (z. B. Athleten-Monitoring)."

**Code-Konsequenz:** `set_race_goal` = Stufe 1, `scaffold_plan` = Stufen 3+5, `save_plan` +
Coach-Workout-Auswahl = Stufen 4+6. Kommentare in `athlete_mcp.py` auf dieses Schema referenzieren.

---

## 5. Periodisierung & Zyklen

**Regel:** Gesamtbelastung wird über Makro- (1+ Monate), Meso- (mehrere Wochen) und
Mikrozyklen (~1 Woche) strukturiert. Klassisch drei Makrozyklen: **Vorbereitungs-,
Wettkampf-, Übergangsphase** (Peaking). Grund: „Topform" ist instabil; konstant hohe
Belastung ist nicht tolerierbar (Matwejew 1972).

> Ferrauti, S.69–70: „… drei Entwicklungsphasen bzw. Makrozyklen (Vorbereitungs-,
> Wettkampf- und Übergangsphase) zu durchlaufen. Diese werden wiederum in einzelne Zyklen
> kürzerer Dauer unterteilt … Makrozyklen über einen bis mehrere Monate … Mesozyklen über
> mehrere Wochen, Mikrozyklen gewöhnlich über eine Woche."

Für den Freizeitläufer existiert ein **einfaches Mesozyklus-Periodisierungsmodell**
mit den Stellgrößen Reizdauer, Belastungsintensität, Trainingshäufigkeit, Tapering —
Reihenfolge: erst **Häufigkeit → Dauer** (Umfang) steigern, dann **Intensität** im
Wettkampftempo, dann Taper.

> Ferrauti, S.188 (Abb. 3.65): „… zunächst den Trainingsumfang (erst Trainingshäufigkeit
> dann -dauer) steigern und erst anschließend intensivere Belastungen im avisierten
> Wettkampftempo absolvieren, bevor er in der Vorwettkampfwoche (Taper-Phase) die Belastung
> reduziert." (Einfaches Periodisierungsmodell (Mesozyklus) für den Freizeitläufer)

**Code-Konsequenz:** `_phase_split` (base/build/peak/taper) auf die belegte Reihenfolge
mappen: base ≈ Umfangsaufbau (Häufigkeit→Dauer), build/peak ≈ Intensität im Wettkampftempo,
taper ≈ Vorwettkampfphase.

---

## 6. Belastungssteigerung (Reizstufenregel) & Entlastung

**Regel (Reizstufenregel, Roux 1895):** Reize wirken nur in einem individuellen Fenster:
unterschwellig (wirkungslos) → schwach überschwellig (erhaltend) → **stark überschwellig
(entwickelnd)** → zu stark (schädigend). „Je mehr, desto besser" ist falsch. Belastung
muss **progressiv, aber allmählich** gesteigert werden.

> Ferrauti, S.81: „Auf Roux geht daher die … Reizstufenregel zurück, nach der zwischen
> unterschwelligen, schwach bzw. stark überschwelligen und zu starken Reizen unterschieden wird."
> Ferrauti, S.87: „… eine individuell optimale Reizsetzung … und unreflektierte Theorien nach
> dem Motto ‚Je mehr, desto besser!' [sind] langfristig wenig zielführend."
> Güllich, S.631: „Die (Kraft-)Trainingsreize müssen also mit zunehmendem Expertiseniveau
> systematisch gesteigert werden (Prinzip der progressiven Belastung)."

**Regel (Entlastung):** Belastungszyklen (z. B. 4 Wochen) enden mit einer
**Entlastungsphase** zur Ermöglichung von Regeneration und Adaptation.

> Ferrauti, S.295: „… Vorbereitungszyklus und einem anschließenden Intensivierungszyklus von
> je vier Wochen Dauer. Die Reizsetzung erfolgt progressiv … Entlastungsphasen sind jeweils am
> Ende der Zyklen zur Ermöglichung von Regeneration und Adaptation eingeplant."

**Code-Konsequenz:** Der Rampen-Cap (`MAX_WEEKLY_RAMP`) und der **4-Wochen-Cutback**
(`CUTBACK_EVERY = 4`, Entlastung am Zyklusende) sind **buchgestützt** — als „allmähliche,
progressive Steigerung mit Entlastung am Zyklusende" dokumentieren (Ferrauti S.295). Die exakte
Prozentzahl des Caps ist eine konservative Konvention (kein exakter Buchwert) und bleibt als
**weiche Guardrail** deklariert, nicht als „Gesetz".

---

## 7. Tapering (unmittelbare Wettkampfvorbereitung)

**Regel:** In der letzten Phase vor dem Wettkampf **BelastungsUMFANG deutlich reduzieren,
Intensität aber erhalten**, damit Ermüdung abklingt und die Fitness gehalten wird. Dauer:
**bis zu ~2 Wochen (2–15 Tage)**. Richtiger Zeitpunkt → „überschießende" Formsteigerung;
zu frühe Reduktion verschenkt Leistung.

> Ferrauti, S.117: „… schließt sich … die Taper-Phase an (Mujika & Padilla, 2003). In dieser
> Phase wird über einen längeren Zeitraum von bis zu zwei Wochen bei deutlich reduziertem
> Belastungsumfang durch das Abklingen der trainingsbedingten [Ermüdung] …"
> Ferrauti, S.117 (Abb. 3.17): „1. Phase (2–15 Tage) Tapering".
> Ferrauti, S.82: „… Taper-Phase … in der nur noch wenige intensive Trainingsreize zum Erhalt
> der Fitness gesetzt werden und die Ermüdung entsprechend kontinuierlich abklingen kann."
> Ferrauti, S.91 (Abb. 2.28): „Wird das Overreaching zum richtigen Zeitpunkt durch eine
> Taperphase unterbrochen (B), kann eine überschießende Leistungssteigerung gegenüber einer zu
> frühzeitigen Reduktion der Trainingsreize (A) erreicht werden."

**Code-Konsequenz:** Taper = **1–2 Wochen**, **Umfang** deutlich runter (die bisherige
40–60-%-Volumenstufe passt zu „deutlich reduziert"), **Intensität erhalten**. Der bisherige
Taper-Guardrail-Check in `_validate_plan` bleibt, mit dieser Quelle belegt.

---

## 8. HIIT-Gestaltung (konkrete Protokolle)

**Regel:** Intensität hängt an der Intervalldauer — je kürzer, desto intensiver
(% der Maximalleistung/HFmax). Belegte Protokolle (Ferrauti S.58–59):

| Protokoll | Struktur | Intensität | B:P |
|---|---|---|---|
| Langintervall | **4 × 4 min** | 80–85 % Pmax | 2:1 |
| — | 7 × 2 min | ~80–85 % | 1:1 |
| Kurzintervall | 2 × 10 × 30 s | **90–95 %** | 1:2 |
| — | 3 × 9 × 15 s | 90–95 % | 1:3 |
| Sprint (IST/RST) | 4 × 6 × 5 s | **100 % (all-out)** | 1:6 |

> Ferrauti, S.58: „… bei den längeren HIIT-Protokollen über 2 bis 4 min unter der vorgegebenen
> Belastungsintensität (80–85 % der Maximalleistung) höher als bei einer kürzeren Belastungsdauer
> von 15–30 s, obwohl hier die Belastungsintensität auf 90–95 % der Maximalleistung angehoben
> wurde … Intervallsprinttraining mit nur 5 s Belastungsdauer, dafür aber mit 100 %
> Belastungsintensität („all-out")."

Ergänzend (Güllich): intensive Intervallmethode „all out", moderate Pause (2–4 min), einige
(10–12) Wiederholungen. Für Untrainierte längere Pausen (z. B. 90 s aktiv, Dransmann).

**Code-Konsequenz:** Der Coach (LLM) wählt HIIT-Workouts **aus diesen belegten Protokollen**
statt frei zu erfinden; Intensität als Zone/%HFmax gemäß Abschnitt 2. Die Protokolltabelle
gehört in den Coach-Prompt (bzw. wird über die RAG-Suche zitiert).

---

## 9. Monitoring & Individualisierung (Readiness — mit Vorsicht)

**Regel:** Trainingssteuerung soll tagesaktuell über **Monitoring** feinjustiert werden
(externe Belastung + interne Beanspruchung + Readiness). **Aber:** intransparente „Readiness-
Scores" mit unbekannten Formeln sind laut Ferrauti **hochproblematisch** — Kennzahlen müssen
definiert, nachvollziehbar und validierbar sein.

> Ferrauti, S.185 (3.5.4): „Das Monitoring … gilt mittlerweile als zentrale Schlüsselstelle im
> Prozess der Individualisierung von Trainingsmaßnahmen."
> Ferrauti, S.202: „Gleichermaßen bewerten wir die zahlreichen vermeintlich anwenderfreundlichen
> ‚Scores' für Ermüdung, Erholung oder ‚Readiness' als hochgradig problematisch. In den meisten
> Fällen fehlen Definitionen … bleiben die … Berechnungsformeln unbekannt. Damit ist eine externe
> und unabhängige Validierung solcher Score oft kaum möglich."

**Code-Konsequenz:** Das athlete-Design (jede Zahl nachrechenbar, Basis protokolliert) ist genau
richtig. Falls ein Readiness-/Monitoring-Signal ergänzt wird: **transparent und aus dokumentierten
Rohwerten** (z. B. Ruhe-HF-Trend), nie ein Blackbox-Score.

---

## 10. Superkompensation — kritisch einordnen

**Regel:** Das naive Fixed-Timing-Superkompensationsmodell gilt als didaktische Vereinfachung;
moderne Steuerung ist antagonistisch/komplex und monitoring-basiert.

> Ferrauti (Expertenmeinung, Kap. Regeneration): „Superkompensation ist die Erdscheibe der
> Trainingslehre!"
> Güllich, S.808 / S.796: Reizstufenregel + wellenförmige Belastungsverteilung (Matwejew); starre
> Anpassungsregeln haben „lediglich einen didaktischen Wert".

**Code-Konsequenz:** Keine harte Timing-Annahme („nächster Reiz exakt im Superkompensations-
fenster"). Steuerung über progressive Belastung + Entlastung + Monitoring (Abschnitte 5–7, 9).

---

## Mapping: aktuelle Code-Konstanten → Buchbeleg → Änderung

| `athlete_mcp.py` | bisher (Anglo) | Buch-Grundlage | Änderung |
|---|---|---|---|
| `_hr_zones` | Friel %LTHR | %HFmax (Ferrauti S.446) + v4 (S.200) | **umstellen** auf %HFmax/%v4 |
| `_pace_zones` | Daniels-VDOT-Vielfache | %v4-Geschwindigkeit (Ferrauti S.200) | **umstellen** auf %v4 |
| `_riegel` / Prognose | Riegel 1.06 | Leistungsdiagnostik v4 (Ferrauti S.50) | **ersetzen** durch v4-vs-Zieltempo |
| HFmax-Fallback | (keiner) | 208−0,7·Alter (Güllich S.771) | **ergänzen** |
| `MAX_WEEKLY_RAMP` | +8 % Konvention | progressive Belastung (Güllich S.631) | **belegen**, als weiche Guardrail deklarieren |
| `CUTBACK_EVERY=4` | Konvention | 4-Wochen-Zyklus + Entlastung (Ferrauti S.295) | **belegt** — Kommentar mit Quelle |
| `_phase_split` | base/build/peak/taper | Makro/Meso + Freizeitläufer-Modell (Ferrauti S.69, S.188) | **belegen**, Reihenfolge Häufigkeit→Dauer→Intensität→Taper |
| Taper-Check | ≤75 % Peak | 2–15 Tage, Umfang↓ Intensität erhalten (Ferrauti S.117) | **belegt** — Quelle ergänzen |
| HIIT-Workouts | frei erfunden | Protokolle Ferrauti S.58–59 | **in Coach-Prompt** verankern |
| Superkompensation-Annahme | implizit | kritisch (Ferrauti/Güllich) | Timing-Annahmen entfernen |
