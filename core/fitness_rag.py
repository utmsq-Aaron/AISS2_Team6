"""Fitness-literature vector store + retriever (the RAG seam).

The Fitness specialist is the one agent with **no MCP server**. Instead of calling
live APIs it answers from a local corpus of public-domain fitness/physical-culture
books (Project Gutenberg), retrieved with a lightweight **local** embedding model.

This module is the runtime side of that RAG pipeline:

  * an embedding model wrapper (``sentence-transformers``, default the small
    ``all-MiniLM-L6-v2`` — fast on Apple-Silicon MPS / CPU, ~90 MB),
  * a dependency-light **vector store** — an L2-normalised float32 matrix on disk
    plus a JSON chunk sidecar; cosine search is a single matrix-vector product in
    numpy (the corpus is a few thousand chunks, so no faiss/chroma is needed),
  * ``FitnessRetriever`` — loads both once per process and answers ``search()``.

The offline build (download → clean → chunk → embed → save) lives in
``scripts/build_fitness_index.py`` and reuses :func:`embed` / :class:`VectorStore`
here. Heavy imports (torch / sentence-transformers) are lazy so importing this
module stays cheap for processes that never touch the fitness agent.

Config (live from ``.env``, like ``core.llm``):
  FITNESS_EMBED_MODEL   default ``sentence-transformers/all-MiniLM-L6-v2``
  FITNESS_EMBED_DEVICE  default auto (mps → cuda → cpu); override e.g. ``cpu``
  FITNESS_INDEX_DIR     default ``data/fitness_library/index``
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np

from core.llm import _env

# Quiet a noisy tokenizers fork-warning when the model runs inside the agent loop.
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
_DEFAULT_INDEX_DIR = _ROOT / "data" / "fitness_library" / "index"


def embed_model_name() -> str:
    return _env("FITNESS_EMBED_MODEL", _DEFAULT_MODEL)


def index_dir() -> Path:
    raw = _env("FITNESS_INDEX_DIR", "")
    return Path(raw) if raw else _DEFAULT_INDEX_DIR


def _resolve_device() -> str:
    """Pick the best local device: explicit override, else MPS → CUDA → CPU."""
    forced = _env("FITNESS_EMBED_DEVICE", "").strip().lower()
    if forced:
        return forced
    try:
        import torch
        if torch.backends.mps.is_available():
            return "mps"
        if torch.cuda.is_available():
            return "cuda"
    except Exception:  # noqa: BLE001 — torch absent or probe failed → CPU
        pass
    return "cpu"


# ── Embedding model (lazy singleton) ──────────────────────────────────────────

_model = None  # cached SentenceTransformer, loaded on first use


def _get_model():
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer  # heavy → lazy
        name, device = embed_model_name(), _resolve_device()
        print(f"[fitness] loading embedding model {name} on {device}", flush=True)
        _model = SentenceTransformer(name, device=device)
    return _model


def embed(texts: List[str], *, batch_size: int = 64) -> np.ndarray:
    """Embed ``texts`` → an L2-normalised float32 matrix (N, dim).

    Normalising here means cosine similarity is a plain dot product at query time.
    """
    if not texts:
        return np.zeros((0, embedding_dim()), dtype="float32")
    vecs = _get_model().encode(
        texts,
        batch_size=batch_size,
        convert_to_numpy=True,
        normalize_embeddings=True,
        show_progress_bar=len(texts) > 256,
    )
    return np.asarray(vecs, dtype="float32")


def embedding_dim() -> int:
    return int(_get_model().get_sentence_embedding_dimension())


# ── Vector store (numpy matrix + JSON chunk sidecar) ──────────────────────────

class VectorStore:
    """An on-disk cosine-similarity index: normalised vectors + chunk metadata.

    Files in ``directory``:
      vectors.npy    float32 (N, dim), L2-normalised
      chunks.json    list[ {id, text, title, author, source_id, license} ]
      manifest.json  {model, dim, count, books, normalized}
    """

    VECTORS = "vectors.npy"
    CHUNKS = "chunks.json"
    MANIFEST = "manifest.json"

    def __init__(self, vectors: np.ndarray, chunks: List[Dict[str, Any]],
                 manifest: Optional[Dict[str, Any]] = None) -> None:
        self.vectors = vectors
        self.chunks = chunks
        self.manifest = manifest or {}

    # -- persistence -----------------------------------------------------------

    def save(self, directory: Path) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        np.save(directory / self.VECTORS, self.vectors)
        (directory / self.CHUNKS).write_text(
            json.dumps(self.chunks, ensure_ascii=False), encoding="utf-8")
        (directory / self.MANIFEST).write_text(
            json.dumps(self.manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    @classmethod
    def load(cls, directory: Path) -> "VectorStore":
        vectors = np.load(directory / cls.VECTORS)
        chunks = json.loads((directory / cls.CHUNKS).read_text(encoding="utf-8"))
        manifest_path = directory / cls.MANIFEST
        manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
        return cls(vectors, chunks, manifest)

    @classmethod
    def exists(cls, directory: Path) -> bool:
        return (directory / cls.VECTORS).exists() and (directory / cls.CHUNKS).exists()

    # -- search ----------------------------------------------------------------

    def search_vec(self, query_vec: np.ndarray, k: int = 5) -> List[Dict[str, Any]]:
        """Top-``k`` chunks by cosine similarity to a (normalised) query vector."""
        if self.vectors.shape[0] == 0:
            return []
        scores = self.vectors @ query_vec.astype("float32")  # (N,) cosine sims
        k = min(k, scores.shape[0])
        # argpartition for the top-k, then sort just those by score (descending).
        top = np.argpartition(-scores, k - 1)[:k]
        top = top[np.argsort(-scores[top])]
        out: List[Dict[str, Any]] = []
        for idx in top:
            chunk = self.chunks[int(idx)]
            out.append({**chunk, "score": round(float(scores[int(idx)]), 4)})
        return out


# ── Retriever (lazy singleton used by the agent) ──────────────────────────────

class FitnessRetriever:
    """Loads the vector store + embedding model once, answers ``search()``."""

    def __init__(self, directory: Optional[Path] = None) -> None:
        self.directory = directory or index_dir()
        self._store: Optional[VectorStore] = None

    @property
    def store(self) -> VectorStore:
        if self._store is None:
            if not VectorStore.exists(self.directory):
                raise FileNotFoundError(
                    f"Fitness vector index not found at {self.directory}. "
                    "Build it with: python -m scripts.build_fitness_index")
            self._store = VectorStore.load(self.directory)
        return self._store

    def available(self) -> bool:
        return VectorStore.exists(self.directory)

    def search(self, query: str, k: int = 5) -> List[Dict[str, Any]]:
        """Embed ``query`` and return the ``k`` most relevant book passages."""
        q = embed([query])[0]
        return self.store.search_vec(q, k=k)

    def sources(self) -> List[Dict[str, Any]]:
        return self.store.manifest.get("books", [])


_retriever: Optional[FitnessRetriever] = None


def get_retriever() -> FitnessRetriever:
    """Process-wide singleton so the model/index load only once."""
    global _retriever
    if _retriever is None:
        _retriever = FitnessRetriever()
    return _retriever


def index_available() -> bool:
    return VectorStore.exists(index_dir())


# ── tiny CLI for smoke-testing: python -m core.fitness_rag "deadlift form" ─────

if __name__ == "__main__":
    import sys

    query = " ".join(sys.argv[1:]) or "how should a beginner build strength?"
    hits = get_retriever().search(query, k=5)
    print(f"\nQuery: {query}\n")
    for i, h in enumerate(hits, 1):
        src = f"{h.get('title', '?')} — {h.get('author', '?')}"
        print(f"[{i}] score={h['score']}  {src}")
        print("    " + " ".join(h["text"].split())[:240] + "…\n")
