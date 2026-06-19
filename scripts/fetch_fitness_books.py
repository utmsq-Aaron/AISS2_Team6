"""Download the fitness-literature corpus for the Fitness RAG agent.

Source: **Project Gutenberg** (via the gutendex catalog API). Every title below is
in the **public domain**, so the corpus is freely redistributable and can live in
the repo — no scraping of copyrighted/pirated material. The curated list spans
strength training, endurance/athletic conditioning, women's physical training,
physical-culture/massage and general exercise & health.

    python -m scripts.fetch_fitness_books          # download missing books
    python -m scripts.fetch_fitness_books --force   # re-download all

Output:
    data/fitness_library/corpus/<slug>.txt   one cleaned plain-text book each
    data/fitness_library/sources.json        manifest (title, author, gutenberg id, license)
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
CORPUS_DIR = ROOT / "data" / "fitness_library" / "corpus"
SOURCES_JSON = ROOT / "data" / "fitness_library" / "sources.json"

GUTENDEX = "https://gutendex.com/books/{id}"

# Curated public-domain fitness / physical-culture books → (gutenberg_id, slug).
BOOKS = [
    (65987, "sandow-strength-and-how-to-obtain-it"),
    (13574, "camp-keeping-fit-all-the-way"),
    (56398, "james-practical-training-running-walking-rowing-boxing"),
    (70797, "personal-hygiene-and-physical-training-for-women"),
    (67114, "jensen-massage-and-exercises-combined"),
    (56134, "girls-and-athletics"),
    (49279, "woods-health-how-to-get-it-and-keep-it"),
    (65260, "the-physical-training-of-children"),
]


def _clean_title(title: str) -> str:
    """Drop Gutenberg's MARC ``$b`` subtitle delimiter and tidy separators."""
    title = re.sub(r"\s*:?\s*\$b\s*", ": ", title)
    return re.sub(r"\s+", " ", title).strip()


def _pick_text_url(formats: dict) -> str | None:
    """Prefer a UTF-8 plain-text URL; fall back to any non-zip text/plain."""
    candidates = [(k, u) for k, u in formats.items()
                  if k.startswith("text/plain") and not u.endswith(".zip")]
    for k, u in candidates:
        if "utf-8" in k.lower():
            return u
    return candidates[0][1] if candidates else None


def fetch_one(gid: int, slug: str, *, force: bool) -> dict | None:
    out = CORPUS_DIR / f"{slug}.txt"
    meta = requests.get(GUTENDEX.format(id=gid), timeout=30).json()
    title = _clean_title(meta.get("title", slug))
    authors = ", ".join(a.get("name", "") for a in meta.get("authors", [])) or "Unknown"
    record = {
        "gutenberg_id": gid,
        "slug": slug,
        "title": title,
        "author": authors,
        "license": "Public domain (Project Gutenberg)",
        "source_url": f"https://www.gutenberg.org/ebooks/{gid}",
        "file": f"corpus/{slug}.txt",
    }
    if out.exists() and not force:
        print(f"  ✓ {slug} (exists, {out.stat().st_size // 1024} KB)")
        return record
    url = _pick_text_url(meta.get("formats", {}))
    if not url:
        print(f"  ✗ {slug}: no plain-text format on Gutenberg", file=sys.stderr)
        return None
    text = requests.get(url, timeout=60).text
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    print(f"  ↓ {slug}: {len(text) // 1024} KB  ←  {title[:50]}")
    return record


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--force", action="store_true", help="re-download even if present")
    args = ap.parse_args()

    CORPUS_DIR.mkdir(parents=True, exist_ok=True)
    print(f"Fetching {len(BOOKS)} public-domain fitness books → {CORPUS_DIR}")
    records = []
    for gid, slug in BOOKS:
        try:
            rec = fetch_one(gid, slug, force=args.force)
            if rec:
                records.append(rec)
        except Exception as exc:  # noqa: BLE001 — keep going, report at the end
            print(f"  ✗ {slug}: {type(exc).__name__}: {exc}", file=sys.stderr)

    SOURCES_JSON.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n{len(records)}/{len(BOOKS)} books available. Manifest → {SOURCES_JSON}")
    return 0 if records else 1


if __name__ == "__main__":
    raise SystemExit(main())
