# Fitness Expert agent — RAG over a vector DB

The **Fitness Expert** specialist (A2A server on **:9005**) is the one agent that
does **not** use an MCP server. Where the other specialists call live APIs (Garmin,
Strava, weather, routes), the fitness agent answers general training / technique /
exercise-science questions from a **local vector database of fitness literature**,
retrieved with **RAG**.

This keeps the architecture honest: it shows the same LangGraph + A2A specialist
pattern with a *different* knowledge backend (a retriever tool instead of MCP
tools), and the orchestrator coordinates it identically.

## How it fits the existing architecture

```
orchestrator (:9000)  --ask_fitness-->  fitness agent (:9005)
                                            │  search_fitness_literature(query, k)
                                            ▼
                                   core/fitness_rag.py  (embed query → cosine top-k)
                                            ▼
                                   data/fitness_library/index/  (vectors + chunks)
```

The agent is a normal `create_agent` ReAct loop whose single tool,
`search_fitness_literature`, is recorded into the same `recorder` shape the MCP
wrapper uses — so the retrieved call shows up in the orchestrator's `trace` and the
UI's agent-trace panel exactly like any other tool call. No UI change was needed.

## The corpus

Five German **sport-science textbooks** — training science, the HIIT literature and
sports nutrition. They are the same works the deterministic training math is derived
from (see [`trainingsregeln.md`](trainingsregeln.md)), which is the point: the coach's
arithmetic and the Fitness Expert's prose answers rest on one body of literature
rather than on two unrelated sources. The manifest is
`data/fitness_library/sources.json`, the full bibliography
`data/fitness_library/SOURCES.txt`.

> **Licensing.** These are copyrighted works (Springer and journal articles),
> obtained through the university library and used **locally, for retrieval only** —
> the text is never redistributed by the app and never leaves the machine. The source
> PDFs are deliberately not in the repo. An earlier build used ~50 public-domain
> Project Gutenberg titles; those were replaced because century-old physical-culture
> books cannot ground modern training rules. Nothing here comes from a shadow library.

## The vector store

Dependency-light by design — no faiss/chroma. The index is just:

```
data/fitness_library/index/
  vectors.npy     float32 (N, 384), L2-normalised   → cosine = a dot product
  chunks.json     [{id, text, title, author, source_id, license}, …]
  manifest.json   {model, dim, count, books, normalized, corpus_fingerprint}
```

`corpus_fingerprint` is a checkout-deterministic sha256 over the corpus's
`(filename, byte-size)` pairs (plus `sources.json`'s size) — the staleness signal
the launchers use to auto-rebuild when the corpus changes (see *Build / run*).

Embeddings come from a small **local** model (default
`sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`, ~460 MB) — no embedding API needed; runs on
Apple-Silicon MPS / CPU. The corpus is a few thousand chunks, so brute-force cosine
search in numpy is instant. Runtime code is in `core/fitness_rag.py`.

## Build / run

The launcher (`./run.sh`) builds the index automatically on
first run and then start the agent on :9005. They call
`build_fitness_index --if-missing`, which skips instantly when an index already
exists *and* both its stored `corpus_fingerprint` and its `model` still match, but
**auto-rebuilds** when either differs — i.e. whenever books were added, removed,
resized, `sources.json` changed, or `FITNESS_EMBED_MODEL` was switched. So every
machine self-heals a stale index on its next start; no launcher change and no manual
rebuild is needed. Should an index somehow survive a model switch anyway,
`FitnessRetriever.search` refuses it with the rebuild command rather than comparing
vectors from two different models.

Manually:

```bash
pip install -r requirements.txt                  # adds sentence-transformers (+ torch)
python -m scripts.build_fitness_index            # embed → data/fitness_library/index/
python -m agents.fitness_agent                   # serve the agent on :9005

# quick retrieval smoke test (no LLM):
python -m core.fitness_rag "Wie viele Kohlenhydrate brauche ich vor einem langen Lauf?"
```

The built index is git-ignored (a derived artifact); the corpus and scripts are
committed, so a rebuild is deterministic and offline.

## Config (optional, live from `.env`)

| var | default | purpose |
|-----|---------|---------|
| `FITNESS_EMBED_MODEL`  | `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` | embedding model |
| `FITNESS_EMBED_DEVICE` | auto (`mps`→`cuda`→`cpu`)                | force a device |
| `FITNESS_INDEX_DIR`    | `data/fitness_library/index`             | index location |

## Adding / changing books

Put the PDF in `data/fitness_library/Literatur_Fitness/`, add its entry to the `BOOKS`
list in `scripts/extract_literature_corpus.py` (slug, title, author, licence,
source URL), then re-extract:

```bash
python -m scripts.extract_literature_corpus     # --replace wipes the old corpus first
```

You can rebuild the index explicitly with `python -m scripts.build_fitness_index`, but
you don't have to: the corpus change bumps the `corpus_fingerprint`, so the next
launcher start (which runs `--if-missing`) detects the mismatch and rebuilds
automatically. Keep the licence field honest — it is what tells a reader of the repo
what the corpus actually is.
