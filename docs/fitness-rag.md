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

50 **public-domain** fitness / physical-culture books from **Project Gutenberg**
(redistributable, so the corpus lives in the repo). Curated for breadth — physical
culture & strength training, athletics & specific sports (swimming, boxing,
wrestling, fencing, football, golf, cycling, dancing), the physiology and anatomy of
the human body, and personal/public hygiene, longevity & health. The manifest is
`data/fitness_library/sources.json`.

> We deliberately do **not** pull from shadow-library sites (e.g. libgen): those
> distribute copyrighted books without permission. Public-domain sources give the
> same RAG demonstration with clean provenance.

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
`sentence-transformers/all-MiniLM-L6-v2`, ~90 MB) — no embedding API needed; runs on
Apple-Silicon MPS / CPU. The corpus is a few thousand chunks, so brute-force cosine
search in numpy is instant. Runtime code is in `core/fitness_rag.py`.

## Build / run

The launch scripts (`./dev_stack.sh`, `./start.sh`) build the index automatically on
first run and then start the agent on :9005. They call
`build_fitness_index --if-missing`, which is **corpus-aware**: it skips instantly
when an index already exists *and* its stored `corpus_fingerprint` matches the
current corpus, but **auto-rebuilds** when the fingerprint differs — i.e. whenever
books were added, removed, resized, or `sources.json` changed since the index was
last built. So every machine self-heals a stale index on its next start; no launcher
change and no manual rebuild is needed after the corpus grows.

Manually:

```bash
pip install -r requirements.txt                  # adds sentence-transformers (+ torch)
python -m scripts.fetch_fitness_books            # download the corpus (committed already)
python -m scripts.build_fitness_index            # embed → data/fitness_library/index/
python -m agents.fitness_agent                   # serve the agent on :9005

# quick retrieval smoke test (no LLM):
python -m core.fitness_rag "how should a beginner build strength?"
```

The built index is git-ignored (a derived artifact); the corpus and scripts are
committed, so a rebuild is deterministic and offline.

## Config (optional, live from `.env`)

| var | default | purpose |
|-----|---------|---------|
| `FITNESS_EMBED_MODEL`  | `sentence-transformers/all-MiniLM-L6-v2` | embedding model |
| `FITNESS_EMBED_DEVICE` | auto (`mps`→`cuda`→`cpu`)                | force a device |
| `FITNESS_INDEX_DIR`    | `data/fitness_library/index`             | index location |

## Adding / changing books

Edit the `BOOKS` list in `scripts/fetch_fitness_books.py` (Gutenberg id + slug), then
re-run fetch (`python -m scripts.fetch_fitness_books`). You can rebuild the index
explicitly with `python -m scripts.build_fitness_index`, but you don't have to: the
corpus change bumps the `corpus_fingerprint`, so the next launcher start (which runs
`--if-missing`) detects the mismatch and rebuilds automatically. Use only
public-domain / openly-licensed sources.
