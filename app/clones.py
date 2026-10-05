"""Voice cloning engines and cloned voices.

* VieNeu-TTS v3 Turbo — Vietnamese + English, 48 kHz, ONNX on CPU, 25 preset voices, cloning.
* Pocket TTS (Kyutai) — English (+ fr/de/es/it/pt/nl), 24 kHz, 8 preset voices; cloning needs the
  gated weights (accept the terms on huggingface.co/kyutai/pocket-tts, then `hf auth login` on the Pi).
* Kokoro-82M (fp32 ONNX) — English narration, 24 kHz, 28 preset voices, no cloning.
* Supertonic 3 (Supertone, ONNX) — English, 44.1 kHz, 10 preset voices, no cloning.

A cloned voice lives in clones/<id>/ (meta.json, reference.wav and the engine's voice profile), so
enrolment runs once and every later request loads the stored profile.
"""

from __future__ import annotations

import json
import logging
import re
import shutil
import threading
import time
import unicodedata
from pathlib import Path
from typing import Callable, Optional

import numpy as np

from . import audio, config

_LOGGER = logging.getLogger(__name__)

VIENEU_DIR = config.MODELS_DIR / "vieneu-v3-turbo"
CODEC_DIR = config.MODELS_DIR / "moss-audio-tokenizer-nano-onnx"
KOKORO_DIR = config.MODELS_DIR / "kokoro"
SUPERTONIC_DIR = config.MODELS_DIR / "supertonic-3"
KOKORO_RELEASE = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.1/"

# language code → (Pocket TTS model name, locale)
POCKET_LANGS = {"en": ("english", "en_US"), "fr": ("french", "fr_FR"), "de": ("german", "de_DE"),
                "es": ("spanish", "es_ES"), "it": ("italian", "it_IT"), "pt": ("portuguese", "pt_PT"),
                "nl": ("dutch", "nl_NL")}
LANG_NAMES = {"vi": ("Vietnamese", "Vietnam", "Tiếng Việt"), "en": ("English", None, "English"),
              "fr": ("French", None, "Français"), "de": ("German", None, "Deutsch"), "es": ("Spanish", None, "Español"),
              "it": ("Italian", None, "Italiano"), "pt": ("Portuguese", None, "Português"), "nl": ("Dutch", None, "Nederlands")}
CLONE_LANGS = ["vi", *POCKET_LANGS]
ENGINE_LABELS = {"vieneu": "VieNeu", "pocket": "Pocket TTS", "kokoro": "Kokoro", "supertonic": "Supertonic"}
CLONING_ENGINES = ("vieneu", "pocket")
ENGINE_SAMPLE_RATES = {"vieneu": 48000, "pocket": 24000, "kokoro": 24000, "supertonic": 44100}
# Kokoro's own quality grades (hexgrad/Kokoro-82M VOICES.md) — used to sort and describe the voices.
KOKORO_GRADES = {"af_heart": "A", "af_bella": "A-", "af_nicole": "B-", "bf_emma": "B-", "af_aoede": "C+", "af_kore": "C+",
                 "af_sarah": "C+", "am_fenrir": "C+", "am_michael": "C+", "am_puck": "C+", "af_alloy": "C", "af_nova": "C",
                 "bf_isabella": "C", "bm_fable": "C", "bm_george": "C", "af_sky": "C-", "bm_lewis": "D+", "af_jessica": "D",
                 "af_river": "D", "am_echo": "D", "am_eric": "D", "am_liam": "D", "am_onyx": "D", "bf_alice": "D",
                 "bf_lily": "D", "bm_daniel": "D", "am_santa": "D-", "am_adam": "F+"}
GRADE_ORDER = ["A", "A-", "B-", "C+", "C", "C-", "D+", "D", "D-", "F+"]
POCKET_GATED_HELP = ("Pocket TTS voice cloning is locked: accept the terms at https://huggingface.co/kyutai/pocket-tts "
                     "with your Hugging Face account, then run `hf auth login` on the Pi and restart pi-tts-pack.")


class CloneError(ValueError):
    pass


def slugify(name: str) -> str:
    name = name.replace("đ", "d").replace("Đ", "D")
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "_", ascii_name.lower()).strip("_")[:40] or "voice"


def _peak_normalize(pcm: np.ndarray, peak: float = 0.95) -> np.ndarray:
    top = float(np.max(np.abs(pcm))) if len(pcm) else 0.0
    return (pcm * (peak / top)).astype(np.float32) if top > 1e-6 else pcm.astype(np.float32)


# ---------------------------------------------------------------------------
# Engines


class VieNeuEngine:
    name = "vieneu"
    label = "VieNeu"
    languages = ("vi", "en")

    def __init__(self) -> None:
        self._tts = None
        self._lock = threading.Lock()

    def _load(self):
        if self._tts is not None:
            return self._tts
        sub = "onnx_int8" if config.VIENEU_PRECISION == "int8" else "onnx_update"
        if not (VIENEU_DIR / sub / "vieneu_prefill.onnx").exists() or not (CODEC_DIR / "moss_audio_tokenizer_encode.onnx").exists():
            from huggingface_hub import snapshot_download

            # Plain local copies: onnxruntime >= 1.30 rejects ONNX external data reached through the HF cache symlinks.
            snapshot_download("pnnbao-ump/VieNeu-TTS-v3-Turbo", local_dir=str(VIENEU_DIR), allow_patterns=[
                f"{sub}/*", "denoiser.onnx", "speaker_encoder.onnx", "config.json", "tokenizer.json"])
            snapshot_download("OpenMOSS-Team/MOSS-Audio-Tokenizer-Nano-ONNX", local_dir=str(CODEC_DIR))

        # VieNeu does not pass codec_dir through to its ONNX engine, so point the codec lookup at the local copy.
        from vieneu._v3_turbo_engine import onnx_runtime_lite as orl

        original = orl.OnnxV3LiteEngine._fetch
        if not getattr(original, "_local_codec", False):
            def fetch(repo, files, subfolder):
                return CODEC_DIR if repo == orl._CODEC_REPO else original(repo, files, subfolder)

            fetch._local_codec = True
            orl.OnnxV3LiteEngine._fetch = staticmethod(fetch)

        from vieneu import Vieneu

        started = time.monotonic()
        self._tts = Vieneu(mode="v3turbo", backbone_repo=str(VIENEU_DIR), onnx_dir=str(VIENEU_DIR / sub),
                           precision=config.VIENEU_PRECISION, threads=config.CLONE_THREADS)
        _LOGGER.info("Loaded VieNeu v3 Turbo (%s) in %.1fs", config.VIENEU_PRECISION, time.monotonic() - started)
        return self._tts

    def presets(self) -> list[dict]:
        import vieneu

        data = json.loads((Path(vieneu.__file__).parent / "assets" / "voices_v3_turbo.json").read_text(encoding="utf-8"))
        out = []
        for name, meta in data["presets"].items():
            out.append({"name": name, "gender": meta.get("gender"), "region": meta.get("region"),
                        "description": meta.get("description"), "featured": int(meta.get("featured") or 99)})
        return sorted(out, key=lambda p: (p["featured"], p["name"]))

    def clean(self, src: Path, dest: Path) -> Path:
        """AI-denoise a reference clip (44.1 kHz) — Pocket copies noise and browser artefacts otherwise."""
        with self._lock:
            self._load().denoise(src, out_path=dest)
        return dest

    def enroll(self, ref_wav: Path, voice_dir: Path, language: str, denoise: bool = True) -> None:
        with self._lock:
            speaker_emb, codes = self._load().encode_reference(ref_wav, denoise=denoise)
        np.savez(voice_dir / "vieneu.npz", speaker_emb=speaker_emb, codes=codes)

    def synthesize(self, text: str, profile, language: str) -> tuple[np.ndarray, int]:
        if isinstance(profile, Path):
            z = np.load(profile / "vieneu.npz")
            profile = {"speaker_emb": z["speaker_emb"], "codes": z["codes"]}
        with self._lock:
            tts = self._load()
            wav = tts.infer(text, voice=profile)
        return np.asarray(wav, dtype=np.float32).reshape(-1), tts.sample_rate

    def status(self) -> dict:
        return {"loaded": self._tts is not None, "cloning": True, "languages": list(self.languages),
                "precision": config.VIENEU_PRECISION}


class PocketEngine:
    name = "pocket"
    label = "Pocket TTS"
    languages = tuple(POCKET_LANGS)

    def __init__(self) -> None:
        self._models: dict = {}
        self._states: dict = {}
        self._lock = threading.Lock()

    def _model(self, language: str):
        if language not in POCKET_LANGS:
            raise CloneError(f"Pocket TTS speaks {', '.join(POCKET_LANGS)}, not '{language}'")
        if language not in self._models:
            import torch
            from pocket_tts import TTSModel

            torch.set_num_threads(config.CLONE_THREADS)
            started = time.monotonic()
            self._models[language] = TTSModel.load_model(language=POCKET_LANGS[language][0],
                                                         quantize=config.POCKET_QUANTIZE)
            _LOGGER.info("Loaded Pocket TTS %s in %.1fs (cloning=%s)", language, time.monotonic() - started,
                         self._models[language].has_voice_cloning)
        return self._models[language]

    def presets(self) -> list[dict]:
        from pocket_tts.utils.utils import _ORIGINS_OF_PREDEFINED_VOICES

        return [{"name": n, "description": "Kyutai preset voice"} for n in _ORIGINS_OF_PREDEFINED_VOICES]

    def can_clone(self, language: str = "en") -> bool:
        with self._lock:
            return bool(self._model(language).has_voice_cloning)

    def enroll(self, ref_wav: Path, voice_dir: Path, language: str, denoise: bool = True) -> None:
        from pocket_tts import export_model_state

        with self._lock:
            model = self._model(language)
            if not model.has_voice_cloning:
                raise CloneError(POCKET_GATED_HELP)
            clean = voice_dir / "reference_clean.wav"
            state = model.get_state_for_audio_prompt(clean if clean.exists() else Path(ref_wav), truncate=True)
            export_model_state(state, voice_dir / f"pocket-{language}.safetensors")

    def synthesize(self, text: str, profile, language: str) -> tuple[np.ndarray, int]:
        with self._lock:
            model = self._model(language)
            if isinstance(profile, Path):
                source = profile / f"pocket-{language}.safetensors"
                if not source.exists():  # cloned in another language: enrol for this one from the stored reference
                    if not model.has_voice_cloning:
                        raise CloneError(POCKET_GATED_HELP)
                    from pocket_tts import export_model_state

                    ref = profile / "reference_clean.wav"
                    ref = ref if ref.exists() else profile / "reference.wav"
                    export_model_state(model.get_state_for_audio_prompt(ref, truncate=True), source)
                key = str(source)
            else:
                source = key = profile
            state = self._states.get((language, key))
            if state is None:
                state = self._states[(language, key)] = model.get_state_for_audio_prompt(str(source))
            # A short lead-in stops Pocket from occasionally swallowing the first word (measured 7/8 → 8/8).
            wav = model.generate_audio(state, "... " + text)
        return wav.reshape(-1).float().numpy(), model.sample_rate

    def status(self) -> dict:
        loaded = next(iter(self._models.values()), None)
        return {"loaded": loaded is not None, "cloning": None if loaded is None else bool(loaded.has_voice_cloning),
                "languages": list(self.languages), "quantized": config.POCKET_QUANTIZE, "help": POCKET_GATED_HELP}


def _with_speed(synth: Callable[[float], tuple[np.ndarray, int]], speed: float, lo: float, hi: float):
    """Use the engine's own speed control within its range and stretch the remainder with ffmpeg."""
    native = min(max(speed, lo), hi)
    wav, sr = synth(native)
    return audio.change_tempo(wav, sr, speed / native), sr


class KokoroEngine:
    name = "kokoro"
    label = "Kokoro"
    languages = ("en",)
    native_speed = True

    def __init__(self) -> None:
        self._tts = None
        self._lock = threading.Lock()

    def _files(self) -> tuple[Path, Path]:
        return KOKORO_DIR / "kokoro-v1.0.onnx", KOKORO_DIR / "voices-v1.0.bin"

    def _load(self):
        if self._tts is None:
            model, voices = self._files()
            KOKORO_DIR.mkdir(parents=True, exist_ok=True)
            for f in (model, voices):
                if not f.exists():  # fp32: on the Pi's CPU it is faster *and* cleaner than the int8 export
                    import urllib.request

                    urllib.request.urlretrieve(KOKORO_RELEASE + f.name, f.with_suffix(".part"))
                    f.with_suffix(".part").rename(f)
            from kokoro_onnx import Kokoro

            started = time.monotonic()
            self._tts = Kokoro(str(model), str(voices))
            _LOGGER.info("Loaded Kokoro-82M in %.1fs", time.monotonic() - started)
        return self._tts

    def presets(self) -> list[dict]:
        _, voices = self._files()
        if not voices.exists():
            raise FileNotFoundError(voices)
        out = []
        for key in np.load(voices).files:
            if key[:2] not in ("af", "am", "bf", "bm"):
                continue
            grade = KOKORO_GRADES.get(key, "D")
            accent = "American" if key[0] == "a" else "British"
            gender = "female" if key[1] == "f" else "male"
            out.append({"name": key.split("_", 1)[1].title(), "key": key, "gender": gender, "grade": grade,
                        "region": accent, "description": f"{accent} · {gender} · quality grade {grade}",
                        "locale": "en_US" if key[0] == "a" else "en_GB",
                        "country": "United States" if key[0] == "a" else "Great Britain"})
        return sorted(out, key=lambda p: (GRADE_ORDER.index(p["grade"]), p["name"]))

    def synthesize(self, text: str, profile, language: str, speed: float = 1.0) -> tuple[np.ndarray, int]:
        def run(native: float):
            with self._lock:
                wav, sr = self._load().create(text, voice=profile, speed=native,
                                              lang="en-gb" if str(profile).startswith("b") else "en-us")
            return np.asarray(wav, dtype=np.float32).reshape(-1), sr

        return _with_speed(run, speed, 0.5, 2.0)

    def status(self) -> dict:
        return {"loaded": self._tts is not None, "cloning": False, "languages": list(self.languages),
                "voices": len(KOKORO_GRADES)}


class SupertonicEngine:
    name = "supertonic"
    label = "Supertonic"
    languages = ("en",)  # it has 31 languages, but its Vietnamese tested poorly — English only for now
    native_speed = True

    def __init__(self) -> None:
        self._tts = None
        self._styles: dict = {}
        self._lock = threading.Lock()

    def _load(self):
        if self._tts is None:
            from supertonic import TTS

            started = time.monotonic()
            self._tts = TTS(model="supertonic-3", model_dir=str(SUPERTONIC_DIR), auto_download=True,
                            intra_op_num_threads=config.CLONE_THREADS)
            _LOGGER.info("Loaded Supertonic 3 in %.1fs", time.monotonic() - started)
        return self._tts

    def presets(self) -> list[dict]:
        names = sorted(p.stem for p in (SUPERTONIC_DIR / "voice_styles").glob("*.json"))
        if not names:
            raise FileNotFoundError(SUPERTONIC_DIR / "voice_styles")
        return [{"name": n, "key": n, "gender": "female" if n.startswith("F") else "male",
                 "description": f"Supertonic 3 · {'female' if n.startswith('F') else 'male'} voice {n[1:]}"}
                for n in names]

    def synthesize(self, text: str, profile, language: str, speed: float = 1.0) -> tuple[np.ndarray, int]:
        def run(native: float):
            with self._lock:
                tts = self._load()
                style = self._styles.get(profile) or self._styles.setdefault(profile, tts.get_voice_style(profile))
                wav, _ = tts.synthesize(text, voice_style=style, lang="en", total_steps=config.SUPERTONIC_STEPS,
                                        speed=native)
            return np.asarray(wav, dtype=np.float32).reshape(-1), tts.sample_rate

        return _with_speed(run, speed, 0.7, 1.6)

    def status(self) -> dict:
        return {"loaded": self._tts is not None, "cloning": False, "languages": list(self.languages),
                "steps": config.SUPERTONIC_STEPS}


# ---------------------------------------------------------------------------
# Cloned + preset voices


class CloneManager:
    def __init__(self) -> None:
        config.CLONES_DIR.mkdir(parents=True, exist_ok=True)
        self.engines = {"vieneu": VieNeuEngine(), "pocket": PocketEngine(), "kokoro": KokoroEngine(),
                        "supertonic": SupertonicEngine()}
        self._infos: dict[str, dict] = {}
        self._profiles: dict[str, tuple] = {}  # voice id → (engine, profile, language)
        self._create_lock = threading.Lock()
        self.refresh()

    # ----- registry -------------------------------------------------------

    @staticmethod
    def _info(voice_id: str, name: str, language: str, engine: str, kind: str, **extra) -> dict:
        lang_name, country, native = LANG_NAMES.get(language, (language, None, None))
        locale = "vi_VN" if language == "vi" else POCKET_LANGS.get(language, (None, language))[1]
        return {
            "id": voice_id, "name": name, "slug": voice_id.split("-", 1)[1], "language": locale,
            "language_family": language, "language_name": lang_name, "language_native": native, "country": country,
            "quality": kind, "num_speakers": 1, "speakers": [], "engine": engine, "kind": kind,
            "dataset": ENGINE_LABELS.get(engine, engine), "size_mb": 0,
            "sample_rate": ENGINE_SAMPLE_RATES.get(engine, 24000), **extra,
        }

    def refresh(self) -> None:
        infos, profiles = {}, {}
        for meta_path in sorted(config.CLONES_DIR.glob("*/meta.json")):
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            vid = meta["id"]
            infos[vid] = self._info(vid, meta["name"], meta["language"], meta["engine"], "clone",
                                    created=meta.get("created"), updated=meta.get("updated"),
                                    source_name=meta.get("source_name"),
                                    ref_text=meta.get("ref_text"), ref_seconds=meta.get("ref_seconds"))
            profiles[vid] = (meta["engine"], meta_path.parent, meta["language"])
        if config.CLONE_PRESETS:
            for engine_name, language in (("vieneu", "vi"), ("pocket", "en"), ("kokoro", "en"), ("supertonic", "en")):
                try:
                    presets = self.engines[engine_name].presets()
                except Exception as err:  # noqa: BLE001 — a missing optional engine just hides its presets
                    _LOGGER.warning("No %s presets: %s", engine_name, err)
                    continue
                for p in presets:
                    vid = f"{engine_name}-{slugify(p.get('key') or p['name'])}"
                    extra = {k: p[k] for k in ("country", "grade") if p.get(k)}
                    infos[vid] = self._info(vid, p["name"], language, engine_name, "preset",
                                            description=p.get("description"), gender=p.get("gender"),
                                            region=p.get("region"), **extra)
                    if p.get("locale"):
                        infos[vid]["language"] = p["locale"]
                    profiles[vid] = (engine_name, p.get("key") or p["name"], language)
        self._infos, self._profiles = infos, profiles

    def infos(self) -> dict[str, dict]:
        return self._infos

    def clones(self) -> list[dict]:
        return [v for v in self._infos.values() if v["kind"] == "clone"]

    def status(self) -> dict:
        return {name: engine.status() for name, engine in self.engines.items()}

    # ----- synthesis ------------------------------------------------------

    def synthesize(self, voice_id: str, text: str, *, speed: float = 1.0, pause: float = 0.3, volume: float = 1.0,
                   language: Optional[str] = None) -> tuple[np.ndarray, int]:
        engine_name, profile, voice_lang = self._profiles[voice_id]
        engine = self.engines[engine_name]
        lang = language if (language and language in engine.languages) else voice_lang
        native = getattr(engine, "native_speed", False)
        parts, sr = [], None
        for para in [p.strip() for p in re.split(r"\n\s*\n|\r?\n", text) if p.strip()]:
            wav, sr = engine.synthesize(para, profile, lang, speed=speed) if native else engine.synthesize(para, profile, lang)
            parts.append(wav)
            parts.append(np.zeros(int(sr * max(pause, 0.0)), dtype=np.float32))
        if not parts:
            return np.zeros(0, dtype=np.float32), sr or 24000
        pcm = _peak_normalize(np.concatenate(parts[:-1]), 0.95 * min(volume, 1.05))
        return (pcm if native else audio.change_tempo(pcm, sr, speed)), sr

    # ----- create / delete ------------------------------------------------

    def pick_engine(self, language: str, engine: Optional[str] = None) -> str:
        if engine:
            if engine not in CLONING_ENGINES:
                raise CloneError(f"engine must be one of {', '.join(CLONING_ENGINES)} — "
                                 f"{ENGINE_LABELS.get(engine, engine)} only has preset voices")
            if language not in self.engines[engine].languages:
                raise CloneError(f"{self.engines[engine].label} cannot speak '{language}'")
            if engine == "pocket" and not self.engines["pocket"].can_clone(language):
                raise CloneError(POCKET_GATED_HELP)
            return engine
        if language == "vi":
            return "vieneu"
        if language in POCKET_LANGS and self.engines["pocket"].can_clone(language):
            return "pocket"
        if language == "en":
            return "vieneu"  # VieNeu also speaks English while Pocket's cloning weights are locked
        if language in POCKET_LANGS:
            raise CloneError(POCKET_GATED_HELP)
        raise CloneError(f"Cloning supports {', '.join(CLONE_LANGS)} — not '{language}'")

    def create(self, name: str, reference: Path, *, language: str, engine: Optional[str] = None,
               source_name: Optional[str] = None, ref_text: Optional[str] = None, denoise: bool = True,
               progress: Optional[Callable[[float, str], None]] = None) -> dict:
        progress = progress or (lambda p, m="": None)
        name = (name or "").strip()
        if not name:
            raise CloneError("Give the voice a name")
        engine = self.pick_engine(language, engine)
        with self._create_lock:
            base = f"clone-{slugify(name)}"
            vid, n = base, 2
            while (config.CLONES_DIR / vid).exists() or vid in self._infos:
                vid, n = f"{base}_{n}", n + 1
            voice_dir = config.CLONES_DIR / vid
            voice_dir.mkdir(parents=True)
        try:
            shutil.copyfile(reference, voice_dir / "reference.wav")
            started = time.monotonic()
            self._enroll(engine, voice_dir, language, denoise, progress)
            meta = {"id": vid, "name": name, "language": language, "engine": engine, "created": time.time(),
                    "source_name": source_name, "ref_text": ref_text, "enroll_seconds": round(time.monotonic() - started, 1),
                    "ref_seconds": round(audio.probe(voice_dir / "reference.wav").duration, 1),
                    "consent": {"confirmed": True, "at": time.time()}}
            (voice_dir / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            shutil.rmtree(voice_dir, ignore_errors=True)
            raise
        self.refresh()
        return self._infos[vid]

    def _enroll(self, engine: str, voice_dir: Path, language: str, denoise: bool,
                progress: Callable[[float, str], None]) -> None:
        if engine == "pocket" and denoise:
            progress(0.5, "Cleaning the recording…")
            self.engines["vieneu"].clean(voice_dir / "reference.wav", voice_dir / "reference_clean.wav")
        progress(0.6, f"Learning the voice with {self.engines[engine].label}…")
        self.engines[engine].enroll(voice_dir / "reference.wav", voice_dir, language, denoise=denoise)

    def switch_engine(self, voice_id: str, engine: str,
                      progress: Optional[Callable[[float, str], None]] = None) -> dict:
        """Re-learn a cloned voice with the other engine from its stored reference (same id, so callers keep working)."""
        progress = progress or (lambda p, m="": None)
        info = self._infos.get(voice_id)
        if not info or info["kind"] != "clone":
            raise LookupError(f"No cloned voice '{voice_id}'")
        voice_dir = config.CLONES_DIR / voice_id
        meta = json.loads((voice_dir / "meta.json").read_text(encoding="utf-8"))
        engine = self.pick_engine(meta["language"], engine)
        with self._create_lock:
            for old in list(voice_dir.glob("pocket-*.safetensors")) + [voice_dir / "vieneu.npz"]:
                old.unlink(missing_ok=True)
            started = time.monotonic()
            self._enroll(engine, voice_dir, meta["language"], True, progress)
            meta.update(engine=engine, updated=time.time(), enroll_seconds=round(time.monotonic() - started, 1))
            (voice_dir / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        self.engines["pocket"]._states.clear()
        self.refresh()
        return self._infos[voice_id]

    def delete(self, voice_id: str) -> None:
        info = self._infos.get(voice_id)
        if not info or info["kind"] != "clone":
            raise LookupError(f"No cloned voice '{voice_id}'")
        shutil.rmtree(config.CLONES_DIR / voice_id, ignore_errors=True)
        self.refresh()

    def reference(self, voice_id: str) -> Path:
        info = self._infos.get(voice_id)
        if not info or info["kind"] != "clone":
            raise LookupError(f"No cloned voice '{voice_id}'")
        return config.CLONES_DIR / voice_id / "reference.wav"


# ---------------------------------------------------------------------------
# Reference clip selection


def best_speech_window(wav16k: Path, seconds: float) -> float:
    """Start time of the `seconds`-long stretch with the most speech (simple energy VAD)."""
    import wave

    with wave.open(str(wav16k), "rb") as w:
        sr = w.getframerate()
        pcm = np.frombuffer(w.readframes(min(w.getnframes(), sr * 600)), dtype=np.int16).astype(np.float32) / 32768
    hop = int(sr * 0.1)
    if len(pcm) < hop * 2:
        return 0.0
    frames = pcm[: len(pcm) // hop * hop].reshape(-1, hop)
    rms = np.sqrt((frames ** 2).mean(axis=1) + 1e-12)
    db = 20 * np.log10(rms)
    voiced = (db > max(np.percentile(db, 90) - 25, -50)).astype(np.float32)
    win = max(int(seconds / 0.1), 1)
    if len(voiced) <= win:
        return 0.0
    scores = np.convolve(voiced, np.ones(win), mode="valid")
    start = int(np.argmax(scores))
    while voiced[start] == 0 and start < len(voiced) - win:  # don't open on silence
        start += 1
    return round(start * 0.1, 1)
