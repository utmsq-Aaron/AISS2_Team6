# Handover — Training Copilot Seminar Paper (`aiss2026/`)

_Last updated: 2026-07-07._ This document is the pick-up point for anyone (human or coding agent) continuing the paper. The **binding rules** for citations/sources live in the repo-root `CLAUDE.md` → "Seminar Paper → Citation Knowledge Base — MANDATORY"; this file is the state + workflow + outstanding-work summary.

## Current status

- **Builds clean.** `build.bat` produces `thesis.pdf` (**85 pages**; body = intro→conclusion is **42 printed pages**), **0 undefined citations/references**, **0 `??` markers**.
- **Chapters are `\input`, not `\include`** (thesis.tex) so they flow continuously like the template's single `main_body.tex`, instead of each starting on a fresh page. This alone took the body from 47 to 42 pages with no content cut. All other layout settings (`parskip 0.2cm`, `baselinestretch 1.24`, `raggedbottom`) match `latex_template_original` exactly.
- **69 references**, each with the full four-artifact set (bib entry + KB file + source PDF + `REFERENCES.md` entry). All numeric claims re-verified **directly against the source PDFs** (not just the KB) on 2026-07-11.
- The product is called **Training Copilot** throughout (never "FitDash").
- **The evaluation is not yet run** — Chapter 7 ships methodology + `TBD` placeholder tables. This is the single biggest outstanding item (see below).
- **Body length note:** the body is **float-dominated** (~14 figures/tables set the page budget; each ≈ 1 page). Prose was compacted hard (all per-paper percentages + main redundancies removed) but the page count is sticky at ~47 — proven empirically: ~2.6 KB of removed prose = 0 pages, while removing one figure = −1 page. Getting to ~40 needs figure/table removal, not more prose trimming; the author chose to **keep all figures** (2026-07-11), so 47 is the accepted floor.

## What changed in the 2026-07-11 pass

- **Full fact-check against the PDFs.** Re-verified every numeric SOTA/market/competitor claim directly against `sources/*.pdf` (PyMuPDF), not the KB. All matched. Two corrections applied: joerke "93% MI-consistent" → "93% MI-consistent **or neutral**" (source says both); rothschild RMSE 5.5–23.6 was "fivefold" → "more than fourfold" (it is 4.3×).
- **New feature shipped in code, now in the paper — Google Maps MCP server (`servers/google_maps_mcp.py`, port 8108, 6 tools).** Adds place/POI search, place details, geocoding, directions, and `maps_search_along_route` (café/bakery/water-fountain **on a planned route** via the route tools' `poi_anchors`). Route specialist scope is now `["routes","google_maps"]`. Updated: §5 intro (7→**8 data sources**), new §5.3 Maps paragraph, §5.4 "six→**seven** native servers", `tab_data_summary` (+Maps row; "8 books"→**50 books**), `tab_full_tools` (+6-tool Maps block), `tab_agent_scope` (Route +Maps), `tab_api_overview` (+Google Maps Platform row), `fig_architecture` (+Maps :8108 node + Route→Maps arrow), tool counts "~45/40+" → **~50**, and Ch9 "experience-aware route planning" moved from *future work* to **shipped** (remaining: multi-stop itineraries + a curated route source).
- **Planned→shipped features (were "planned" on 2026-07-07, now in code):** onboarding wizard (`web/src/components/onboarding`, `core/user_profile.py`), goal tracking + agent-authored dashboard panels (`core/goal_panel.py`, `core/goal_store.py`), a scheduler + proactive push to the in-app Coach chat and Telegram (`core/schedule_store.py`, `core/goal_build_queue.py`, `core/delivery.py`), and the one-click **report button** (`web/src/components/FeedbackButton.tsx`, `api/feedback_service.py`). Rewrote §4.7 Supporting Subsystems, §6.2 (fourth delegation pattern now real), §6.5 (report button real), §7.3 (human-label loop reframed as the only remaining future part). **React is now the primary UI**; Streamlit is kept for tests, not updated (§4.7 multi-channel).
- **Author-comment corrections (from `thesis_with_comments.pdf`, 73 annotations):** fixed a **hallucinated** early-prototyping example in §4.2 ("using weather data to answer recovery questions" → "combined tools incorrectly in complex, multi-step queries"); §5 intro + §5.4 rewritten to drop AI-generated tone; CTL/ATL/TSB glossed at first use; Strava API "previously free"; §7.6 model-separation corrected (system under test also on OpenAI because the KIT gateway is too slow); personas note runners/hikers; §8 notes known issues are being fixed, adds a RAG-relevance hedge and the proactive capability; circular-route-as-PoC in the outlook; removed the Wahoo-stub trivia; `tab_api_overview` LLM row renamed toward "KIT AI Toolbox (DSI experiment gateway)".
- **luo four-artifact fix:** `sources/luo_promoting_2020.pdf` → `luo_promoting_2021.pdf` (bib/KB/cite were already 2021; PDF basename now matches).
- **Length:** compacted per-paper statistics + restatements across Ch1–Ch8 (much leaner prose). Body stays **47** printed pages because it is float-dominated (16 body figures/tables ≈ 1 page each; proven — prose cuts reflow around floats and don't drop pages). Author decided (2026-07-11) to **keep all figures inline** and accept 47; relocating illustrative figures to an appendix was offered and declined.
- **Gateway naming corrected:** the app uses **two separate** KIT-hosted OpenAI-compatible endpoints — the **KIT AI Toolbox** (`ki-toolbox.scc.kit.edu`, `azure.gpt-5-mini`, current default) and the **DSI Experimente AI Gateway** (`ai-gateway.dsi-experimente.de`, `kit.gpt-5`). `tab_api_overview` names both; §7.6 now says the DSI gateway's latency is why the evaluation runs on official OpenAI. (Earlier drafts wrongly merged the two.)
- **Ink-annotation re-audit:** the 46 ink strokes in `thesis_with_comments.pdf` are PDF annotation **type 15** (Ink), not 14 — re-analysed by stroke geometry (strike vs underline vs box). Beyond the already-applied fixes this confirmed three more: removed the struck "instabilities under streaming load" over-claim (§4.4, §8.3), removed the struck `(Anthropic, 2024a)` cite in §9 (still cited in §3, no orphan), and condensed the boxed speculative §7.7/§7.8.

## How to build

```bat
cd aiss2026
build.bat            :: resolves MiKTeX itself; set MIKTEX_BIN=... if it can't find pdflatex
```

`build.bat` runs `pdflatex → bibtex → pdflatex → pdflatex`. **New `\label`s / new citations may need a second `build.bat` run** to converge (a new label lands in the aux one pass late). Verify with:

```bash
grep -i "undefined" build/thesis.log        # must be empty
# and check the PDF has no literal "??" (unresolved \ref/\cite)
```

**Build gotcha (will bite you).** `*.aux`/`*.bbl`/chapter `*.aux` are gitignored build artifacts, but stray *committed* copies in the source root (`aiss2026/*.aux`, `aiss2026/thesis.bbl`, `aiss2026/chapters/*.aux`) **shadow** the fresh ones under `build/`, causing phantom "undefined citation/reference" + `??` even when the `.bbl` is correct. Fix: delete the stray root-level artifacts and rebuild; never commit them.

## Citation & source workflow (summary — full rules in `CLAUDE.md`)

Every `\cite{key}` needs, all sharing basename `{key}`:
1. `references.bib` → `@type{key, …}`
2. `citation_kb/{key}.txt` → claim + `SOURCE CONFIRMS` quote, verified against the PDF
3. `sources/{key}.pdf` → **real PDF** of the paper or website (arXiv download, or Playwright print-to-PDF for web pages) — never a stub
4. `REFERENCES.md` → mapping-table row + full entry + bottom "New References" link, and bump `Total: N`; plus a `citation_map.md` row

Verify the PDF text supports the claim before writing the `\cite`. PyMuPDF (`fitz`) and `pypdf` are available in the `aiss2026` conda env for text extraction. If a source doesn't support a specific claim, drop it — don't force-cite.

To capture a website as PDF (the pattern used for the competitor sources):

```python
# playwright headless chromium: goto → scroll to load lazy content → page.pdf(...)
# see the capture approach used for garmin/oura/runna/tridot sources
```

## Outstanding work (in priority order)

1. **Run the evaluation and fill the placeholders.** The framework is `evaluation/run_e2e.py` (`python -m evaluation.run_e2e`, needs the full stack + MLflow up via `./dev_stack.sh`). Then populate:
   - `2_Tables/tab_results.tex` — replace every `TBD` with the per-type / overall scorer pass rates + mean turns/latency.
   - `2_Tables/tab_ablation.tex` — run the **single-agent baseline** (one ReAct agent over all servers' tools) against the same 10 personas and fill correct-tool-selection / completeness / grounding / latency vs. the multi-agent config.
   - Update `chapters/7_evaluation.tex` §Expected Outcomes → replace expectations with actual findings once known.
   - Confirm the eval model IDs in `evaluation/config.py` (`gpt-5.4-mini` / `gpt-5.4-nano`) are the real IDs you run against before the numbers land.
2. **Decide the fate of the "planned" features.** `chapters/4_architecture.tex` §Supporting Subsystems and `chapters/6_agent_interaction.tex` describe the **onboarding questionnaire**, **goal-plan tracking / schedule-triggered delegation**, and the **report button** as *planned* — because they are **not in the code** (verified: no onboarding UI, no scheduler, no report/feedback button in Python or `web/`). Either build them (then change "would/planned" → present tense) or leave them framed as planned. Do **not** revert them to present-tense claims without shipping the code.
3. **Optional: the RAG citation-faithfulness scorer** (§7.3 names it as a planned sixth dimension). If you build it in `evaluation/scorers.py`, update §7.3 and `tab_scorers.tex`.

## What changed in the 2026-07-07 pass

- **Competitors (Ch. 2):** added Garmin Connect+, Oura Advisor, Runna, TriDot to `tab_competitors.tex` + prose on the 2025 AI-coach wave; softened the absolute MCP+A2A novelty claim to "to the best of our knowledge."
- **SOTA:** added `zheng_judging_2023` (LLM-as-a-Judge) to §7 and `hou_mcp_2025` (MCP security) to §3.2 + §8.4; wove the two previously-orphaned wearable refs (`li_wearable_2016`, `alzahrani_advanced_2024`) into §3.3.3; removed `strava_year_2024` (unverifiable).
- **Evaluation (Ch. 7):** renamed the duplicate §7.1 heading; added `tab_results.tex` + `tab_ablation.tex` placeholders, metric-semantics + small-sample caveat, the planned citation-faithfulness scorer, and the human-label loop.
- **Flythrough:** wired the `flythrough` MCP server into the load specialist's scope (`core/config.py`) so it is chat-triggerable; updated §4.6, `tab_agent_scope.tex`, `tab_full_tools.tex`, removed the stale known-issues row, and changed "at most two → three" servers everywhere (`1–27` tools/specialist).
- **Truthfulness:** reframed onboarding / goal-plan / report-button as planned; deleted two "will be checked before submission" hedge footnotes; added the Activity Analysis tab to §5.1; clarified the "40+ tools" count excludes the 116-tool Telegram bridge.

## What changed in the 2026-07-08 follow-up

- **Reference overhaul** (repo `references.bib` is authoritative — **69 entries, builds clean**): removed unverifiable `strava_year_2024`; corrected two author lists (`impellizzeri_training_2020`, `venter_bias_2023`); dropped the extra `tridot_devices_2026` (TriDot device-data claim now carried by `tridot_2025` = the `what-tridot-delivers` page). A user-side Zotero export is being reconciled to match; the only remaining user-side fixes are cosmetic (a few corporate authors need `{{…}}` double-braces; `tridot_2025` needs `date=2025`).
- **Reference formatting:** all arXiv/ACL papers converted from raw URLs to DOIs (`10.48550/arXiv.<id>`) → list is now uniform (papers → DOI, real websites → URL).
- **Ordering:** *AI Usage Documentation* moved to the **end of the appendix** (references stay last, per `latex_template_original`).
- **Architecture Fig. 3:** added the **Flythrough MCP server (:8107)** to the MCP row + a `Load → Flythrough` arrow (row rebuilt as a fixed-gap 7-server chain so it never overlaps).
- **Attribution / hosting / test data (§5 intro, §4.7):** now explicit that **all servers are our own native FastMCP code except the vendored external Telegram bridge**; Docker = one container per MCP server and per agent from a single shared Dockerfile; footnote that we used the authors' own Strava/Garmin accounts and can grant graders access.
- **Precision:** fixed stale "five MCP servers" → "six native"; sharpened RAG params (≈900-char chunks / 150 overlap, top-5 retrieval, NumPy matrix–vector, no FAISS).
- **New content:** competitor row now includes **Claude** + a note on Strava's (rolling-out) MCP server; §8.3 **route-quality limitation** (Komoot/Outdooractive keep curated route data closed → fallback to OpenRouteService/OSM); §9 **experience-aware route planning** future work (POI-along-route via a places API).

## RAG corpus expansion (done — 8 → 50 books)

- `scripts/fetch_fitness_books.py` `BOOKS` list expanded to **50 public-domain Gutenberg titles** (physical culture/strength, athletics, physiology/anatomy, specific sports, hygiene/health); all 50 fetched to `data/fitness_library/corpus/` + `sources.json`. Total **32,125 chunks** (≈32k). Paper counts updated in §3.4, §5.4, §8.3, §9 and `fig_rag_pipeline.tex` ("fifty books, ~32k chunks"); §5.4 description broadened from "exercise-science books" to "physical culture, athletics, physiology and health."
- **Embedding index NOT rebuilt here** — `sentence-transformers` is not installed in the eval conda env, so the vector index rebuilds automatically at app launch (`build_fitness_index --if-missing` / `--rebuild`). For the paper only the corpus + `sources.json` + updated counts matter; commit those. (If you want the committed index refreshed, run `python -m scripts.build_fitness_index --rebuild` in an env with `sentence-transformers`.)

## New references added this pass

| Key | What | Source PDF |
|---|---|---|
| `garmin_connectplus_2025` | Garmin Connect+ / Active Intelligence (competitor) | Garmin newsroom, 27 Mar 2025 |
| `oura_advisor_2025` | Oura Advisor conversational AI coach (competitor) | Oura blog, 31 Mar 2025 |
| `runna_2025` | Runna AI running plans / Strava acquisition (competitor) | Strava press, 17 Apr 2025 |
| `tridot_2025` | TriDot AI triathlon optimisation (competitor) | TriDot article, 6 Jun 2025 |
| `zheng_judging_2023` | Judging LLM-as-a-Judge (MT-Bench/Chatbot Arena) | arXiv 2306.05685 |
| `hou_mcp_2025` | MCP: Landscape, Security Threats | arXiv 2503.23278 |

## Pre-submission checklist

- [ ] Evaluation run; `tab_results` + `tab_ablation` filled; §Expected Outcomes updated to findings.
- [ ] Planned features (onboarding / goal-plan / report button) either shipped-and-re-worded or confirmed left as planned.
- [ ] `build.bat` twice → `grep -i undefined build/thesis.log` empty, no `??` in the PDF.
- [ ] Every `\cite{}` still has all four artifacts (bib + KB + `sources/*.pdf` + `REFERENCES.md`); `citation_map.md` current.
- [ ] Product name is "Training Copilot" everywhere; no "FitDash" in the paper.
- [ ] No stray committed `*.aux`/`*.bbl` in the source tree.
- [ ] RAG book/chunk counts in §3.4, §5.4, §8.3, §9 match the actual corpus (updated after the 8 → ~50 book expansion).
