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

8 **public-domain** fitness / physical-culture books from **Project Gutenberg**
(redistributable, so the corpus lives in the repo). Curated for breadth — strength,
endurance/athletic conditioning, women's physical training, physical culture/massage
and general exercise & health. The manifest is `data/fitness_library/sources.json`.

> We deliberately do **not** pull from shadow-library sites (e.g. libgen): those
> distribute copyrighted books without permission. Public-domain sources give the
> same RAG demonstration with clean provenance.

## The vector store

Dependency-light by design — no faiss/chroma. The index is just:

```
data/fitness_library/index/
  vectors.npy     float32 (N, 384), L2-normalised   → cosine = a dot product
  chunks.json     [{id, text, title, author, source_id, license}, …]
  manifest.json   {model, dim, count, books, normalized}
```

Embeddings come from a small **local** model (default
`sentence-transformers/all-MiniLM-L6-v2`, ~90 MB) — no embedding API needed; runs on
Apple-Silicon MPS / CPU. The corpus is a few thousand chunks, so brute-force cosine
search in numpy is instant. Runtime code is in `core/fitness_rag.py`.

## Build / run

The launch scripts (`./dev_stack.sh`, `./start.sh`) build the index automatically on
first run (idempotent — skipped if present) and then start the agent on :9005.
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
re-run fetch + build. Use only public-domain / openly-licensed sources.
