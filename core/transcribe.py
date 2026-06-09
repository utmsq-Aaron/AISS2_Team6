"""Local speech-to-text via Whisper — multilingual (German + English), Apple-Silicon aware.

Picks a backend lazily on first use and falls back so it still runs on any OS:

  1. mlx-whisper     — native Apple Silicon (MLX/Metal), fastest on a Mac.
                       Needs ``ffmpeg`` on PATH (``brew install ffmpeg``).
  2. faster-whisper  — CTranslate2 int8 on CPU. Portable (Linux/Win/Mac); decodes
                       audio via PyAV, so no system ffmpeg is required.
  3. openai-whisper  — reference PyTorch implementation (last resort).

Streamlit-free and import-light: the heavy deps are imported lazily and the model
is loaded once and cached. The model auto-downloads on first use.

Env:
  WHISPER_BACKEND  force one of: mlx | faster | openai     (default: auto)
  WHISPER_MODEL    size/id: tiny|base|small|medium|large-v3 (default: "small";
                   bigger = better German, slower / more RAM). For mlx a bare size
                   maps to the matching ``mlx-community`` repo; an explicit HF repo
                   (contains "/") is used as-is.
  WHISPER_LANGUAGE force a language e.g. "de" / "en"        (default: auto-detect)
"""

from __future__ import annotations

import importlib.util
import logging
import os
import platform
import shutil
from typing import Dict, List, Optional

log = logging.getLogger("fitdash.transcribe")

# Bare size → mlx-community repo (mlx can't take faster-whisper's size names).
_MLX_REPO = {
    "tiny": "mlx-community/whisper-tiny-mlx",
    "base": "mlx-community/whisper-base-mlx",
    "small": "mlx-community/whisper-small-mlx",
    "medium": "mlx-community/whisper-medium-mlx",
    "large": "mlx-community/whisper-large-v3-mlx",
    "large-v2": "mlx-community/whisper-large-v2-mlx",
    "large-v3": "mlx-community/whisper-large-v3-mlx",
    "turbo": "mlx-community/whisper-large-v3-turbo",
    "large-v3-turbo": "mlx-community/whisper-large-v3-turbo",
}
_MODULE = {"mlx": "mlx_whisper", "faster": "faster_whisper", "openai": "whisper"}

# One loaded model per process.
_cache: Dict[str, Optional[object]] = {"backend": None, "model_id": None, "model": None}


def transcribe(audio_path: str, language: Optional[str] = None) -> Dict[str, str]:
    """Transcribe an audio file → ``{"text", "language", "backend", "model"}``.

    Language is auto-detected unless given (or ``WHISPER_LANGUAGE`` is set). Tries the
    best available backend and falls back to the next one on failure.
    """
    lang = language or (os.getenv("WHISPER_LANGUAGE") or None)
    errors: List[str] = []
    for backend in _candidates():
        try:
            model_id = _load(backend)
            return _run(backend, model_id, audio_path, lang)
        except Exception as exc:  # noqa: BLE001 — try the next backend
            log.warning("Whisper backend %r failed: %s", backend, exc)
            errors.append(f"{backend}: {exc}")
            _cache.update({"backend": None, "model_id": None, "model": None})
    raise RuntimeError("All Whisper backends failed:\n" + "\n".join(errors))


def available_backend() -> Optional[str]:
    """The backend that would be used right now, or None if none is installed."""
    try:
        return _candidates()[0]
    except RuntimeError:
        return None


# ── internals ────────────────────────────────────────────────────────────────────

def _installed(module: str) -> bool:
    return importlib.util.find_spec(module) is not None


def _candidates() -> List[str]:
    """Ordered backend candidates from env + platform + what's importable."""
    forced = (os.getenv("WHISPER_BACKEND") or "").strip().lower()
    if forced in _MODULE:
        if not _installed(_MODULE[forced]):
            raise RuntimeError(f"WHISPER_BACKEND={forced} but its package isn't installed.")
        return [forced]
    apple_silicon = platform.system() == "Darwin" and platform.machine() == "arm64"
    order = ["mlx", "faster", "openai"] if apple_silicon else ["faster", "openai", "mlx"]
    has_ffmpeg = shutil.which("ffmpeg") is not None
    avail: List[str] = []
    for b in order:
        if not _installed(_MODULE[b]):
            continue
        # mlx + openai decode audio via ffmpeg; in auto mode skip them when it's
        # missing so we don't fail-then-fall-back on every clip (faster-whisper
        # uses PyAV and needs no system ffmpeg). A forced backend is honoured above.
        if b in ("mlx", "openai") and not has_ffmpeg:
            continue
        avail.append(b)
    if not avail:
        hint = ""
        if not has_ffmpeg and (_installed("mlx_whisper") or _installed("whisper")):
            hint = "\n(mlx/openai-whisper are installed but need ffmpeg: brew install ffmpeg)"
        raise RuntimeError(
            "No usable Whisper backend. Install one:\n"
            "  pip install faster-whisper   # portable, any OS, no ffmpeg needed\n"
            "  pip install mlx-whisper       # Apple Silicon (also: brew install ffmpeg)" + hint
        )
    return avail


def _resolve_model_id(backend: str) -> str:
    raw = (os.getenv("WHISPER_MODEL") or "small").strip()
    if backend == "mlx" and "/" not in raw:
        return _MLX_REPO.get(raw, _MLX_REPO["small"])
    return raw


def _load(backend: str) -> str:
    """Load (and cache) the model for ``backend``; return the resolved model id."""
    model_id = _resolve_model_id(backend)
    ready = _cache["backend"] == backend and _cache["model_id"] == model_id and (
        _cache["model"] is not None or backend == "mlx"
    )
    if ready:
        return model_id

    log.info("loading Whisper: backend=%s model=%s (first use may download)…", backend, model_id)
    if backend == "faster":
        from faster_whisper import WhisperModel
        _cache["model"] = WhisperModel(model_id, device="cpu", compute_type="int8")
    elif backend == "openai":
        import whisper
        _cache["model"] = whisper.load_model(model_id)
    elif backend == "mlx":
        import mlx_whisper  # noqa: F401 — used at call time, keyed by repo id
        _cache["model"] = None
    _cache["backend"] = backend
    _cache["model_id"] = model_id
    return model_id


def _run(backend: str, model_id: str, audio_path: str, language: Optional[str]) -> Dict[str, str]:
    if backend == "faster":
        segments, info = _cache["model"].transcribe(audio_path, language=language, vad_filter=True)
        text = "".join(s.text for s in segments).strip()
        return {"text": text, "language": getattr(info, "language", "") or (language or ""),
                "backend": "faster-whisper", "model": model_id}
    if backend == "openai":
        res = _cache["model"].transcribe(audio_path, language=language)
        return {"text": (res.get("text") or "").strip(), "language": res.get("language", "") or (language or ""),
                "backend": "openai-whisper", "model": model_id}
    if backend == "mlx":
        import mlx_whisper
        res = mlx_whisper.transcribe(audio_path, path_or_hf_repo=model_id, language=language)
        return {"text": (res.get("text") or "").strip(), "language": res.get("language", "") or (language or ""),
                "backend": "mlx-whisper", "model": model_id}
    raise RuntimeError(f"unknown backend {backend!r}")
