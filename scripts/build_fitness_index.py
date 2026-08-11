"""Build the Fitness RAG vector index from the corpus.

Pipeline:  corpus/*.txt → chunk → embed (local model) → save a normalised vector
matrix + chunk sidecar to the index dir.

    python -m scripts.build_fitness_index               # build
    python -m scripts.build_fitness_index --if-missing  # no-op when an index already exists
    python -m scripts.build_fitness_index --rebuild     # force a clean rebuild

The ``--if-missing`` form is what the launch scripts call: the first run downloads
the embedding model (~90 MB) and embeds the corpus; later runs skip instantly.

The corpus itself is committed (``data/fitness_library/corpus/*.txt``), produced
from the source PDFs by ``scripts.extract_literature_corpus``. This script only
turns that text into vectors — it never fetches anything.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CORPUS_DIR = ROOT / "data" / "fitness_library" / "corpus"
SOURCES_JSON = ROOT / "data" / "fitness_library" / "sources.json"

CHUNK_CHARS = 900       # target chunk size (characters)
CHUNK_OVERLAP = 150     # carried-over context between adjacent chunks
MIN_CHUNK_CHARS = 200   # drop slivers shorter than this


def chunk_text(text: str) -> list[str]:
    """Paragraph-aware splitter: pack paragraphs to ~CHUNK_CHARS with overlap.

    Whitespace is normalised first, so embeddings see continuous prose rather
    than the PDF extractor's line breaks.
    """
    paras = [re.sub(r"\s+", " ", p).strip() for p in re.split(r"\n\s*\n", text)]
    paras = [p for p in paras if len(p) > 1]

    chunks: list[str] = []
    buf = ""
    for para in paras:
        if buf and len(buf) + len(para) + 1 > CHUNK_CHARS:
            chunks.append(buf.strip())
            tail = buf[-CHUNK_OVERLAP:]
            # resume from a word boundary so the overlap reads cleanly
            buf = tail[tail.find(" ") + 1:] if " " in tail else ""
        buf = f"{buf} {para}".strip()
    if buf.strip():
        chunks.append(buf.strip())
    return [c for c in chunks if len(c) >= MIN_CHUNK_CHARS]


def load_sources() -> dict[str, dict]:
    if not SOURCES_JSON.exists():
        return {}
    return {r["slug"]: r for r in json.loads(SOURCES_JSON.read_text(encoding="utf-8"))}


def corpus_fingerprint() -> str:
    """A checkout-deterministic hash of the corpus, for staleness detection.

    sha256 over the sorted ``(filename, byte-size)`` pairs of ``CORPUS_DIR/*.txt``
    plus ``sources.json``'s byte-size. Names + sizes are identical on every fresh
    ``git checkout`` (unlike mtimes, which differ per machine), so the same corpus
    always yields the same fingerprint, and adding/removing/resizing any book (or
    editing the manifest) changes it — which is exactly the signal ``--if-missing``
    needs to know the on-disk index is stale.
    """
    h = hashlib.sha256()
    for path in sorted(CORPUS_DIR.glob("*.txt"), key=lambda p: p.name):
        h.update(path.name.encode("utf-8"))
        h.update(b"\0")
        h.update(str(path.stat().st_size).encode("ascii"))
        h.update(b"\n")
    sources_size = SOURCES_JSON.stat().st_size if SOURCES_JSON.exists() else 0
    h.update(f"sources.json\0{sources_size}\n".encode("ascii"))
    return h.hexdigest()


def build() -> int:
    # Lazy import: only needed for an actual build (keeps --if-missing skip cheap).
    from core.fitness_rag import VectorStore, embed, embed_model_name, index_dir

    sources = load_sources()
    files = sorted(CORPUS_DIR.glob("*.txt"))
    if not files:
        print(f"No corpus found in {CORPUS_DIR}.\n"
              "The corpus ships with the repo; if it is missing, regenerate it from the\n"
              "source PDFs (see data/fitness_library/SOURCES.txt):\n"
              "    python -m scripts.extract_literature_corpus --replace",
              file=sys.stderr)
        return 1

    chunks: list[dict] = []
    indexed_slugs: list[str] = []  # slugs that actually contributed ≥1 chunk
    for path in files:
        slug = path.stem
        meta = sources.get(slug, {})
        body = path.read_text(encoding="utf-8", errors="ignore").strip()
        pieces = chunk_text(body)
        if pieces:
            indexed_slugs.append(slug)
        for i, piece in enumerate(pieces):
            chunks.append({
                "id":        f"{slug}#{i}",
                "text":      piece,
                "title":     meta.get("title", slug),
                "author":    meta.get("author", "Unknown"),
                "source_id": meta.get("source_url"),
                "license":   meta.get("license", "unknown"),
            })
        print(f"  {slug}: {len(pieces)} chunks")

    print(f"\nEmbedding {len(chunks)} chunks with {embed_model_name()} …")
    vectors = embed([c["text"] for c in chunks])

    manifest = {
        "model":      embed_model_name(),
        "dim":        int(vectors.shape[1]) if vectors.size else 0,
        "count":      len(chunks),
        "normalized": True,
        "corpus_fingerprint": corpus_fingerprint(),
        # Derive the book list from the slugs actually indexed in the chunk loop
        # (not blindly from sources.json), so the manifest can never claim books
        # whose text never made it into the index.
        "books": [
            {"slug": s,
             "title": sources.get(s, {}).get("title", s),
             "author": sources.get(s, {}).get("author", "Unknown"),
             "source_url": sources.get(s, {}).get("source_url"),
             "license": sources.get(s, {}).get("license", "unknown")}
            for s in indexed_slugs
        ],
    }
    out = index_dir()
    VectorStore(vectors, chunks, manifest).save(out)
    print(f"✓ Vector index built: {len(chunks)} chunks, dim {manifest['dim']} → {out}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--if-missing", action="store_true",
                    help="skip the build when an index already exists")
    ap.add_argument("--rebuild", action="store_true", help="(alias) force a build")
    args = ap.parse_args()

    from core.fitness_rag import VectorStore, embed_model_name, index_dir
    if args.if_missing and VectorStore.exists(index_dir()):
        # Skip only when the on-disk index matches BOTH the current corpus and the
        # configured embedding model. The stored fingerprint lets every machine
        # self-heal: if books were added/changed since the index was built, the
        # fingerprints differ and we rebuild. The model check does the same for
        # FITNESS_EMBED_MODEL — vectors from two models are not comparable, so a
        # switched model must never be served from the old index.
        # Read manifest.json directly (not VectorStore.load) so the skip stays
        # cheap — no need to load the multi-MB vectors matrix just to compare.
        manifest_path = index_dir() / VectorStore.MANIFEST
        stored_fp = stored_model = None
        if manifest_path.exists():
            stored = json.loads(manifest_path.read_text(encoding="utf-8"))
            stored_fp = stored.get("corpus_fingerprint")
            stored_model = stored.get("model")
        want_model = embed_model_name()
        if stored_fp == corpus_fingerprint() and stored_model == want_model:
            print(f"✓ Fitness index already present at {index_dir()} "
                  f"(corpus + model unchanged) — skipping build.")
            return 0
        if stored_model and stored_model != want_model:
            print(f"embedding model changed ({stored_model} → {want_model}) — rebuilding index…")
        else:
            n_books = len(sorted(CORPUS_DIR.glob("*.txt")))
            print(f"corpus changed ({n_books} books) — rebuilding index…")

    return build()


if __name__ == "__main__":
    raise SystemExit(main())
