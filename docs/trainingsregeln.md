# Training rules — the evidence base for the coach/athlete logic

This document is the **single source of truth** for every training calculation and
structure in `servers/athlete_mcp.py` and in the coach prompt logic. Every rule is
backed by the **German sport-science textbooks** that make up the fitness RAG corpus
(book + page + original quotation). The decision of 2026-07-20: **everything rests on
those works, not on Anglo-American open-source sources** — Riegel, Friel and Daniels
were discarded because they are not in our corpus.

> **On the quotations.** Every `>` block quotes its source **verbatim in German**. They
> are left untranslated on purpose: a translated quotation is no longer a quotation, and
> the same rule already applies to the bibliographic titles (see the note in
> `scripts/extract_literature_corpus.py`). The German zone names **ReKom / GA 1 / GA 2 /
> WSA** are likewise kept — they are the source's nomenclature *and* the literal dict
> keys in `servers/athlete_mcp.py`.

Short citation keys (see `data/fitness_library/SOURCES.txt`):
- **Ferrauti** = Ferrauti (ed.), *Trainingswissenschaft für die Sportpraxis*, Springer (ISBN 978-3-662-69524-1)
- **Güllich** = Güllich/Krüger (eds.), *Handbuch Sport und Sportwissenschaft*, Springer (ISBN 978-3-662-53410-6)
- **Dransmann** = Dransmann, *HIIT vs. extensive Dauermethode*, Springer (ISBN 978-3-658-29154-9)
- **Engel** = Engel, *Physiologische Reaktionen auf HIIT …* (dissertation)

---

## 1. Maximum heart rate (HRmax)

**Rule:** HRmax is individual, genetically determined, not trainable, and declines with
age. **Measuring** it always beats estimating. When it must be estimated:
**HRmax = 208 − (0.7 × age)** — not the older "220 − age" formula.

> Güllich, p. 771: „Anhand der Formel: 208 – (0,7 × Lebensalter in Jahren) (Tanaka et al.
> 2001) lässt sich im Vergleich zur älteren Formel 220-Lebensalter recht gut die maximale
> Herzfrequenz abschätzen. Da die maximale Herzfrequenz individuell ist, wird die
> Belastungsintensität zumeist anhand von Prozent der maximalen Herzfrequenz beschrieben."

> Güllich, p. 771: „Die maximale Herzfrequenz ist individuell und genetisch festgelegt.
> Sie ist höchst reliabel und durch Training nicht beeinflussbar."

**Consequence in code:** `compute_zones` still requires a real `max_hr`. Only when none is
available does it fall back to `208 − 0.7·age`. **Never** `220 − age`.

---

## 2. Intensity control and training zones

**Chosen basis (decision of 2026-07-20): %HRmax — NO lactate/v4.** Since we cannot measure
lactate from Garmin or Strava, intensity is steered by **% of maximum heart rate** (Garmin
supplies HR and resting HR). Zone nomenclature: the German scheme **ReKom / GA 1 / GA 2 /
WSA** (Ferrauti p. 442 ff.).

> Ferrauti, p. 442: „Das Ausdauertraining wird … in vier Trainingsbereiche unterteilt:
> Regeneration/Kompensation (ReKom), Grundlagenausdauer 1 (GA 1), Grundlagenausdauer 2 (GA 2)
> und wettkampfspezifisches Ausdauertraining (WSA). Synonym … wird auch von Zonen … gesprochen."

### 2a. HR zones via %HRmax and %HRR/Karvonen (Ferrauti p. 459) — PRIMARY

| Zone | %HRmax | %HRR (Karvonen) | Character of the stimulus |
|---|---|---|---|
| **ReKom** | **50–60 %** | 35–50 % | regeneration/compensation |
| **GA 1** | **60–80 %** | 50–70 % | aerobic base endurance (the largest share of volume) |
| **GA 2** | **80–90 %** | 70–85 % | the aerobic–anaerobic development range |
| **WSA** | **90–100 %** | 85–100 % | race-specific/anaerobic |

> Ferrauti, p. 459: „ReKom 50–60 % von HFmax bzw. 35–50 % HFR … GA 1 60–80 % von HFmax bzw.
> 50–70 % HFR … GA 2 80–90 % von HFmax bzw. 70–85 % HFR … WSA 90–100 % von HFmax bzw.
> 85–100 % HFR". And: „Insbesondere bei niedriger oder moderater Intensität verspricht die
> Herzfrequenzreserve gegenüber der Orientierung an der maximalen Herzfrequenz Vorteile."

**Karvonen/HRR:** target HR = resting HR + %HRR × (HRmax − resting HR). It is emitted as soon
as a resting HR is available (from Garmin) — per the book, more precise for ReKom and GA 1.

### 2b. Pace zones from race/target performance (Ferrauti p. 462, tab. 7.7, Joch 2004)

Without lactate, pace ranges are derived "as a percentage calculation from the **10 km personal
best**" or from the targeted performance. Example, tab. 7.7, for a 10 km goal of 50 min
(= 5:00/km):

| Zone | Pace [min/km] | ≈ factor × 10 km race pace |
|---|---|---|
| ReKom | ~07:09 | ~1.43 |
| GA 1 | ~05:53–06:40 | ~1.18–1.33 |
| GA 2 | ~05:16–05:33 | ~1.05–1.11 |
| WSA | ~04:46–05:00 | ~0.95–1.00 |

> Ferrauti, p. 462: „Dies erfolgt beim Laufen … über eine prozentuale Berechnung anhand der
> 10-km-Bestleistung (Hottenrott & Zülch, 1998; Joch, 2004) oder der Angabe von
> Trainingsbereichen je nach angestrebter Leistung (Steffny, 2004; Tab. 7.7)."

**Consequence in code:** move `_hr_zones` from Friel's %LTHR to **%HRmax + Karvonen (2a)**
(labels ReKom/GA1/GA2/WSA). Replace Daniels' VDOT in `_pace_zones` with a **factor × race pace
(2b)** derived from a real race near 10 km or from the target performance. No lactate, no v4.

---

## 3. Race prediction — "am I on track?"

**Rule:** German sport science does **not** predict performance by extrapolating across
distances (Riegel). It predicts **from data, via performance diagnostics**: the realistic race
pace is derived from the individual threshold velocity (v4) or the sustained-performance limit,
and then compared against the target time.

> Ferrauti, p. 50: „Dies kann beim Ausdauertraining eines Freizeitläufers in der
> Vorbereitung auf einen Marathon die Ableitung seiner Trainingsgeschwindigkeit von den
> Ergebnissen einer zuvor durchgeführten Leistungsdiagnostik bedeuten."

**Consequence in code:** remove `_riegel`. "on_track" is determined only when a **real benchmark
race near the target distance** exists: compare the actual race pace against the pace the target
time requires. Without a comparable race, the coach says honestly that a prediction would need
performance diagnostics or a benchmark run (the book's stance: measure, don't extrapolate) —
**no** distance extrapolation (Riegel / `d^1.06`).

---

## 4. Training-plan structure (the six-step programme)

**Rule:** an evidence-based plan is built in six steps (Ferrauti tab. 1.2, p. 39):

1. **Training goal** — SMART (specific, measurable, achievable/reasonable, time-bound),
   e.g. "a marathon under 3 h".
2. **Literature review** — scientific evidence plus textbooks (→ our RAG).
3. **Training frame** — total duration, number and length of the microcycles, number of sessions.
4. **Training content** — suitable content and test methods, per the evidence.
5. **Long-term plan/progression** — volume, load distribution and progression longitudinally.
6. **Short-term plan** — weeks and sessions, continuously adjusted to current athlete monitoring.

> Ferrauti, tab. 1.2 (p. 39): „1. Trainingsziel … (z. B. Marathon unter 3 h) … 3. Trainingsrahmen:
> Festlegung von konkreten Zeitabschnitten (Gesamtdauer, Anzahl und Dauer der Mikrozyklen,
> Anzahl der Trainingseinheiten) … 5. Langzeitplan/Progression … 6. Kurzzeittrainingsplan:
> Planung von Trainingswochen und Trainingseinheiten … (z. B. Athleten-Monitoring)."

**Consequence in code:** `set_race_goal` is step 1, `scaffold_plan` is steps 3 + 5, and
`save_plan` plus the coach's workout selection are steps 4 + 6. Comments in `athlete_mcp.py`
reference this scheme.

---

## 5. Periodisation and cycles

**Rule:** total load is structured into macro- (one or more months), meso- (several weeks) and
microcycles (about a week). Classically three macrocycles: **preparation, competition and
transition phase** (peaking). The reason: peak form is unstable, and constantly high load is not
tolerable (Matwejew 1972).

> Ferrauti, pp. 69–70: „… drei Entwicklungsphasen bzw. Makrozyklen (Vorbereitungs-,
> Wettkampf- und Übergangsphase) zu durchlaufen. Diese werden wiederum in einzelne Zyklen
> kürzerer Dauer unterteilt … Makrozyklen über einen bis mehrere Monate … Mesozyklen über
> mehrere Wochen, Mikrozyklen gewöhnlich über eine Woche."

For the recreational runner there is a **simple mesocycle periodisation model** with the control
variables stimulus duration, load intensity, training frequency and tapering — in this order:
first raise **frequency → duration** (volume), then **intensity** at race pace, then taper.

> Ferrauti, p. 188 (fig. 3.65): „… zunächst den Trainingsumfang (erst Trainingshäufigkeit
> dann -dauer) steigern und erst anschließend intensivere Belastungen im avisierten
> Wettkampftempo absolvieren, bevor er in der Vorwettkampfwoche (Taper-Phase) die Belastung
> reduziert." (Einfaches Periodisierungsmodell (Mesozyklus) für den Freizeitläufer)

**Consequence in code:** map `_phase_split` (base/build/peak/taper) onto that documented order:
base ≈ building volume (frequency → duration), build/peak ≈ intensity at race pace, taper ≈ the
pre-competition phase.

---

## 6. Progressive overload (the stimulus-level rule) and unloading

**Rule (stimulus-level rule, Roux 1895):** stimuli only work within an individual window:
sub-threshold (no effect) → weakly supra-threshold (maintaining) → **strongly supra-threshold
(developing)** → too strong (damaging). "More is better" is wrong. Load must increase
**progressively but gradually**.

> Ferrauti, p. 81: „Auf Roux geht daher die … Reizstufenregel zurück, nach der zwischen
> unterschwelligen, schwach bzw. stark überschwelligen und zu starken Reizen unterschieden wird."
> Ferrauti, p. 87: „… eine individuell optimale Reizsetzung … und unreflektierte Theorien nach
> dem Motto ‚Je mehr, desto besser!' [sind] langfristig wenig zielführend."
> Güllich, p. 631: „Die (Kraft-)Trainingsreize müssen also mit zunehmendem Expertiseniveau
> systematisch gesteigert werden (Prinzip der progressiven Belastung)."

**Rule (unloading):** load cycles (e.g. four weeks) end with an **unloading phase** to allow
recovery and adaptation.

> Ferrauti, p. 295: „… Vorbereitungszyklus und einem anschließenden Intensivierungszyklus von
> je vier Wochen Dauer. Die Reizsetzung erfolgt progressiv … Entlastungsphasen sind jeweils am
> Ende der Zyklen zur Ermöglichung von Regeneration und Adaptation eingeplant."

**Consequence in code:** the ramp cap (`MAX_WEEKLY_RAMP`) and the **four-week cutback**
(`CUTBACK_EVERY = 4`, unloading at the end of a cycle) are **backed by the book** — document them
as "gradual, progressive increase with unloading at the end of the cycle" (Ferrauti p. 295). The
exact percentage of the cap is a conservative convention, not a value from the book, and stays
declared as a **soft guardrail** rather than a law.

---

## 7. Tapering (the immediate pre-competition phase)

**Rule:** in the final phase before a race, **cut load VOLUME markedly but keep intensity**, so
fatigue dissipates while fitness is retained. Duration: **up to about two weeks (2–15 days)**.
Timed right it produces an **overshooting** rise in form; reducing too early gives performance
away.

> Ferrauti, p. 117: „… schließt sich … die Taper-Phase an (Mujika & Padilla, 2003). In dieser
> Phase wird über einen längeren Zeitraum von bis zu zwei Wochen bei deutlich reduziertem
> Belastungsumfang durch das Abklingen der trainingsbedingten [Ermüdung] …"
> Ferrauti, p. 117 (fig. 3.17): „1. Phase (2–15 Tage) Tapering".
> Ferrauti, p. 82: „… Taper-Phase … in der nur noch wenige intensive Trainingsreize zum Erhalt
> der Fitness gesetzt werden und die Ermüdung entsprechend kontinuierlich abklingen kann."
> Ferrauti, p. 91 (fig. 2.28): „Wird das Overreaching zum richtigen Zeitpunkt durch eine
> Taperphase unterbrochen (B), kann eine überschießende Leistungssteigerung gegenüber einer zu
> frühzeitigen Reduktion der Trainingsreize (A) erreicht werden."

**Consequence in code:** taper = **one to two weeks**, **volume** down markedly (the existing
40–60 % volume step matches "deutlich reduziert"), **intensity retained**. The existing taper
guardrail check in `_validate_plan` stays, now with this source attached.

---

## 8. HIIT design (concrete protocols)

**Rule:** intensity follows interval duration — the shorter the interval, the higher the intensity
(% of maximal performance/HRmax). Documented protocols (Ferrauti pp. 58–59):

| Protocol | Structure | Intensity | work:rest |
|---|---|---|---|
| long interval | **4 × 4 min** | 80–85 % Pmax | 2:1 |
| — | 7 × 2 min | ~80–85 % | 1:1 |
| short interval | 2 × 10 × 30 s | **90–95 %** | 1:2 |
| — | 3 × 9 × 15 s | 90–95 % | 1:3 |
| sprint (IST/RST) | 4 × 6 × 5 s | **100 % (all-out)** | 1:6 |

> Ferrauti, p. 58: „… bei den längeren HIIT-Protokollen über 2 bis 4 min unter der vorgegebenen
> Belastungsintensität (80–85 % der Maximalleistung) höher als bei einer kürzeren Belastungsdauer
> von 15–30 s, obwohl hier die Belastungsintensität auf 90–95 % der Maximalleistung angehoben
> wurde … Intervallsprinttraining mit nur 5 s Belastungsdauer, dafür aber mit 100 %
> Belastungsintensität („all-out")."

Additionally (Güllich): the intensive interval method "all out", a moderate rest (2–4 min), and
some 10–12 repetitions. For untrained athletes, longer rests (e.g. 90 s active, Dransmann).

**Consequence in code:** the coach (the LLM) picks HIIT workouts **from these documented
protocols** instead of inventing them, with intensity expressed as a zone or %HRmax per section 2.
The protocol table belongs in the coach prompt, or is cited through the RAG search.

---

## 9. Monitoring and individualisation (readiness — with care)

**Rule:** training should be fine-tuned day to day through **monitoring** (external load, internal
strain, readiness). **But:** opaque "readiness scores" with unknown formulas are, per Ferrauti,
**highly problematic** — metrics must be defined, traceable and verifiable.

> Ferrauti, p. 185 (3.5.4): „Das Monitoring … gilt mittlerweile als zentrale Schlüsselstelle im
> Prozess der Individualisierung von Trainingsmaßnahmen."
> Ferrauti, p. 202: „Gleichermaßen bewerten wir die zahlreichen vermeintlich anwenderfreundlichen
> ‚Scores' für Ermüdung, Erholung oder ‚Readiness' als hochgradig problematisch. In den meisten
> Fällen fehlen Definitionen … bleiben die … Berechnungsformeln unbekannt. Damit ist eine externe
> und unabhängige Validierung solcher Score oft kaum möglich."

**Consequence in code:** the athlete design — every number recomputable, its basis logged — is
exactly right. Should a readiness or monitoring signal be added, it must be **transparent and
built from documented raw values** (e.g. a resting-HR trend), never a black-box score.

---

## 10. Supercompensation — to be treated critically

**Rule:** the naive fixed-timing supercompensation model counts as a didactic simplification;
modern control is antagonistic, complex and monitoring-based.

> Ferrauti (expert opinion, recovery chapter): „Superkompensation ist die Erdscheibe der
> Trainingslehre!"
> Güllich, p. 808 / p. 796: the stimulus-level rule plus wave-shaped load distribution (Matwejew);
> rigid adaptation rules have „lediglich einen didaktischen Wert".

**Consequence in code:** no hard timing assumption ("the next stimulus exactly inside the
supercompensation window"). Control comes from progressive load plus unloading plus monitoring
(sections 5–7 and 9).

---

## 11. Goal-driven plan structure, cross-training and build-up races

**Rule (goal first):** the plan is built **for the goal** (step 1 of the six-step programme,
section 4); history only supplies the starting point. Ferrauti's example plans (tab. 7.10–7.13)
show the goal-directed phase structure, including the role of **unspecific endurance training**
(cross-training) and of **build-up races** on the way to the season's peak.

> Ferrauti, tab. 7.10 (marathon year overview): general preparation phase =
> „Allgemeine Athletik (Koordination, Beweglichkeit, Technik, Kraft), unspezifisches
> Ausdauertraining (Skilanglauf, Rad/MTB, Schwimmen, Aquajogging), Ausdauer im Laufen";
> specific preparation phase = „Allgemeine Athletik und spezifische Ausdauer im Laufen
> (GA 1, GA 1-2, GA 2)"; then the immediate pre-competition phase („Wettkampfspezifische
> Ausdauer"). The race row on the way to the marathon: „Crosslauf … 5 km und 10 km …
> 10 km und HM …" — real **build-up races as intermediate stations**.

> Ferrauti, tab. 7.11 (marathon week plans): recovery sessions across all phases as
> cross-training — „Regenerationslauf oder Aquajogging (REKOM) 60 min",
> „90 min Radfahren oder 60 min Aquajogging (REKOM)"; the key sessions of race
> preparation are run-specific at race pace („4-5 x 2000 m in
> Halbmarathonrenntempo", „3 x 5 km in Marathonrenntempo (GA 2)").

> Ferrauti, tab. 7.12 (triathlon year overview): general preparation = „viel
> unspezifisches Ausdauertraining (MTB, Ruderergometer, Aquajogging, Skilanglauf)",
> later „vermehrt disziplinspezifisches Ausdauertraining" — combining sports is
> sensible, but tied to the phase.

**Cross-training rules (derived):** unspecific endurance (cycling, swimming, aqua jogging) belongs
in the **base phase** (GA 1) and as **ReKom recovery** in every phase; the closer the race, the
more **sport-specific** the key sessions — cross-training never replaces the race-specific
session. During injury windows, cross-training is the designed substitute (`blocked_sports`).

**Feasibility as facts, not an invented threshold:** the corpus contains no table saying "a
marathon needs X km per week". The server therefore only places recomputable facts side by side —
achievable peak weekly volume (the deterministic ramp) vs. race distance, and required race pace
(target time / distance) vs. benchmark race pace (section 3) — and the coach judges them against
the SMART criterion "achievable" (tab. 1.2, step 1) in conversation with the athlete. One
comparison is self-evidently arithmetic and is surfaced as an explicit `warning`: if even the
maximum permitted ramp leaves the peak **week** below the race **distance**, the athlete cannot
get race-ready on this path, and the coach MUST say so openly (adjust goal, date or expectation).

**Two-phase progression: frequency first, then duration (the recreational-runner model).**
A percentage ramp on a small base (8 km/week × 1.08 = +0.7 km) is meaningless in training terms —
and for exactly this case the corpus prescribes raising **frequency**:

> Ferrauti (Belastungsnormative): „Dies kann beispielsweise während der Vorbereitung auf
> einen **Halbmarathon** zunächst durch einen **Anstieg der Belastungshäufigkeit pro
> Woche** und anschließend durch eine **Steigerung der Belastungsdauer** … erfolgen.
> Schließlich sollte die Belastungsintensität … in Richtung des avisierten
> Wettkampftempos zunehmen." (p. 83)
> Ferrauti, p. 83: „Während die Trainingshäufigkeit im **Freizeitsport häufig nur 2–3
> TE/Woche** erreicht …"
> Ferrauti, p. 188: „… zunächst den Trainingsumfang (**erst Trainingshäufigkeit dann
> -dauer**) steigern …"

**Consequence in code (`_run_targets`) — the long-run line:** what gets planned are **runs, not
weekly totals**, laid out backwards from race day (the goal drives the plan; history only drives
the conversation). The **line of key sessions** ends in the last build week at the **race
demand** — the full race distance for goals up to about 25 km ("cover the distance once
beforehand"), 75 % above that (a marathon is never run in full beforehand) — and starts at half
the anchor distance, in even steps across all build weeks (steady approach, no jumps = progressive
load, §6). Frequency still rises first (+1 session per week, pp. 83/188); **companion runs ≈ 40 %
of the week's long run**, which makes weekly volume a **derivative of the runs**. Cutback week:
long run × 0.7 (p. 295); taper: 50 % / 30 % of peak (p. 117). The factors (the 25 km boundary,
75 %, the half-distance start, the 40 % companion run, the 50/30 taper) are **documented
engineering conventions**, not values from the books. Guardrails (`_validate_plan`): the largest
run of the week equals `long_run_km` (±10 %), workouts sum to the weekly total (±10 %), at most
+1 session per week; the old weekly percentage cap now applies only to legacy plans without a line.

**Outlook (deliberately not implemented yet):** the structural decision currently lives in this
deterministic layer. The intended inversion is for the coach agent to design the plan structure
itself, literature-backed, from a corpus extended with **prescriptive running literature** (e.g.
Steffny, Hottenrott/Zülch — both cited by Ferrauti himself), structured reference plans and an
explicit athlete-diagnosis step. The server would then be a pure **validator and calculator**
(guardrails, zones, dates, actuals).

**The adaptation loop (step 6 + monitoring):** the short-term plan is "continuously adjusted to
current athlete monitoring" (tab. 1.2, section 4; monitoring as the „zentrale Schlüsselstelle",
section 9). Implementation: `record_week_actual` logs actuals (raw values only — no black-box
scores, Ferrauti p. 202) and computes the actual/target ratio; `rescaffold_plan` re-bases **only
future** weeks on the volume actually demonstrated — progressively capped (+8 % from the last
frozen week, section 6), with unloading on overload (Ferrauti p. 295, the stimulus-level rule).

**Consequence in code:** `set_race_goal` carries the sport (`sport: run|ride`); `scaffold_plan`
returns a `phase_focus` per week (content per tab. 7.10/7.11) and a `feasibility` fact block;
`save_plan` marks non-target-sport workouts as cross-training (duration-based, excluded from the
`target_km` ramp guardrail); milestones are derived from the stored plan weeks (plus one real
build-up race per tab. 7.10); `record_week_actual` and `rescaffold_plan` form the adaptation loop.

---

## Mapping: code → its basis in the literature

Every computation in `servers/athlete_mcp.py` and where it comes from. The right-hand
column separates the two kinds of number in the system: values the books state, and
conventions we chose and declare as such.

| `athlete_mcp.py` | basis in the books | status of the numbers |
|---|---|---|
| `_hr_zones` | %HRmax + %HRR/Karvonen (Ferrauti pp. 446, 459) | book values |
| `_pace_zones` | factor × race pace (Ferrauti p. 462, tab. 7.7) | book values |
| prediction / "on track" | performance diagnostics, benchmark race (Ferrauti p. 50) | book method; no extrapolation |
| HRmax fallback | 208 − 0.7·age (Güllich p. 771) | book formula |
| `MAX_WEEKLY_RAMP` | progressive load (Güllich p. 631; Ferrauti p. 81) | principle from the books, percentage a declared soft guardrail |
| `CUTBACK_EVERY=4` | four-week cycle with unloading at its end (Ferrauti p. 295) | book value |
| `_phase_split` | macro/meso cycles + the recreational-runner model (Ferrauti pp. 69, 188) | book structure; order frequency→duration→intensity→taper |
| taper check | 2–15 days, volume down, intensity retained (Ferrauti p. 117) | book values |
| HIIT workouts | the protocols in Ferrauti pp. 58–59 | book protocols, selected by the coach |
| supercompensation | treated critically (Ferrauti/Güllich) | no timing assumption is made |
| `PHASE_FOCUS` (phase content) | tab. 7.10/7.11 (phases + cross-training) | book content, per scaffolded week |
| `_feasibility` (goal vs. data) | SMART/tab. 1.2 + section 3 (benchmark) | facts only; the judgement is the coach's |
| cross-training validation | tab. 7.10/7.11 (REKOM/unspecific) | duration-based, outside the km ramp |
| `record_week_actual` | tab. 1.2 step 6 + pp. 185, 202 (monitoring, raw values) | actuals + actual/target ratio, deterministic |
| `rescaffold_plan` | p. 295 (unloading), Güllich p. 631 (progressive) | future weeks only, +8 % cap from the last frozen week |
| `_run_targets` (long-run line) | progressive load §6; frequency before duration (pp. 83, 188) | principle from the books; the individual factors are documented conventions (§11) |
