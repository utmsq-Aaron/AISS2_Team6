"""Build the Fitness RAG vector index from the downloaded corpus.

Pipeline:  corpus/*.txt → strip Gutenberg boilerplate → chunk → embed (local
model) → save a normalised vector matrix + chunk sidecar to the index dir.

    python -m scripts.build_fitness_index               # build (fetches corpus if absent)
    python -m scripts.build_fitness_index --if-missing  # no-op when an index already exists
    python -m scripts.build_fitness_index --rebuild     # force a clean rebuild

The ``--if-missing`` form is what the launch scripts call: the first run downloads
the embedding model (~90 MB) and embeds the corpus; later runs skip instantly.
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

# Gutenberg wraps each book in legal boilerplate between these markers.
_START_RE = re.compile(r"\*\*\*\s*START OF (?:THE|THIS) PROJECT GUTENBERG.*?\*\*\*", re.I | re.S)
_END_RE = re.compile(r"\*\*\*\s*END OF (?:THE|THIS) PROJECT GUTENBERG.*?\*\*\*", re.I | re.S)

CHUNK_CHARS = 900       # target chunk size (characters)
CHUNK_OVERLAP = 150     # carried-over context between adjacent chunks
MIN_CHUNK_CHARS = 200   # drop slivers shorter than this


def strip_boilerplate(text: str) -> str:
    """Keep only the body between the START/END Gutenberg markers."""
    start = _START_RE.search(text)
    if start:
        text = text[start.end():]
    end = _END_RE.search(text)
    if end:
        text = text[:end.start()]
    return text.strip()


def chunk_text(text: str) -> list[str]:
    """Paragraph-aware splitter: pack paragraphs to ~CHUNK_CHARS with overlap.

    Whitespace is normalised so embeddings see clean prose, not Gutenberg's
    hard-wrapped 70-column lines.
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
        print("No corpus found. Run: python -m scripts.fetch_fitness_books", file=sys.stderr)
        return 1

    chunks: list[dict] = []
    indexed_slugs: list[str] = []  # slugs that actually contributed ≥1 chunk
    for path in files:
        slug = path.stem
        meta = sources.get(slug, {})
        body = strip_boilerplate(path.read_text(encoding="utf-8", errors="ignore"))
        pieces = chunk_text(body)
        if pieces:
            indexed_slugs.append(slug)
        for i, piece in enumerate(pieces):
            chunks.append({
                "id":        f"{slug}#{i}",
                "text":      piece,
                "title":     meta.get("title", slug),
                "author":    meta.get("author", "Unknown"),
                "source_id": meta.get("gutenberg_id"),
                "license":   meta.get("license", "Public domain (Project Gutenberg)"),
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
             "gutenberg_id": sources.get(s, {}).get("gutenberg_id"),
             "license": sources.get(s, {}).get("license", "Public domain (Project Gutenberg)")}
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

    from core.fitness_rag import VectorStore, index_dir
    if args.if_missing and VectorStore.exists(index_dir()):
        # Skip only when the on-disk index matches the current corpus. The stored
        # fingerprint lets every machine self-heal: if books were added/changed
        # since the index was built, the fingerprints differ and we rebuild.
        # Read manifest.json directly (not VectorStore.load) so the skip stays
        # cheap — no need to load the multi-MB vectors matrix just to compare.
        manifest_path = index_dir() / VectorStore.MANIFEST
        stored_fp = None
        if manifest_path.exists():
            stored_fp = json.loads(
                manifest_path.read_text(encoding="utf-8")).get("corpus_fingerprint")
        current_fp = corpus_fingerprint()
        if stored_fp == current_fp:
            print(f"✓ Fitness index already present at {index_dir()} "
                  f"(corpus unchanged) — skipping build.")
            return 0
        n_books = len(sorted(CORPUS_DIR.glob("*.txt")))
        print(f"corpus changed ({n_books} books) — rebuilding index…")

    # Auto-fetch the corpus if it's missing (e.g. a fresh checkout without it).
    if not any(CORPUS_DIR.glob("*.txt")):
        print("Corpus missing — fetching books first …")
        from scripts.fetch_fitness_books import main as fetch_main
        if fetch_main() != 0:
            return 1

    return build()


if __name__ == "__main__":
    raise SystemExit(main())
