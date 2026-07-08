"""Shared cross-process JSON persistence helpers.

Several per-user stores under ``data/`` are written by MORE THAN ONE process — the
Coach chat (``core.chat_store``) and the goals list (``core.goal_store``) are both
touched by FastAPI AND the Telegram bridge. A per-process ``threading.Lock`` is
therefore not enough. These helpers give every such store the same safety:

  * :func:`flock` — a best-effort cross-process advisory lock (``fcntl``) keyed by a
    sibling ``<path>.lock`` file; a no-op on non-Unix (the caller's threading lock
    still applies).
  * :func:`atomic_write` — write via a temp file + ``os.replace`` so a concurrent
    reader never sees a half-written file (a torn read would parse as ``None``).
  * :func:`read_json` — best-effort read → object or ``None``.
  * :func:`slugify` — filesystem-safe, traversal-proof per-user directory name.

The invariant for a multi-writer store: hold :func:`flock` around a re-read →
mutate → :func:`atomic_write` cycle.
"""

from __future__ import annotations

import contextlib
import json
import re
import uuid
from pathlib import Path
from typing import Any, Optional

try:
    import fcntl  # Unix (macOS/Linux) — cross-process advisory file lock
except ImportError:  # pragma: no cover — non-Unix falls back to the threading lock
    fcntl = None  # type: ignore


def slugify(user: str) -> str:
    """Filesystem-safe per-user directory name (also guards against traversal)."""
    return re.sub(r"[^a-z0-9_-]+", "-", (user or "").strip().lower()).strip("-") or "anon"


@contextlib.contextmanager
def flock(path: Path):
    """Best-effort cross-process exclusive lock keyed by ``<path>.lock``.

    A no-op on non-Unix (no ``fcntl``) — the caller's ``threading.Lock`` still
    applies. Body errors propagate; the lock is always released.
    """
    if fcntl is None:
        yield
        return
    p = Path(path)
    lock_path = p.with_suffix(p.suffix + ".lock")
    fh = None
    try:  # acquire — any failure degrades to the caller's threading lock alone
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        fh = open(lock_path, "w")
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
    except OSError:
        if fh is not None:
            fh.close()
            fh = None
    try:  # run the guarded body exactly once; release in finally
        yield
    finally:
        if fh is not None:
            try:
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
            finally:
                fh.close()


def read_json(path: Path) -> Optional[Any]:
    """Parse a JSON file → object, or ``None`` on any error (missing/torn/invalid)."""
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def atomic_write(path: Path, obj: Any) -> None:
    """Write ``obj`` as pretty JSON atomically (temp file + ``os.replace``).

    A concurrent reader (which need not hold the lock) sees either the old bytes or
    the new bytes, never a torn mix.
    """
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + f".tmp-{uuid.uuid4().hex[:8]}")
    try:
        tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(p)  # os.replace — atomic on POSIX
    except OSError:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise
