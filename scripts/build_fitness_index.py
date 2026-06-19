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


def build() -> int:
    # Lazy import: only needed for an actual build (keeps --if-missing skip cheap).
    from core.fitness_rag import VectorStore, embed, embed_model_name, index_dir

    sources = load_sources()
    files = sorted(CORPUS_DIR.glob("*.txt"))
    if not files:
        print("No corpus found. Run: python -m scripts.fetch_fitness_books", file=sys.stderr)
        return 1

    chunks: list[dict] = []
    for path in files:
        slug = path.stem
        meta = sources.get(slug, {})
        body = strip_boilerplate(path.read_text(encoding="utf-8", errors="ignore"))
        pieces = chunk_text(body)
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
        "books": [
            {"slug": s, "title": r.get("title"), "author": r.get("author"),
             "gutenberg_id": r.get("gutenberg_id"), "license": r.get("license")}
            for s, r in sources.items()
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
        print(f"✓ Fitness index already present at {index_dir()} — skipping build.")
        return 0

    # Auto-fetch the corpus if it's missing (e.g. a fresh checkout without it).
    if not any(CORPUS_DIR.glob("*.txt")):
        print("Corpus missing — fetching books first …")
        from scripts.fetch_fitness_books import main as fetch_main
        if fetch_main() != 0:
            return 1

    return build()


if __name__ == "__main__":
    raise SystemExit(main())
