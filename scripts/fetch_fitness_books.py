"""Download the fitness-literature corpus for the Fitness RAG agent.

Source: **Project Gutenberg** (via the gutendex catalog API). Every title below is
in the **public domain**, so the corpus is freely redistributable and can live in
the repo — no scraping of copyrighted/pirated material. The curated list (~50 books)
spans physical culture & strength training, athletics and specific sports, the
physiology and anatomy of the human body, and personal/public hygiene & health.

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

# Curated public-domain corpus → (gutenberg_id, slug). ~50 titles spanning
# physical culture & strength training, athletics & specific sports (swimming,
# boxing, wrestling, fencing, football, golf, cycling, dancing), the physiology
# and anatomy of the human body, and personal/public hygiene, longevity & health.
BOOKS = [
    # ── original eight ─────────────────────────────────────────────────────
    (65987, "sandow-strength-and-how-to-obtain-it"),
    (13574, "camp-keeping-fit-all-the-way"),
    (56398, "james-practical-training-running-walking-rowing-boxing"),
    (70797, "personal-hygiene-and-physical-training-for-women"),
    (67114, "jensen-massage-and-exercises-combined"),
    (56134, "girls-and-athletics"),
    (49279, "woods-health-how-to-get-it-and-keep-it"),
    (65260, "the-physical-training-of-children"),
    # ── physical culture, strength & general training ──────────────────────
    (36557, "blaikie-how-to-get-strong-and-how-to-stay-so"),
    (19208, "macfadden-vitality-supreme"),
    (56584, "benson-miles-daily-training"),
    (22005, "how-to-add-ten-years-to-your-life"),
    (69080, "walkers-manly-exercises"),
    (47254, "handbook-of-summer-athletic-sports"),
    (12430, "a-lecture-on-physical-development"),
    (78009, "a-guide-to-the-history-of-physical-education"),
    (17727, "the-school-of-recreation"),
    # ── athletics & ancient sport ──────────────────────────────────────────
    (65554, "greek-athletics"),
    (59952, "greek-athletic-sports-and-festivals"),
    (64627, "athletics-and-games-of-the-ancient-greeks"),
    # ── physiology, anatomy & the human body ───────────────────────────────
    (53347, "foster-physiology"),
    (34211, "a-treatise-on-physiology-and-hygiene"),
    (30541, "a-treatise-on-anatomy-physiology-and-hygiene"),
    (6986, "steele-hygienic-physiology"),
    (15435, "object-lessons-on-the-human-body"),
    (72451, "combe-physiology-of-digestion"),
    (56427, "animal-locomotion-walking-swimming-flying"),
    (63456, "andreas-vesalius-reformer-of-anatomy"),
    # ── specific sports & activities ───────────────────────────────────────
    (19065, "swimming-scientifically-taught"),
    (12135, "the-art-of-fencing"),
    (37562, "wrestling-and-wrestlers"),
    (64111, "pugilistica-history-of-british-boxing"),
    (39743, "camp-american-football"),
    (35683, "association-football-and-how-to-play-it"),
    (28107, "the-complete-golfer"),
    (18048, "football-days"),
    (13749, "around-the-world-on-a-bicycle-vol-2"),
    (78268, "dancing-beauty-and-games"),
    (66449, "modern-dancing-and-dancers"),
    (12926, "the-morris-book-part-1"),
    # ── hygiene, longevity & health ────────────────────────────────────────
    (58591, "hygiene-personal-and-public-health"),
    (19598, "fisher-how-to-live"),
    (21353, "civics-and-health"),
    (9173, "youth-education-regimen-and-hygiene"),
    (51521, "metchnikoff-the-prolongation-of-life"),
    (64237, "old-age-deferred"),
    (32250, "red-cross-home-hygiene-and-care-of-the-sick"),
    (17682, "the-healthy-life-magazine"),
    (4339, "nerves-and-common-sense"),
    (5694, "harvard-classics-v38-scientific-papers-physiology"),
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
