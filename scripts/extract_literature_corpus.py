"""Extract the German sport-science textbooks (PDFs) into the RAG corpus.

Drops in ``data/fitness_library/Literatur_Fitness/*.pdf`` are born-digital
textbooks (not scans), so we pull their text with PyMuPDF, clean it (soft
hyphens, hyphenated line breaks, exotic whitespace) and emit one
``corpus/<slug>.txt`` per book — paragraph-separated so the existing chunker
in ``scripts/build_fitness_index.py`` splits them sensibly.

    python -m scripts.extract_literature_corpus            # extract + refresh sources.json
    python -m scripts.extract_literature_corpus --replace  # first wipe the old corpus/*.txt

Then rebuild the vector index:

    python -m scripts.build_fitness_index --rebuild

NOTE: these books are copyrighted (Springer et al.). The extracted text and the
built index are for *local* RAG use only — the index dir is gitignored; do not
commit the corpus text or redistribute it.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LIT_DIR = ROOT / "data" / "fitness_library" / "Literatur_Fitness"
CORPUS_DIR = ROOT / "data" / "fitness_library" / "corpus"
SOURCES_JSON = ROOT / "data" / "fitness_library" / "sources.json"

# pdf-filename → bibliographic metadata + target slug.
# The titles stay in the original German on purpose: they are citations of German
# works (they end up in sources.json and in the RAG's source attribution), not UI
# text. Translating them would misquote the literature.
BOOKS = [
    {
        "pdf": "978-3-662-69524-1.pdf",
        "slug": "ferrauti-trainingswissenschaft-sportpraxis",
        "title": "Trainingswissenschaft für die Sportpraxis. Lehrbuch für Studium, "
                 "Ausbildung und Unterricht im Sport",
        "author": "Ferrauti, Alexander (ed.)",
        "license": "© Springer — copyrighted (local RAG use only)",
        "source_url": "https://doi.org/10.1007/978-3-662-69524-1",
    },
    {
        "pdf": "978-3-662-53410-6.pdf",
        "slug": "guellich-krueger-handbuch-sport-sportwissenschaft",
        "title": "Handbuch Sport und Sportwissenschaft — Bewegung, Training, "
                 "Leistung und Gesundheit",
        "author": "Güllich, Arne; Krüger, Michael (ed.)",
        "license": "© Springer — copyrighted (local RAG use only)",
        "source_url": "https://doi.org/10.1007/978-3-662-53410-6",
    },
    {
        "pdf": "978-3-662-68974-5.pdf",
        "slug": "koenig-carlsohn-praxis-sporternaehrung",
        "title": "Praxis der Sporternährung — Ein Leitfaden für Studierende und "
                 "Fachkräfte der Ernährungs- und Sportwissenschaften",
        "author": "König, Daniel; Carlsohn, Anja (ed.)",
        "license": "© Springer — copyrighted (local RAG use only)",
        "source_url": "https://doi.org/10.1007/978-3-662-68974-5",
    },
    {
        "pdf": "978-3-658-29154-9.pdf",
        "slug": "dransmann-hiit-vs-dauermethode",
        "title": "Hochintensives Intervalltraining vs. extensive Dauermethode — "
                 "Feldstudie zum ausdauernden Laufen im Sportunterricht",
        "author": "Dransmann, Milan",
        "license": "© Springer — copyrighted (local RAG use only)",
        "source_url": "https://doi.org/10.1007/978-3-658-29154-9",
    },
    {
        "pdf": "Engel_Florian.pdf",
        "slug": "engel-hiit-nachwuchsleistungssport",
        "title": "Physiologische Reaktionen auf hochintensives Intervalltraining bei "
                 "Nachwuchsleistungssportlern und erwachsenen Athleten",
        "author": "Engel, Florian",
        "license": "Dissertation (copyrighted, local RAG use only)",
        "source_url": "",
    },
]

# soft hyphen + zero-width / exotic spaces that survive block extraction.
_SOFT_HYPHEN = "­"
_DEHYPHEN = re.compile(r"(?<=\w)-\s*\n\s*(?=\w)")   # "Trainings-\nwissenschaft" → join
_WS = re.compile(r"[ \t  -   ﻿]+")


def clean_block(text: str) -> str:
    """A single PyMuPDF text block → one clean paragraph line."""
    text = text.replace(_SOFT_HYPHEN, "")
    text = _DEHYPHEN.sub("", text)          # rejoin words split across lines
    text = text.replace("\n", " ")           # remaining newlines are wraps
    text = _WS.sub(" ", text)
    return text.strip()


def extract(pdf_path: Path) -> str:
    # Lazy so --help stays cheap. `pymupdf` is the current module name; the old
    # `fitz` alias still works but warns on import.
    import pymupdf

    doc = pymupdf.open(pdf_path)
    paras: list[str] = []
    for page in doc:
        for block in page.get_text("blocks"):
            para = clean_block(block[4])
            # keep only blocks with real prose (drops page numbers, stray glyphs)
            if len(para) >= 40:
                paras.append(para)
    doc.close()
    # blank line between paragraphs → the chunker's paragraph splitter works
    return "\n\n".join(paras)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--replace", action="store_true",
                    help="delete the existing corpus/*.txt before extracting")
    args = ap.parse_args()

    if not LIT_DIR.exists():
        print(f"No literature dir at {LIT_DIR}", file=sys.stderr)
        return 1

    CORPUS_DIR.mkdir(parents=True, exist_ok=True)
    if args.replace:
        old = sorted(CORPUS_DIR.glob("*.txt"))
        for p in old:
            p.unlink()
        print(f"Removed {len(old)} old corpus files.")

    sources: list[dict] = []
    for book in BOOKS:
        pdf = LIT_DIR / book["pdf"]
        if not pdf.exists():
            print(f"  ! missing {pdf.name} — skipping", file=sys.stderr)
            continue
        body = extract(pdf)
        out = CORPUS_DIR / f"{book['slug']}.txt"
        out.write_text(body, encoding="utf-8")
        print(f"  {book['slug']}: {len(body):,} chars → {out.name}")
        sources.append({
            "slug": book["slug"],
            "title": book["title"],
            "author": book["author"],
            "license": book["license"],
            "source_url": book["source_url"],
            "file": f"corpus/{book['slug']}.txt",
        })

    SOURCES_JSON.write_text(
        json.dumps(sources, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n✓ Wrote {len(sources)} entries to {SOURCES_JSON.name}")
    print("Next: python -m scripts.build_fitness_index --rebuild")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
