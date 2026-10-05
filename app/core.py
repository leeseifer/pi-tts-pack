"""PI TTS Pack core: TTS, speech-to-speech (revoice), history, uploads, jobs, phone."""

from __future__ import annotations

import base64
import hashlib
import io
import json
import logging
import re
import secrets
import shutil
import tempfile
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import BinaryIO, Callable, Optional, Union
from urllib.parse import urlparse
from xml.sax.saxutils import escape as xml_escape

import httpx

from . import audio, config, stt
from .clones import CloneManager, best_speech_window
from .voices import VoiceManager, VoiceNotFound

_LOGGER = logging.getLogger(__name__)

MEDIA_EXTS = {".wav", ".mp3", ".m4a", ".aac", ".ogg", ".oga", ".opus", ".flac", ".wma", ".amr", ".3gp",
              ".mp4", ".m4v", ".mov", ".mkv", ".webm", ".avi", ".wmv", ".flv", ".mpg", ".mpeg", ".ts", ".caf"}
_ID_RE = re.compile(r"^[A-Za-z0-9_-]{6,64}$")

SAMPLE_TEXT = {
    "en": "Hello! I'm {name}. I can read any text you give me, in a clear and natural voice.",
    "vi": "Xin chào! Tôi là {name}. Tôi có thể đọc bất kỳ văn bản nào bạn đưa cho tôi.",
    "fr": "Bonjour ! Je m'appelle {name}. Je peux lire n'importe quel texte que vous me donnez.",
    "de": "Hallo! Ich bin {name}. Ich kann jeden Text vorlesen, den du mir gibst.",
    "es": "¡Hola! Soy {name}. Puedo leer cualquier texto que me des, con una voz natural.",
    "it": "Ciao! Sono {name}. Posso leggere qualsiasi testo tu mi dia.",
    "pt": "Olá! Eu sou {name}. Posso ler qualquer texto que você me der.",
    "nl": "Hallo! Ik ben {name}. Ik kan elke tekst voorlezen die je me geeft.",
    "zh": "你好！我是{name}。我可以朗读你给我的任何文字。",
    "ru": "Привет! Я {name}. Я могу прочитать любой текст, который вы мне дадите.",
    "uk": "Привіт! Я {name}. Я можу прочитати будь-який текст.",
    "pl": "Cześć! Jestem {name}. Mogę przeczytać każdy tekst, który mi dasz.",
    "tr": "Merhaba! Ben {name}. Bana verdiğiniz her metni okuyabilirim.",
    "ar": "مرحبا! أنا {name}. يمكنني قراءة أي نص تعطيني إياه.",
    "hi": "नमस्ते! मैं {name} हूँ। मैं आपका दिया हुआ कोई भी पाठ पढ़ सकती हूँ।",
    "ja": "こんにちは！{name}です。どんな文章でも読み上げます。",
    "ko": "안녕하세요! 저는 {name}입니다. 어떤 글이든 읽어 드릴게요.",
}


class StudioError(ValueError):
    pass


@dataclass
class TTSOptions:
    voice: Optional[str] = None
    speaker: Union[str, int, None] = None
    speed: float = 1.0
    noise_scale: Optional[float] = None
    noise_w: Optional[float] = None
    pause: float = 0.3
    volume: float = 1.0


# ---------------------------------------------------------------------------
# Jobs


@dataclass
class Job:
    id: str
    kind: str
    status: str = "queued"  # queued | running | done | error
    progress: float = 0.0
    message: str = "Queued"
    result: Optional[dict] = None
    error: Optional[str] = None
    created: float = field(default_factory=time.time)
    finished: Optional[float] = None


class JobManager:
    def __init__(self, workers: int = 2) -> None:
        self._pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="job")
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()

    def submit(self, kind: str, fn: Callable[[Callable[[float, str], None]], dict]) -> Job:
        job = Job(id=secrets.token_hex(8), kind=kind)
        with self._lock:
            self._prune()
            self._jobs[job.id] = job

        def progress(p: float, msg: str = "") -> None:
            job.progress = round(max(0.0, min(p, 1.0)), 3)
            if msg:
                job.message = msg

        def run() -> None:
            job.status, job.message = "running", "Starting…"
            try:
                job.result = fn(progress)
                job.status, job.progress, job.message = "done", 1.0, "Done"
            except Exception as err:  # noqa: BLE001 — reported to the client
                _LOGGER.error("Job %s failed: %s\n%s", job.id, err, traceback.format_exc())
                job.status, job.error, job.message = "error", str(err), "Failed"
            job.finished = time.time()

        self._pool.submit(run)
        return job

    def get(self, job_id: str) -> Optional[Job]:
        return self._jobs.get(job_id)

    def _prune(self) -> None:
        cutoff = time.time() - 3600
        for jid in [j.id for j in self._jobs.values() if j.finished and j.finished < cutoff]:
            del self._jobs[jid]


# ---------------------------------------------------------------------------
# History of generated files


class History:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._by_hash: dict[str, str] = {}
        for meta in self.list(limit=None):
            if meta.get("hash"):
                self._by_hash[meta["hash"]] = meta["id"]

    @staticmethod
    def new_id() -> str:
        return time.strftime("%Y%m%d-%H%M%S-") + secrets.token_hex(3)

    def save(self, meta: dict) -> dict:
        meta.setdefault("created", time.time())
        (config.OUTPUTS_DIR / f"{meta['id']}.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
        if meta.get("hash"):
            with self._lock:
                self._by_hash[meta["hash"]] = meta["id"]
        return meta

    def get(self, item_id: str) -> dict:
        if not _ID_RE.match(item_id or ""):
            raise StudioError("Invalid id")
        path = config.OUTPUTS_DIR / f"{item_id}.json"
        if not path.exists():
            raise LookupError(f"No generated audio with id '{item_id}'")
        return json.loads(path.read_text(encoding="utf-8"))

    def by_hash(self, key: str) -> Optional[dict]:
        item_id = self._by_hash.get(key)
        if not item_id:
            return None
        try:
            meta = self.get(item_id)
        except LookupError:
            return None
        return meta if (config.OUTPUTS_DIR / meta["file"]).exists() else None

    def list(self, limit: Optional[int] = 50) -> list[dict]:
        metas = []
        for path in sorted(config.OUTPUTS_DIR.glob("*.json"), reverse=True):
            try:
                metas.append(json.loads(path.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                continue
            if limit and len(metas) >= limit:
                break
        return metas

    def delete(self, item_id: str) -> None:
        meta = self.get(item_id)
        for name in (meta.get("file"), meta.get("video_file")):
            if name:
                (config.OUTPUTS_DIR / name).unlink(missing_ok=True)
        (config.OUTPUTS_DIR / f"{item_id}.json").unlink(missing_ok=True)
        with self._lock:
            self._by_hash = {k: v for k, v in self._by_hash.items() if v != item_id}

    def path(self, item_id: str) -> Path:
        return config.OUTPUTS_DIR / self.get(item_id)["file"]


def public(meta: dict, base: str) -> dict:
    """Add absolute URLs to a history item for API clients."""
    out = {k: v for k, v in meta.items() if k not in ("hash",)}
    out["url"] = f"{base}/files/{meta['file']}"
    if meta.get("video_file"):
        out["video_url"] = f"{base}/files/{meta['video_file']}"
    if meta.get("text") and len(meta["text"]) > 2000:
        out["text"] = meta["text"][:2000] + "…"
    return out


# ---------------------------------------------------------------------------
# Studio


class Studio:
    def __init__(self) -> None:
        self.voices = VoiceManager()
        self.clones = CloneManager()
        self.voices.attach(self.clones)
        self.history = History()
        self.jobs = JobManager()
        self._tts_sem = threading.BoundedSemaphore(config.TTS_CONCURRENCY)

    # ----- TTS -------------------------------------------------------------

    def _synth(self, text: str, opts: TTSOptions, language: Optional[str] = None) -> tuple:
        voice_id = self.voices.resolve(opts.voice)
        if self.voices.engine(voice_id) != "piper":
            with self._tts_sem:
                pcm, sr = self.clones.synthesize(voice_id, text, speed=opts.speed, pause=opts.pause,
                                                 volume=opts.volume, language=language)
            return pcm, sr, voice_id, None
        sid = self.voices.speaker_id(voice_id, opts.speaker)
        with self._tts_sem:
            voice = self.voices.get(voice_id)
            pcm = audio.synthesize(voice, text, speaker_id=sid, speed=opts.speed, noise_scale=opts.noise_scale,
                                   noise_w=opts.noise_w, volume=opts.volume, pause=opts.pause)
        return pcm, voice.config.sample_rate, voice_id, sid

    def tts(self, text: str, opts: TTSOptions, fmt: str = "wav") -> dict:
        text = (text or "").strip()
        if not text:
            raise StudioError("Text is empty")
        if len(text) > config.MAX_TEXT_CHARS:
            raise StudioError(f"Text is longer than {config.MAX_TEXT_CHARS} characters")
        fmt = audio.check_format(fmt)
        voice_id = self.voices.resolve(opts.voice)
        info = self.voices.info(voice_id)  # a re-learned or re-created clone must not hit the cache
        rev = info.get("updated") or info.get("created")
        key = hashlib.sha1(json.dumps([text, voice_id, asdict(opts) | {"voice": voice_id, "rev": rev}, fmt],
                                      sort_keys=True, default=str).encode()).hexdigest()
        cached = self.history.by_hash(key)
        if cached:
            return cached

        started = time.monotonic()
        pcm, sr, voice_id, sid = self._synth(text, TTSOptions(**{**asdict(opts), "voice": voice_id}))
        item_id = self.history.new_id()
        path = audio.encode(pcm, sr, fmt, config.OUTPUTS_DIR / item_id)
        return self.history.save({
            "id": item_id, "kind": "tts", "file": path.name, "format": fmt, "mime": audio.FORMATS[fmt]["mime"],
            "voice": voice_id, "speaker": sid, "speed": opts.speed, "text": text,
            "duration": round(len(pcm) / sr, 2), "render_seconds": round(time.monotonic() - started, 2),
            "hash": key,
        })

    def sample(self, voice_id: str, speaker: Union[str, int, None] = None) -> Path:
        voice_id = self.voices.resolve(voice_id)
        sid = self.voices.speaker_id(voice_id, speaker)
        path = config.SAMPLES_DIR / f"{voice_id}{'' if sid is None else f'-s{sid}'}.mp3"
        if not path.exists():
            info = self.voices.info(voice_id)
            template = SAMPLE_TEXT.get(info["language_family"], SAMPLE_TEXT["en"])
            name = info["name"] if sid is None else f"{info['name']} {sid}"
            pcm, sr, _, _ = self._synth(template.format(name=name), TTSOptions(voice=voice_id, speaker=sid))
            audio.encode(pcm, sr, "mp3", path.with_suffix(""))
        return path

    def tts_stream(self, text: str, opts: TTSOptions, encoding: str):
        if encoding not in audio.STREAM_ENCODINGS:
            raise StudioError(f"encoding must be one of {', '.join(audio.STREAM_ENCODINGS)}")
        text = (text or "").strip()
        if not text:
            raise StudioError("Text is empty")
        voice_id = self.voices.resolve(opts.voice)
        if self.voices.engine(voice_id) != "piper":
            info = self.voices.info(voice_id)

            def clone_chunks():
                for para in [p.strip() for p in text.splitlines() if p.strip()]:
                    pcm, sr = self.clones.synthesize(voice_id, para, speed=opts.speed, pause=opts.pause,
                                                     volume=opts.volume)
                    yield audio.encode_stream_chunk(pcm, sr, encoding)

            return clone_chunks(), audio.STREAM_ENCODINGS[encoding]["rate"] or info["sample_rate"], voice_id
        sid = self.voices.speaker_id(voice_id, opts.speaker)
        voice = self.voices.get(voice_id)
        rate = audio.STREAM_ENCODINGS[encoding]["rate"] or voice.config.sample_rate
        chunks = audio.stream(voice, text, encoding, speaker_id=sid, speed=opts.speed, noise_scale=opts.noise_scale,
                              noise_w=opts.noise_w, volume=opts.volume, pause=opts.pause)
        return chunks, rate, voice_id

    def convert(self, item_id: str, fmt: str) -> dict:
        src_meta = self.history.get(item_id)
        fmt = audio.check_format(fmt)
        if src_meta["format"] == fmt:
            return src_meta
        key = f"convert:{item_id}:{fmt}"
        cached = self.history.by_hash(key)
        if cached:
            return cached
        new_id = self.history.new_id()
        path = audio.convert(config.OUTPUTS_DIR / src_meta["file"], fmt, config.OUTPUTS_DIR / new_id)
        meta = {k: v for k, v in src_meta.items() if k in ("voice", "speaker", "speed", "text", "duration", "source_name")}
        return self.history.save({**meta, "id": new_id, "kind": "convert", "file": path.name, "format": fmt,
                                  "mime": audio.FORMATS[fmt]["mime"], "converted_from": item_id, "hash": key})

    # ----- uploads ---------------------------------------------------------

    def _media_meta_path(self, media_id: str) -> Path:
        if not _ID_RE.match(media_id or ""):
            raise StudioError("Invalid media_id")
        return config.UPLOADS_DIR / f"{media_id}.json"

    def media(self, media_id: str) -> dict:
        path = self._media_meta_path(media_id)
        if not path.exists():
            raise LookupError(f"No uploaded media with id '{media_id}'")
        return json.loads(path.read_text(encoding="utf-8"))

    def media_path(self, media_id: str) -> Path:
        return config.UPLOADS_DIR / self.media(media_id)["file"]

    def _register_media(self, media_id: str, path: Path, name: str) -> dict:
        try:
            info = audio.probe(path)
        except ValueError:
            path.unlink(missing_ok=True)
            raise StudioError(f"'{name}' is not a readable audio or video file")
        if not info.has_audio:
            path.unlink(missing_ok=True)
            raise StudioError(f"'{name}' has no audio track to transcribe")
        meta = {"media_id": media_id, "file": path.name, "filename": name, "size": path.stat().st_size,
                "duration": round(info.duration, 2), "has_video": info.has_video, "created": time.time()}
        self._media_meta_path(media_id).write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
        return meta

    def save_upload(self, fileobj: BinaryIO, filename: str) -> dict:
        ext = Path(filename or "").suffix.lower()
        ext = ext if ext in MEDIA_EXTS else ".bin"
        media_id = secrets.token_hex(8)
        dest = config.UPLOADS_DIR / f"{media_id}{ext}"
        limit = config.MAX_UPLOAD_MB * 1024 * 1024
        written = 0
        with open(dest, "wb") as out:
            while chunk := fileobj.read(1024 * 1024):
                written += len(chunk)
                if written > limit:
                    out.close()
                    dest.unlink(missing_ok=True)
                    raise StudioError(f"File is larger than {config.MAX_UPLOAD_MB} MB")
                out.write(chunk)
        return self._register_media(media_id, dest, filename or dest.name)

    def save_base64(self, data: str, filename: str) -> dict:
        if data.startswith("data:") and "," in data[:200]:
            data = data.split(",", 1)[1]
        return self.save_upload(io.BytesIO(base64.b64decode(data)), filename)

    def fetch_url(self, url: str, want_video: bool = False) -> dict:
        """Download a direct media link, or fall back to yt-dlp for pages (YouTube, TikTok, …)."""
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            raise StudioError("Only http(s) URLs are supported")
        name = Path(parsed.path).name or "download"
        media_id = secrets.token_hex(8)
        try:
            with httpx.stream("GET", url, follow_redirects=True, timeout=60) as resp:
                resp.raise_for_status()
                ctype = resp.headers.get("content-type", "").split(";")[0].strip().lower()
                ext = Path(name).suffix.lower()
                if ctype.startswith(("audio/", "video/")) or (ext in MEDIA_EXTS and "html" not in ctype):
                    dest = config.UPLOADS_DIR / f"{media_id}{ext if ext in MEDIA_EXTS else '.bin'}"
                    limit, written = config.MAX_UPLOAD_MB * 1024 * 1024, 0
                    with open(dest, "wb") as out:
                        for chunk in resp.iter_bytes(1024 * 1024):
                            written += len(chunk)
                            if written > limit:
                                raise StudioError(f"Download is larger than {config.MAX_UPLOAD_MB} MB")
                            out.write(chunk)
                    return self._register_media(media_id, dest, name)
        except httpx.HTTPError as err:
            _LOGGER.info("Direct download failed (%s), trying yt-dlp", err)
        return self._ytdlp(url, media_id, want_video)

    def _ytdlp(self, url: str, media_id: str, want_video: bool) -> dict:
        try:
            import yt_dlp
        except ImportError as err:
            raise StudioError("That link is not a direct audio/video file (yt-dlp is not installed)") from err
        opts = {
            "outtmpl": str(config.UPLOADS_DIR / f"{media_id}.%(ext)s"),
            "noplaylist": True, "quiet": True, "no_warnings": True,
            "format": "bv*[height<=720]+ba/b[height<=720]/b" if want_video else "ba/b",
            "merge_output_format": "mp4",
        }
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=True)
        except Exception as err:  # noqa: BLE001
            raise StudioError(f"Could not download media from that link: {err}") from err
        files = [p for p in config.UPLOADS_DIR.glob(f"{media_id}.*") if p.suffix != ".json"]
        if not files:
            raise StudioError("yt-dlp did not produce a file")
        title = (info or {}).get("title") or "download"
        return self._register_media(media_id, files[0], f"{title}{files[0].suffix}")

    def resolve_source(self, source: str, want_video: bool = False) -> dict:
        """MCP helper: URL, media_id, history id, or a local path in an allowed folder."""
        source = (source or "").strip()
        if not source:
            raise StudioError("source is empty")
        if source.startswith(("http://", "https://")):
            return self.fetch_url(source, want_video)
        if _ID_RE.match(source):
            if (config.UPLOADS_DIR / f"{source}.json").exists():
                return self.media(source)
            if (config.OUTPUTS_DIR / f"{source}.json").exists():
                path = self.history.path(source)
                return self._copy_in(path)
        path = Path(source).expanduser().resolve()
        if not any(path.is_relative_to(d) for d in config.MEDIA_DIRS):
            raise StudioError(f"Path must be inside one of: {', '.join(map(str, config.MEDIA_DIRS))}")
        if not path.is_file():
            raise StudioError(f"File not found: {path}")
        return self._copy_in(path)

    def _copy_in(self, path: Path) -> dict:
        media_id = secrets.token_hex(8)
        dest = config.UPLOADS_DIR / f"{media_id}{path.suffix.lower()}"
        shutil.copyfile(path, dest)
        return self._register_media(media_id, dest, path.name)

    # ----- speech to text / speech to speech -------------------------------

    def transcribe(self, media_id: str, *, model: Optional[str] = None, language: Optional[str] = None,
                   translate: bool = False, progress: Optional[Callable[[float, str], None]] = None,
                   languages: Optional[str] = None) -> dict:
        model = model or config.WHISPER_MODEL
        language = (language or "").strip().lower()
        language = None if language in ("", "auto") else language
        candidates = [c.strip().lower() for c in (languages or "").split(",") if c.strip()] or None
        media = self.media(media_id)
        lang_key = language or ("auto_" + "_".join(candidates) if candidates else "auto")
        cache = config.UPLOADS_DIR / f"{media_id}.stt-{model}-{lang_key}-{'tr' if translate else 'tx'}.json"
        if cache.exists():
            return json.loads(cache.read_text(encoding="utf-8"))
        progress = progress or (lambda p, m="": None)
        wav = config.UPLOADS_DIR / f"{media_id}.16k.wav"
        if not wav.exists():
            progress(0.01, "Extracting audio…")
            audio.extract_for_stt(config.UPLOADS_DIR / media["file"], wav)
        progress(0.03, "Loading speech recognition…")
        started = time.monotonic()
        result = stt.transcribe(wav, model=model, language=language, translate=translate, progress=progress,
                                candidates=candidates)
        result["media_id"] = media_id
        result["transcribe_seconds"] = round(time.monotonic() - started, 1)
        cache.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
        return result

    def revoice(self, media_id: str, opts: TTSOptions, *, fmt: str = "mp3", mode: str = "natural",
                segments: Optional[list] = None, transcript: Optional[str] = None, model: Optional[str] = None,
                language: Optional[str] = None, translate: bool = False, make_video: bool = False,
                background_volume: float = 0.0, clone_source: bool = False,
                progress: Optional[Callable[[float, str], None]] = None) -> dict:
        """Transcribe an audio/video file and speak it again with another voice (or the speaker's own, cloned)."""
        fmt = audio.check_format(fmt)
        if mode not in ("natural", "timed"):
            raise StudioError("mode must be 'natural' or 'timed'")
        progress = progress or (lambda p, m="": None)
        media = self.media(media_id)
        src = config.UPLOADS_DIR / media["file"]
        started = time.monotonic()

        detected = spoken = None
        if transcript and transcript.strip():
            segments = [{"start": 0.0, "end": media["duration"], "text": transcript.strip()}]
        if not segments:
            tr = self.transcribe(media_id, model=model, language=language, translate=translate,
                                 progress=lambda p, m="": progress(p * 0.6, m))
            segments, detected, spoken = tr["segments"], tr["language"], tr.get("source_language")
        segments = [{"start": float(s.get("start", 0)), "end": float(s.get("end", 0)), "text": str(s.get("text", ""))}
                    for s in segments]
        if not any(s["text"].strip() for s in segments):
            raise StudioError("No speech was found in that file")

        target_lang = "en" if translate else (detected or self._known_language(media_id) or
                                               (language or "").strip().lower() or None)
        if clone_source:
            opts = TTSOptions(**{**asdict(opts), "voice": self._source_clone(media_id, target_lang or "en", progress)})
        voice_id = self.voices.resolve(opts.voice or self.voices.best_for_language(detected))
        sid = self.voices.speaker_id(voice_id, opts.speaker)
        info = self.voices.info(voice_id)
        engine = info.get("engine", "piper")
        speaks = {info["language_family"]} | (set(self.clones.engines[engine].languages) if engine != "piper" else set())
        warning = None
        if target_lang and target_lang not in speaks:
            warning = (f"Speech is '{target_lang}' but voice {voice_id} speaks '{info['language_family']}'. "
                       "Pick a matching voice, or enable translate (→ English).")

        progress(0.62, f"Speaking with {voice_id}…")
        if engine == "piper":
            voice = self.voices.get(voice_id)
            sr = voice.config.sample_rate
            kw = dict(speaker_id=sid, noise_scale=opts.noise_scale, noise_w=opts.noise_w, volume=opts.volume)

            def synth(text: str, speed: float, pause: float = 0.1):
                return audio.synthesize(voice, text, speed=speed, pause=pause, **kw)
        else:
            sr = info["sample_rate"]

            def synth(text: str, speed: float, pause: float = 0.1):
                return self.clones.synthesize(voice_id, text, speed=speed, pause=pause, volume=opts.volume,
                                              language=target_lang)[0]

        with self._tts_sem:
            if mode == "timed":
                pcm = audio.synthesize_timed(synth, sr, segments, media["duration"], speed=opts.speed,
                                             progress=lambda p: progress(0.62 + 0.3 * p, "Speaking (timed)…"))
            else:
                text = "\n".join(s["text"].strip() for s in segments if s["text"].strip())
                pcm = synth(text, opts.speed, opts.pause)

        item_id = self.history.new_id()
        progress(0.93, "Encoding…")
        with tempfile.TemporaryDirectory(dir=config.OUTPUTS_DIR) as tmp:
            voice_wav = audio.write_wav(Path(tmp) / "voice.wav", pcm, sr)
            if background_volume > 0:
                voice_wav = audio.mix_background(voice_wav, src, min(background_volume, 1.0), Path(tmp) / "mix.wav")
            if fmt == "wav":
                out = Path(shutil.copyfile(voice_wav, config.OUTPUTS_DIR / f"{item_id}.wav"))
            else:
                out = audio.convert(voice_wav, fmt, config.OUTPUTS_DIR / item_id)
            video_file = None
            if make_video and media["has_video"]:
                progress(0.96, "Muxing dubbed video…")
                video_file = audio.mux_video(src, voice_wav, config.OUTPUTS_DIR / f"{item_id}-video").name

        text = "\n".join(s["text"] for s in segments)
        return self.history.save({
            "id": item_id, "kind": "revoice", "file": out.name, "format": fmt, "mime": audio.FORMATS[fmt]["mime"],
            "video_file": video_file, "voice": voice_id, "speaker": sid, "speed": opts.speed, "mode": mode,
            "text": text, "segments": segments, "source_name": media["filename"], "media_id": media_id,
            "source_language": spoken or detected, "translated": translate, "warning": warning,
            "cloned_source": clone_source,
            "duration": round(len(pcm) / sr, 2), "render_seconds": round(time.monotonic() - started, 1),
        })

    # ----- voice cloning ---------------------------------------------------

    def _known_language(self, media_id: str) -> Optional[str]:
        """Language of an earlier transcript of this media, if any (edited-transcript runs skip Whisper)."""
        for cache in sorted(config.UPLOADS_DIR.glob(f"{media_id}.stt-*-tx.json")):
            try:
                return json.loads(cache.read_text(encoding="utf-8")).get("language")
            except (OSError, ValueError):
                continue
        return None

    def create_clone(self, media_id: str, name: str, *, language: Optional[str] = None, start: Optional[float] = None,
                     seconds: float = 10.0, engine: Optional[str] = None, denoise: bool = True, consent: bool = False,
                     progress: Optional[Callable[[float, str], None]] = None) -> dict:
        """Clone the voice in (part of) an uploaded audio/video file."""
        if not consent:
            raise StudioError("Confirm you have the speaker's permission to clone this voice (consent=true).")
        progress = progress or (lambda p, m="": None)
        media = self.media(media_id)
        src = config.UPLOADS_DIR / media["file"]
        seconds = min(max(float(seconds or 10), 3.0), 30.0)
        wav16 = config.UPLOADS_DIR / f"{media_id}.16k.wav"
        if not wav16.exists():
            progress(0.02, "Extracting audio…")
            audio.extract_for_stt(src, wav16)
        if start is None:
            start = best_speech_window(wav16, seconds)
        start = max(0.0, min(float(start), max(media["duration"] - 1.0, 0.0)))
        with tempfile.TemporaryDirectory() as tmp:
            ref = audio.extract_clip(src, Path(tmp) / "reference.wav", start, seconds)
            if audio.probe(ref).duration < 2.5:
                raise StudioError("Not enough speech in that part of the file — pick another start time")
            progress(0.15, "Listening to the reference…")
            language = (language or "").strip().lower() or None
            tr = stt.transcribe(ref, model="auto" if not language else ("base" if language == "en" else "small"),
                                language=language)
            info = self.clones.create(name, ref, language=language or tr["language"], engine=engine,
                                      source_name=media["filename"], ref_text=tr["text"], denoise=denoise,
                                      progress=progress)
        return {**info, "ref_start": start}

    def _source_clone(self, media_id: str, language: str, progress) -> str:
        """Clone the speaker of this media once (per engine) and reuse it."""
        meta_path = self._media_meta_path(media_id)
        media = json.loads(meta_path.read_text(encoding="utf-8"))
        engine = self.clones.pick_engine(language)
        existing = (media.get("clones") or {}).get(engine)
        if existing and existing in self.clones.infos():
            return existing
        progress(0.6, "Cloning the original speaker…")
        info = self.create_clone(media_id, f"{Path(media['filename']).stem[:30]} (auto)", language=language,
                                 engine=engine, consent=True)
        media.setdefault("clones", {})[engine] = info["id"]
        meta_path.write_text(json.dumps(media, ensure_ascii=False), encoding="utf-8")
        return info["id"]

    def switch_clone_engine(self, voice_id: str, engine: str, progress=None) -> dict:
        info = self.clones.switch_engine(voice_id, engine, progress)
        for sample in config.SAMPLES_DIR.glob(f"{voice_id}*"):
            sample.unlink(missing_ok=True)
        return info

    def delete_clone(self, voice_id: str) -> None:
        self.clones.delete(voice_id)
        for sample in config.SAMPLES_DIR.glob(f"{voice_id}*"):
            sample.unlink(missing_ok=True)

    # ----- phone -----------------------------------------------------------

    def phone_audio(self, *, text: Optional[str] = None, item_id: Optional[str] = None,
                    opts: Optional[TTSOptions] = None, codec: str = "phone_ulaw") -> dict:
        codec = codec if codec.startswith("phone_") else f"phone_{codec}"
        audio.check_format(codec)
        if item_id:
            return self.convert(item_id, codec)
        if text:
            return self.tts(text, opts or TTSOptions(), codec)
        raise StudioError("Give either text or an existing audio id")

    @staticmethod
    def twiml(audio_url: str, loop: int = 1, pause_after: int = 0) -> str:
        pause = f'<Pause length="{int(pause_after)}"/>' if pause_after else ""
        return ('<?xml version="1.0" encoding="UTF-8"?>'
                f'<Response><Play loop="{max(int(loop), 0)}">{xml_escape(audio_url)}</Play>{pause}</Response>')

    def place_call(self, to: str, audio_url: str, loop: int = 1) -> dict:
        if not config.twilio_enabled():
            raise StudioError("Phone calls need TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN and TWILIO_FROM_NUMBER in .env")
        if not config.PUBLIC_BASE_URL:
            raise StudioError("Set PUBLIC_BASE_URL in .env (a public https URL for this server) so Twilio can fetch audio")
        if not re.fullmatch(r"\+[1-9]\d{6,14}", to.strip()):
            raise StudioError("Phone number must be in E.164 format, e.g. +14155550123")
        resp = httpx.post(
            f"https://api.twilio.com/2010-04-01/Accounts/{config.TWILIO_ACCOUNT_SID}/Calls.json",
            auth=(config.TWILIO_ACCOUNT_SID, config.TWILIO_AUTH_TOKEN),
            data={"To": to.strip(), "From": config.TWILIO_FROM_NUMBER, "Twiml": self.twiml(audio_url, loop)},
            timeout=30,
        )
        data = resp.json()
        if resp.status_code >= 400:
            raise StudioError(f"Twilio error {resp.status_code}: {data.get('message', data)}")
        return {"call_sid": data.get("sid"), "status": data.get("status"), "to": data.get("to"),
                "from": data.get("from"), "audio_url": audio_url}


__all__ = ["Studio", "StudioError", "TTSOptions", "VoiceNotFound", "public"]
