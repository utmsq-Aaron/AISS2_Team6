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
import threading
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


def _soul_every() -> int:
    """Refresh the soul once this many new turns have accumulated since the last."""
    try:
        return max(1, int(_env("USER_MEMORY_SOUL_EVERY", "3")))
    except ValueError:
        return 3


# Per-user lock so two overlapping refreshes can't clobber one soul.md.
_soul_locks: Dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def _soul_lock(slug: str) -> threading.Lock:
    with _locks_guard:
        lock = _soul_locks.get(slug)
        if lock is None:
            lock = _soul_locks[slug] = threading.Lock()
        return lock


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

_SOUL_SYSTEM = """\
You maintain a concise, durable PROFILE ("soul") of one user of a sports-training
assistant. You are given the user's CURRENT profile (markdown) and the most RECENT
conversation turns (the user's questions + the assistant's answers). Produce an
UPDATED profile that folds in any new, durable facts about this person.

Rules:
- Keep the existing markdown structure and headings (About / Goals / Preferences /
  Notes). Add bullets under the right heading; revise stale ones.
- Record ONLY durable, long-term facts: training goals, target events, sport
  background, recurring preferences (units, coaching tone), constraints, injuries,
  schedule patterns. The kind of thing worth remembering across many sessions.
- IGNORE one-off questions and transient data values (today's heart rate, a single
  day's weather, a specific date's workout). Those live in the conversation log,
  not the profile.
- Never invent or infer beyond what the conversation supports. If nothing new is
  durable, return the profile essentially unchanged.
- Replace the placeholder "(…)" template hints with real content once you have it;
  if a section still has nothing real, leave its placeholder.
- Be concise. Output ONLY the profile markdown — no preamble, no commentary, no
  code fences.
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
            meta = self._read_meta()
            meta["turns"] = int(meta.get("turns", 0)) + 1
            self._write_meta(meta)
            return True
        except Exception as exc:  # noqa: BLE001 — never let a write break chat
            print(f"[user_memory] remember skipped for {self.slug}: {exc}", flush=True)
            return False

    # ── self-updating soul (LLM distillation of recent turns) ─────────────────

    @property
    def meta_path(self) -> Path:
        return self.dir / "meta.json"

    def _read_meta(self) -> Dict[str, Any]:
        try:
            return json.loads(self.meta_path.read_text(encoding="utf-8")) if self.meta_path.exists() else {}
        except (OSError, ValueError):
            return {}

    def _write_meta(self, meta: Dict[str, Any]) -> None:
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
            self.meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            pass

    def _recent_turns(self, limit: int) -> List[Dict[str, Any]]:
        """The newest ``limit`` stored Q&A turns (chunks are appended in order)."""
        try:
            from core.fitness_rag import VectorStore
            if not VectorStore.exists(self.conv_dir):
                return []
            chunks = VectorStore.load(self.conv_dir).chunks
            return chunks[-limit:]
        except Exception:  # noqa: BLE001
            return []

    def soul_update_due(self) -> bool:
        meta = self._read_meta()
        pending = int(meta.get("turns", 0)) - int(meta.get("last_soul_update_turn", 0))
        return pending >= _soul_every()

    def maybe_refresh_soul(self) -> bool:
        """Refresh the soul iff enough new turns have accrued. Best-effort, safe to
        call after every turn (it no-ops until the threshold is reached)."""
        if not memory_enabled() or not self.soul_update_due():
            return False
        return self.refresh_soul()

    def refresh_soul(self, max_turns: int = 12) -> bool:
        """Distill recent conversation into the soul via the LLM. Never raises."""
        lock = _soul_lock(self.slug)
        if not lock.acquire(blocking=False):
            return False  # a refresh for this user is already running
        try:
            turns = self._recent_turns(max_turns)
            if not turns:
                return False
            current = self.read_soul().strip() or _DEFAULT_SOUL.format(name=self.user)
            updated = _distill_soul(self.user, current, turns)
            meta = self._read_meta()
            meta["last_soul_update_turn"] = int(meta.get("turns", 0))
            if updated and updated.strip() != current.strip():
                self.write_soul(updated)
                meta["soul_updated_ts"] = datetime.utcnow().isoformat() + "Z"
                self._write_meta(meta)
                print(f"[user_memory] soul refreshed for {self.slug}", flush=True)
                return True
            # Still advance the counter so we don't retry the same turns every turn.
            self._write_meta(meta)
            return False
        except Exception as exc:  # noqa: BLE001
            print(f"[user_memory] soul refresh skipped for {self.slug}: {exc}", flush=True)
            return False
        finally:
            lock.release()

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


_FENCE_RE = re.compile(r"^```[a-zA-Z]*\n(.*)\n```$", re.S)


def _strip_fence(text: str) -> str:
    m = _FENCE_RE.match(text.strip())
    return m.group(1).strip() if m else text.strip()


def _distill_soul(user: str, current_soul: str, turns: List[Dict[str, Any]]) -> str:
    """Ask the LLM to merge recent turns into the profile. Returns "" on any failure."""
    try:
        from langchain_core.messages import HumanMessage, SystemMessage
        from core.llm import get_chat_model
    except Exception as exc:  # noqa: BLE001 — langchain/llm seam unavailable
        print(f"[user_memory] soul distill unavailable: {exc}", flush=True)
        return ""

    convo = "\n\n".join(
        f"[{(t.get('ts') or '')[:10]}] Q: {t.get('question', '')}\nA: {t.get('answer', '')}"
        for t in turns
    )
    human = (
        f"CURRENT PROFILE:\n{current_soul}\n\n"
        f"RECENT CONVERSATIONS (oldest first):\n{convo}\n\n"
        "Return the updated profile markdown only."
    )
    try:
        resp = get_chat_model().invoke(
            [SystemMessage(content=_SOUL_SYSTEM), HumanMessage(content=human)]
        )
        text = getattr(resp, "content", None) or str(resp)
    except Exception as exc:  # noqa: BLE001 — gateway error / timeout
        print(f"[user_memory] soul distill LLM call failed: {exc}", flush=True)
        return ""

    text = _strip_fence(text if isinstance(text, str) else str(text))
    # Sanity bounds: reject empty/degenerate or runaway output, keep the old soul.
    if len(text) < 20 or len(text) > 12000 or "#" not in text:
        return ""
    return text


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
