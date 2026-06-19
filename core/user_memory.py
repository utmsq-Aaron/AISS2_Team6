"""Per-user memory — a markdown "soul" + a private conversation vector store.

Each logged-in user (see ``api/auth.py``) gets a small, isolated memory on disk:

    data/user_memory/<user>/
        soul.md                  durable profile / preferences (human-readable, editable)
        conversations/           a private vector store of past Q&A turns
            vectors.npy          L2-normalised float32 (N, dim)
            chunks.json          [{id, ts, question, answer, text}, …]
            manifest.json        {model, dim, count}

Two complementary kinds of memory:

  * **soul.md** — slow-changing facts about the person (goals, constraints, tone).
    Read into the agent's context every turn; edited rarely (by hand or an API).
  * **conversation vectors** — every chat turn is embedded and appended, so the
    agent can *recall* the most relevant past turns for the current question.
    Retrieval is scoped to one user's directory — memories never cross users.

Embeddings reuse the project's existing local model via ``core.fitness_rag.embed``
(``sentence-transformers/all-MiniLM-L6-v2``, 384-dim, MPS/CPU — no embedding API)
and the same dependency-light :class:`~core.fitness_rag.VectorStore` (a numpy
matrix + JSON sidecar; cosine search is one matrix-vector product). Everything is
**best-effort**: if sentence-transformers/torch is missing or a write fails, memory
silently degrades and chat still works.

Config (live from ``.env``):
  USER_MEMORY_DIR     default ``data/user_memory``
  USER_MEMORY_TOPK    default 4 (past turns recalled per question)
  USER_MEMORY_ENABLED default 1 (set 0 to disable retrieval + writes)
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np

from core.llm import _env

_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_DIR = _ROOT / "data" / "user_memory"

# Keep the stored excerpts compact so a few recalled turns + the soul stay well
# within the agent's context budget.
_Q_CLIP = 400
_A_CLIP = 700


def _slug(user: str) -> str:
    """Filesystem-safe per-user directory name (also a guard against traversal)."""
    return re.sub(r"[^a-z0-9_-]+", "-", (user or "").strip().lower()).strip("-") or "anon"


def memory_root() -> Path:
    raw = _env("USER_MEMORY_DIR", "")
    return Path(raw) if raw else _DEFAULT_DIR


def memory_enabled() -> bool:
    return _env("USER_MEMORY_ENABLED", "1").strip().lower() not in ("0", "false", "no")


def _topk() -> int:
    try:
        return max(1, int(_env("USER_MEMORY_TOPK", "4")))
    except ValueError:
        return 4


_DEFAULT_SOUL = """\
# {name} — soul

A living profile of {name}. The training copilot reads this every conversation,
so keep it short, factual, and current. Edit freely.

## About
- (who they are, sport background, anything they want the assistant to know)

## Goals
- (current training goals / target events)

## Preferences
- (coaching tone, units, constraints, injuries to respect, scheduling quirks)

## Notes
- (anything else worth remembering long-term)
"""


class UserMemory:
    """One user's private memory: the soul file + a conversation vector store."""

    def __init__(self, user: str) -> None:
        self.user = user
        self.slug = _slug(user)
        self.dir = memory_root() / self.slug
        self.conv_dir = self.dir / "conversations"

    # ── soul (markdown profile) ───────────────────────────────────────────────

    @property
    def soul_path(self) -> Path:
        return self.dir / "soul.md"

    def ensure_soul(self) -> Path:
        """Create the soul file from a template if it doesn't exist yet."""
        if not self.soul_path.exists():
            self.dir.mkdir(parents=True, exist_ok=True)
            self.soul_path.write_text(
                _DEFAULT_SOUL.format(name=self.user), encoding="utf-8")
        return self.soul_path

    def read_soul(self) -> str:
        try:
            return self.soul_path.read_text(encoding="utf-8") if self.soul_path.exists() else ""
        except OSError:
            return ""

    def write_soul(self, content: str) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        self.soul_path.write_text(content, encoding="utf-8")

    # ── conversation vector store ─────────────────────────────────────────────

    def recall(self, query: str, k: Optional[int] = None) -> List[Dict[str, Any]]:
        """Most relevant past turns for ``query`` (cosine over this user's store)."""
        if not query.strip():
            return []
        try:
            from core.fitness_rag import VectorStore, embed
            if not VectorStore.exists(self.conv_dir):
                return []
            store = VectorStore.load(self.conv_dir)
            q = embed([query])[0]
            return store.search_vec(q, k=k or _topk())
        except Exception as exc:  # noqa: BLE001 — memory is best-effort
            print(f"[user_memory] recall skipped for {self.slug}: {exc}", flush=True)
            return []

    def remember(self, question: str, answer: str) -> bool:
        """Embed one Q&A turn and append it to this user's vector store."""
        question, answer = (question or "").strip(), (answer or "").strip()
        if not question or not answer:
            return False
        try:
            from core.fitness_rag import VectorStore, embed, embed_model_name
            q = question[:_Q_CLIP]
            a = answer[:_A_CLIP]
            text = f"Q: {q}\nA: {a}"
            vec = embed([text])  # (1, dim), already L2-normalised
            record = {
                "id": uuid.uuid4().hex[:12],
                "ts": datetime.utcnow().isoformat() + "Z",
                "question": q,
                "answer": a,
                "text": text,
            }
            if VectorStore.exists(self.conv_dir):
                store = VectorStore.load(self.conv_dir)
                store.vectors = np.vstack([store.vectors, vec]).astype("float32")
                store.chunks.append(record)
            else:
                store = VectorStore(vec.astype("float32"), [record], {})
            store.manifest.update({
                "model": embed_model_name(),
                "dim": int(store.vectors.shape[1]),
                "count": len(store.chunks),
                "user": self.user,
            })
            store.save(self.conv_dir)
            return True
        except Exception as exc:  # noqa: BLE001 — never let a write break chat
            print(f"[user_memory] remember skipped for {self.slug}: {exc}", flush=True)
            return False

    # ── context assembly (injected into the agent prompt) ─────────────────────

    def context_block(self, query: str) -> str:
        """A prompt preamble blending the soul + the turns most relevant to ``query``.

        Returns "" when there's nothing useful to add, so callers can prepend
        unconditionally.
        """
        if not memory_enabled():
            return ""
        sections: List[str] = []

        soul = self.read_soul().strip()
        if soul:
            sections.append("## User profile (soul.md)\n" + soul)

        hits = self.recall(query)
        if hits:
            lines = []
            for h in hits:
                ts = (h.get("ts") or "")[:10]
                lines.append(f"- [{ts}] Q: {h.get('question', '')}\n  A: {h.get('answer', '')}")
            sections.append("## Relevant past conversations\n" + "\n".join(lines))

        if not sections:
            return ""
        return (
            f"# Personal memory for {self.user}\n"
            "Background on the specific person you're talking to. Use it to personalise "
            "your answer when relevant; don't recite it back unless asked.\n\n"
            + "\n\n".join(sections)
        )


def get_user_memory(user: str) -> UserMemory:
    """Convenience constructor (cheap — heavy imports happen lazily on first use)."""
    return UserMemory(user)


# ── tiny CLI: python -m core.user_memory Marvin "how did I sleep?" ─────────────

if __name__ == "__main__":
    import sys

    who = sys.argv[1] if len(sys.argv) > 1 else "Marvin"
    query = " ".join(sys.argv[2:]) or "training advice"
    mem = get_user_memory(who)
    mem.ensure_soul()
    print(f"soul: {mem.soul_path}")
    print("\n--- context block ---\n")
    print(mem.context_block(query) or "(empty)")
