"""Speech-to-text with faster-whisper (CPU, int8)."""

import logging
import threading
import time
from pathlib import Path
from typing import Callable, Optional

from faster_whisper import WhisperModel

from . import config

_LOGGER = logging.getLogger(__name__)
_models: dict[str, WhisperModel] = {}
_load_lock = threading.Lock()
_run_lock = threading.Lock()  # one transcription at a time: whisper already uses every core


def get_model(size: str) -> WhisperModel:
    if size not in config.WHISPER_MODELS or size == "auto":
        raise ValueError(f"Whisper model must be one of {config.WHISPER_MODELS}")
    with _load_lock:
        if size not in _models:
            if len(_models) >= 2:  # base + small fit easily; drop the rest
                _models.pop(next(iter(_models)))
            started = time.monotonic()
            _models[size] = WhisperModel(
                size, device="cpu", compute_type="int8",
                cpu_threads=config.WHISPER_THREADS, download_root=str(config.MODELS_DIR),
            )
            _LOGGER.info("Loaded whisper %s in %.1fs", size, time.monotonic() - started)
        return _models[size]


def _pick_model(wav_path: Path, language: Optional[str], translate: bool) -> tuple[str, Optional[str]]:
    """Auto mode: base is fast and fine for English; small is far better for other languages."""
    if translate:
        return "small", language
    if language:
        return ("base" if language == "en" else "small"), language
    # transcribe() detects the language eagerly and decodes lazily, so this is cheap.
    _, info = get_model("base").transcribe(str(wav_path), beam_size=1, vad_filter=True)
    if info.language == "en":
        return "base", "en"
    return "small", (info.language if info.language_probability >= 0.7 else None)


def _detect_among(wav_path: Path, candidates: list[str]) -> str:
    """Language ID restricted to the languages the caller expects (short clips fool open detection)."""
    from faster_whisper import decode_audio

    _, _, probs = get_model("base").detect_language(audio=decode_audio(str(wav_path), sampling_rate=16000),
                                                    vad_filter=True)
    scores = dict(probs)
    return max(candidates, key=lambda c: scores.get(c, 0.0))


def transcribe(wav_path: Path, *, model: str, language: Optional[str] = None, translate: bool = False,
               progress: Optional[Callable[[float, str], None]] = None,
               candidates: Optional[list[str]] = None) -> dict:
    with _run_lock:
        if not language and candidates:
            language = candidates[0] if len(candidates) == 1 else _detect_among(wav_path, candidates)
        if model == "auto":
            model, language = _pick_model(wav_path, language, translate)
        whisper = get_model(model)
        if progress:
            progress(0.05, f"Transcribing with Whisper {model}…")
        segments, info = whisper.transcribe(
            str(wav_path),
            language=(language or None),
            task="translate" if translate else "transcribe",
            beam_size=2,
            vad_filter=True,
            condition_on_previous_text=False,
        )
        out = []
        for seg in segments:
            text = seg.text.strip()
            if text:
                out.append({"start": round(seg.start, 2), "end": round(seg.end, 2), "text": text})
            if progress and info.duration:
                progress(min(seg.end / info.duration, 1.0), f"Transcribing… {seg.end:.0f}s / {info.duration:.0f}s")
    return {
        "language": "en" if translate else info.language,
        "source_language": info.language,
        "language_probability": round(info.language_probability, 3),
        "duration": round(info.duration, 2),
        "model": model,
        "translated": translate,
        "segments": out,
        "text": " ".join(s["text"] for s in out),
    }
