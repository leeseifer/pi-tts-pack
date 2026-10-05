"""Installed Piper voices: discovery, lazy loading (LRU), catalog and downloads."""

from __future__ import annotations

import json
import logging
import threading
import time
from collections import OrderedDict
from pathlib import Path
from typing import Optional, Union
from urllib.request import urlopen

from piper import PiperVoice
from piper.download_voices import VOICES_JSON, download_voice

from . import config

_LOGGER = logging.getLogger(__name__)

CATALOG_PATH = config.VOICES_DIR / "_catalog.json"
CATALOG_TTL = 7 * 24 * 3600
RESOURCES_DIR = config.VOICES_DIR / "_resources"


class VoiceNotFound(LookupError):
    pass


def _describe(onnx_path: Path) -> Optional[dict]:
    try:
        cfg = json.loads(Path(f"{onnx_path}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None

    voice_id = onnx_path.stem
    parts = voice_id.split("-")
    lang = cfg.get("language") or {}
    audio = cfg.get("audio") or {}
    speaker_map = cfg.get("speaker_id_map") or {}
    code = lang.get("code") or parts[0]
    return {
        "id": voice_id,
        "name": (parts[1] if len(parts) >= 3 else voice_id).replace("_", " ").title(),
        "language": code,
        "language_family": lang.get("family") or code.split("_")[0],
        "language_name": lang.get("name_english") or code,
        "language_native": lang.get("name_native"),
        "country": lang.get("country_english"),
        "quality": audio.get("quality") or (parts[-1] if len(parts) >= 3 else ""),
        "sample_rate": audio.get("sample_rate"),
        "num_speakers": int(cfg.get("num_speakers") or 1),
        # Big multi-speaker models (libritts_r: 904) only expose numeric ids.
        "speakers": sorted(speaker_map, key=speaker_map.get) if 1 < len(speaker_map) <= 64 else [],
        "dataset": cfg.get("dataset"),
        "size_mb": round(onnx_path.stat().st_size / 1e6, 1),
        "engine": "piper",
        "kind": "piper",
    }


class VoiceManager:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._loaded: "OrderedDict[str, PiperVoice]" = OrderedDict()
        self._infos: dict[str, dict] = {}
        self._speaker_maps: dict[str, dict] = {}
        self._extra = None  # cloned / preset voices from other engines (clones.CloneManager)
        RESOURCES_DIR.mkdir(parents=True, exist_ok=True)
        self.refresh()

    def attach(self, provider) -> None:
        """Add voices from another engine; they resolve and list like Piper voices."""
        self._extra = provider

    def _all(self) -> dict[str, dict]:
        return {**self._infos, **(self._extra.infos() if self._extra else {})}

    def engine(self, voice_id: str) -> str:
        return self._all()[voice_id].get("engine", "piper")

    # ----- discovery -------------------------------------------------------

    def refresh(self) -> None:
        infos = {}
        for onnx in sorted(config.VOICES_DIR.glob("*.onnx")):
            info = _describe(onnx)
            if info:
                infos[info["id"]] = info
        with self._lock:
            self._infos = infos
            self._speaker_maps.clear()
            for vid in list(self._loaded):
                if vid not in infos:
                    del self._loaded[vid]

    def list(self, language: Optional[str] = None) -> list[dict]:
        voices = list(self._all().values())
        if language:
            lang = language.lower().replace("-", "_")
            voices = [
                v for v in voices
                if v["language"].lower().startswith(lang) or v["language_family"].lower() == lang
                or lang in (v["language_name"] or "").lower()
            ]
        return voices

    def info(self, voice_id: str) -> dict:
        return self._all()[self.resolve(voice_id)]

    def resolve(self, voice: Optional[str]) -> str:
        """Accept an exact id, or a loose name like 'amy' / 'en_GB-alan'."""
        if not self._infos:
            raise VoiceNotFound("No voices installed")
        if not voice:
            voice = config.DEFAULT_VOICE if config.DEFAULT_VOICE in self._infos else next(iter(self._infos))
        voice = voice.strip()
        every = self._all()
        if voice in every:
            return voice
        needle = voice.lower().replace(" ", "_")
        ids = list(every)

        def name(vid: str) -> str:
            if every[vid].get("slug"):
                return every[vid]["slug"]
            parts = vid.lower().split("-")
            return parts[1] if len(parts) >= 3 else parts[0]

        # Most specific first: id prefix, exact name, name prefix ("jenny" → jenny_dioco), substring.
        for match in (
            lambda vid: vid.lower().startswith(needle),
            lambda vid: name(vid) == needle,
            lambda vid: name(vid).startswith(needle),
            lambda vid: needle in vid.lower(),
        ):
            found = [vid for vid in ids if match(vid)]
            if found:
                return found[0]
        raise VoiceNotFound(f"Voice '{voice}' is not installed. Use list_voices / GET /api/voices.")

    def best_for_language(self, language: Optional[str]) -> Optional[str]:
        """Pick an installed voice for a whisper language code like 'vi' or 'en'."""
        if not language:
            return None
        matches = [v for v in self._infos.values() if v["language_family"] == language.split("_")[0].lower()]
        if not matches:
            return None
        rank = {"high": 0, "medium": 1, "low": 2, "x_low": 3}
        default = config.DEFAULT_VOICE
        matches.sort(key=lambda v: (v["id"] != default, rank.get(v["quality"], 4), v["num_speakers"] > 1))
        return matches[0]["id"]

    def speaker_id(self, voice_id: str, speaker: Union[str, int, None]) -> Optional[int]:
        info = self._infos.get(voice_id)
        if info is None or info["num_speakers"] <= 1 or speaker is None or speaker == "":
            return None
        if isinstance(speaker, int) or str(speaker).isdigit():
            sid = int(speaker)
            if not 0 <= sid < info["num_speakers"]:
                raise ValueError(f"Speaker must be 0..{info['num_speakers'] - 1} for {voice_id}")
            return sid
        speaker_map = self._speaker_maps.get(voice_id)
        if speaker_map is None:
            cfg = json.loads(Path(config.VOICES_DIR / f"{voice_id}.onnx.json").read_text(encoding="utf-8"))
            speaker_map = self._speaker_maps[voice_id] = cfg.get("speaker_id_map") or {}
        if speaker not in speaker_map:
            raise ValueError(f"Unknown speaker '{speaker}' for {voice_id}")
        return int(speaker_map[speaker])

    # ----- loading ---------------------------------------------------------

    def get(self, voice_id: str) -> PiperVoice:
        voice_id = self.resolve(voice_id)
        with self._lock:
            voice = self._loaded.get(voice_id)
            if voice is not None:
                self._loaded.move_to_end(voice_id)
                return voice
        started = time.monotonic()
        voice = PiperVoice.load(config.VOICES_DIR / f"{voice_id}.onnx", download_dir=RESOURCES_DIR)
        _LOGGER.info("Loaded voice %s in %.1fs", voice_id, time.monotonic() - started)
        with self._lock:
            self._loaded[voice_id] = voice
            while len(self._loaded) > config.MAX_LOADED_VOICES:
                self._loaded.popitem(last=False)
        return voice

    # ----- catalog / install ----------------------------------------------

    def catalog(self, refresh: bool = False) -> list[dict]:
        fresh = CATALOG_PATH.exists() and time.time() - CATALOG_PATH.stat().st_mtime < CATALOG_TTL
        if refresh or not fresh:
            try:
                with urlopen(VOICES_JSON, timeout=30) as resp:
                    CATALOG_PATH.write_bytes(resp.read())
            except OSError as err:
                if not CATALOG_PATH.exists():
                    raise RuntimeError(f"Could not download the voice catalog: {err}") from err
                _LOGGER.warning("Using cached voice catalog: %s", err)
        raw = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
        out = []
        for vid, v in sorted(raw.items()):
            lang = v.get("language") or {}
            onnx_size = sum(f.get("size_bytes", 0) for p, f in (v.get("files") or {}).items() if p.endswith(".onnx"))
            out.append({
                "id": vid,
                "name": (v.get("name") or vid).replace("_", " ").title(),
                "language": lang.get("code"),
                "language_family": lang.get("family"),
                "language_name": lang.get("name_english"),
                "language_native": lang.get("name_native"),
                "country": lang.get("country_english"),
                "quality": v.get("quality"),
                "num_speakers": v.get("num_speakers", 1),
                "size_mb": round(onnx_size / 1e6, 1),
                "installed": vid in self._infos,
            })
        return out

    def install(self, voice_id: str) -> dict:
        voice_id = voice_id.strip()
        known = {v["id"] for v in self.catalog()}
        if voice_id not in known:
            raise VoiceNotFound(f"'{voice_id}' is not in the Piper voice catalog")
        try:
            download_voice(voice_id, config.VOICES_DIR)
        except Exception:
            for leftover in config.VOICES_DIR.glob(f"{voice_id}.onnx*"):
                leftover.unlink(missing_ok=True)
            raise
        self.refresh()
        return self._infos[voice_id]

    def remove(self, voice_id: str) -> None:
        voice_id = self.resolve(voice_id)
        for path in config.VOICES_DIR.glob(f"{voice_id}.onnx*"):
            path.unlink(missing_ok=True)
        for path in config.SAMPLES_DIR.glob(f"{voice_id}*"):
            path.unlink(missing_ok=True)
        self.refresh()
