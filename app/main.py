"""PI TTS Pack HTTP server: web UI + REST API + MCP (streamable HTTP at /mcp)."""

import json
import logging
import secrets
from contextlib import asynccontextmanager
from typing import Annotated, Literal, Optional, Union

from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import audio, config
from .access import is_remote
from .clones import CLONE_LANGS
from .core import Studio, StudioError, TTSOptions, public
from .mcp_server import build_mcp, mcp_http_app
from .voices import VoiceNotFound

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

studio = Studio()
mcp = build_mcp(studio)
_mcp_app = mcp_http_app(mcp)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    async with mcp.session_manager.run():
        yield


app = FastAPI(
    title="PI TTS Pack",
    version=config.VERSION,
    lifespan=lifespan,
    description=(
        "Offline text-to-speech (Piper) and voice changer (Whisper → Piper) on a Raspberry Pi 5.\n\n"
        "* **Text → voice:** `POST /api/tts`\n"
        "* **Audio/Video → new voice:** `POST /api/revoice`\n"
        "* **Phone:** `format=phone_ulaw`, `GET /api/tts/stream?encoding=ulaw8k`, `/phone/twiml`\n"
        "* **MCP:** streamable HTTP at `/mcp`"
    ),
)
app.router.routes.extend(_mcp_app.routes)


class AccessGuard:
    """Protects /api, /mcp and /phone: open on the LAN, PIPER_API_KEY required from the internet."""

    PROTECTED = ("/api", "/mcp", "/phone")

    def __init__(self, asgi_app):
        self.app = asgi_app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope["path"].startswith(self.PROTECTED):
            remote = is_remote(scope)
            if remote and not config.API_KEY:
                detail = "Access from outside the local network is off. Set PIPER_API_KEY in ~/pi-tts-pack/.env on the Pi."
                await JSONResponse({"detail": detail}, status_code=403)(scope, receive, send)
                return
            if config.API_KEY and (remote or config.KEY_ON_LAN):
                headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}
                bearer = headers.get("authorization", "").removeprefix("Bearer ").strip()
                supplied = headers.get("x-api-key") or bearer or Request(scope).query_params.get("key", "")
                if not secrets.compare_digest(supplied, config.API_KEY):
                    await JSONResponse({"detail": "Missing or invalid API key"}, status_code=401)(scope, receive, send)
                    return
        await self.app(scope, receive, send)


app.add_middleware(AccessGuard)


@app.exception_handler(VoiceNotFound)
@app.exception_handler(LookupError)
async def _not_found(_req, err):
    return JSONResponse({"detail": str(err).strip("'\"")}, status_code=404)


@app.exception_handler(ValueError)
async def _bad_request(_req, err):
    return JSONResponse({"detail": str(err)}, status_code=400)


@app.exception_handler(RuntimeError)
async def _server_error(_req, err):
    return JSONResponse({"detail": str(err)}, status_code=500)


def base_of(request: Request) -> str:
    """Public-facing base URL, so links work through tunnels as well as on the LAN."""
    if config.PUBLIC_BASE_URL and is_remote(request.scope):
        return config.PUBLIC_BASE_URL
    proto = request.headers.get("x-forwarded-proto", "").split(",")[0].strip()
    host = (request.headers.get("x-forwarded-host") or request.headers.get("host") or "").split(",")[0].strip()
    if proto in ("http", "https") and host:
        return f"{proto}://{host}"
    return str(request.base_url).rstrip("/")


def phone_base(request: Request) -> str:
    return config.PUBLIC_BASE_URL or base_of(request)


# ---------------------------------------------------------------------------
# UI + info

app.mount("/files", StaticFiles(directory=config.OUTPUTS_DIR), name="files")
app.mount("/static", StaticFiles(directory=config.STATIC_DIR), name="static")


@app.get("/", include_in_schema=False)
async def index():
    return FileResponse(config.STATIC_DIR / "index.html", headers={"Cache-Control": "no-cache"})


@app.get("/health", tags=["info"])
async def health():
    return {"ok": True, "voices": len(studio.voices.list()), "version": config.VERSION}


@app.get("/api/info", tags=["info"])
async def info(request: Request):
    base = base_of(request)
    return {
        "version": config.VERSION,
        "base_url": base,
        "mcp_url": f"{base}/mcp",
        "default_voice": studio.voices.resolve(None) if studio.voices.list() else None,
        "voices_installed": len(studio.voices.list()),
        "whisper_models": config.WHISPER_MODELS,
        "whisper_default": config.WHISPER_MODEL,
        "formats": [{"id": k, "label": v["label"], "group": v["group"], "ext": v["ext"]} for k, v in audio.FORMATS.items()],
        "stream_encodings": list(audio.STREAM_ENCODINGS),
        "api_key_required": bool(config.API_KEY) and (config.KEY_ON_LAN or is_remote(request.scope)),
        "remote_access": bool(config.API_KEY),
        "public_base_url": config.PUBLIC_BASE_URL or None,
        "twilio_enabled": config.twilio_enabled(),
        "max_upload_mb": config.MAX_UPLOAD_MB,
        "https_port": config.HTTPS_PORT or None,
        "cloning": {"engines": studio.clones.status(), "languages": CLONE_LANGS,
                    "clones": len(studio.clones.clones())},
    }


@app.get("/api/formats", tags=["info"])
async def formats():
    return {k: {"label": v["label"], "group": v["group"], "mime": v["mime"], "ext": v["ext"]}
            for k, v in audio.FORMATS.items()}


# ---------------------------------------------------------------------------
# Voices


@app.get("/api/voices", tags=["voices"])
async def list_voices(language: Optional[str] = None):
    return {"default": studio.voices.resolve(None) if studio.voices.list() else None,
            "voices": studio.voices.list(language)}


@app.get("/api/voices/catalog", tags=["voices"])
async def voice_catalog(language: Optional[str] = None, refresh: bool = False):
    items = await run_in_threadpool(studio.voices.catalog, refresh)
    if language:
        lang = language.lower().replace("-", "_")
        items = [v for v in items if (v["language"] or "").lower().startswith(lang)
                 or lang in (v["language_name"] or "").lower()]
    return {"voices": items}


class InstallRequest(BaseModel):
    voice: str


@app.post("/api/voices/install", tags=["voices"])
async def install_voice(body: InstallRequest):
    return {"installed": await run_in_threadpool(studio.voices.install, body.voice)}


@app.delete("/api/voices/{voice_id}", tags=["voices"])
async def delete_voice(voice_id: str):
    voice_id = studio.voices.resolve(voice_id)
    engine = studio.voices.engine(voice_id)
    if engine == "piper":
        await run_in_threadpool(studio.voices.remove, voice_id)
    elif studio.voices.info(voice_id)["kind"] == "clone":
        await run_in_threadpool(studio.delete_clone, voice_id)
    else:
        raise HTTPException(400, "Preset voices of the cloning engines cannot be deleted")
    return {"deleted": voice_id}


@app.get("/api/voices/{voice_id}/sample", tags=["voices"])
async def voice_sample(voice_id: str, speaker: Optional[str] = None):
    path = await run_in_threadpool(studio.sample, voice_id, speaker)
    return FileResponse(path, media_type="audio/mpeg", headers={"Cache-Control": "max-age=86400"})


# ---------------------------------------------------------------------------
# Text → speech


class TTSRequest(BaseModel):
    text: str = Field(..., description="Text to speak. Blank lines add a longer pause.")
    voice: Optional[str] = Field(None, description="Voice id (see /api/voices). Loose names like 'amy' work.")
    speaker: Optional[Union[int, str]] = Field(None, description="Speaker id/name for multi-speaker voices")
    speed: float = Field(1.0, ge=0.25, le=4.0, description="0.5 = slow, 2.0 = fast")
    noise_scale: Optional[float] = Field(None, ge=0, le=2, description="Expressiveness (voice default ≈ 0.667)")
    noise_w: Optional[float] = Field(None, ge=0, le=2, description="Phoneme timing variation (default ≈ 0.8)")
    pause: float = Field(0.3, ge=0, le=5, description="Silence between sentences, seconds")
    volume: float = Field(1.0, ge=0.05, le=2)
    format: str = Field("wav", description="wav, mp3, ogg, flac, phone_ulaw, phone_alaw, phone_pcm8k, …")

    def options(self) -> TTSOptions:
        return TTSOptions(voice=self.voice, speaker=self.speaker, speed=self.speed, noise_scale=self.noise_scale,
                          noise_w=self.noise_w, pause=self.pause, volume=self.volume)


def _audio_response(meta: dict, request: Request, response: str, download: bool):
    item = public(meta, base_of(request))
    if response == "json":
        return item
    headers = {"X-Audio-Id": meta["id"], "X-Audio-Url": item["url"], "X-Audio-Duration": str(meta.get("duration"))}
    return FileResponse(
        config.OUTPUTS_DIR / meta["file"], media_type=meta["mime"], headers=headers,
        filename=meta["file"] if download else None,
        content_disposition_type="attachment" if download else "inline",
    )


@app.post("/api/tts", tags=["tts"], summary="Text → audio file (returns audio, or JSON with ?response=json)")
async def tts_post(body: TTSRequest, request: Request,
                   response: Literal["audio", "json"] = "audio", download: bool = False):
    meta = await run_in_threadpool(studio.tts, body.text, body.options(), body.format)
    return _audio_response(meta, request, response, download)


@app.get("/api/tts", tags=["tts"], summary="Text → audio via query string (handy for <audio src> or Twilio <Play>)")
async def tts_get(request: Request, text: str, voice: Optional[str] = None, speaker: Optional[str] = None,
                  speed: Annotated[float, Query(ge=0.25, le=4.0)] = 1.0,
                  noise_scale: Annotated[Optional[float], Query(ge=0, le=2)] = None,
                  noise_w: Annotated[Optional[float], Query(ge=0, le=2)] = None,
                  pause: Annotated[float, Query(ge=0, le=5)] = 0.3,
                  volume: Annotated[float, Query(ge=0.05, le=2)] = 1.0, format: str = "wav",
                  response: Literal["audio", "json"] = "audio", download: bool = False):
    params = TTSRequest(text=text, voice=voice, speaker=speaker, speed=speed, noise_scale=noise_scale,
                        noise_w=noise_w, pause=pause, volume=volume, format=format)
    meta = await run_in_threadpool(studio.tts, params.text, params.options(), params.format)
    return _audio_response(meta, request, response, download)


@app.get("/api/tts/stream", tags=["tts", "phone"],
         summary="Low-latency streaming raw audio (pcm | pcm16k | pcm8k | ulaw8k | alaw8k), sentence by sentence")
async def tts_stream(text: str, voice: Optional[str] = None, speaker: Optional[str] = None, speed: float = 1.0,
                     encoding: str = "pcm", pause: float = 0.3):
    opts = TTSOptions(voice=voice, speaker=speaker, speed=speed, pause=pause)
    chunks, rate, voice_id = await run_in_threadpool(studio.tts_stream, text, opts, encoding)
    enc = audio.STREAM_ENCODINGS[encoding]
    media = enc["mime"] + (f";rate={rate};channels=1" if enc["mime"] == "audio/L16" else "")
    return StreamingResponse(chunks, media_type=media, headers={
        "X-Sample-Rate": str(rate), "X-Encoding": encoding, "X-Voice": voice_id, "X-Channels": "1"})


# ---------------------------------------------------------------------------
# Audio / video → speech


async def _ingest(file: Optional[UploadFile], url: Optional[str], media_id: Optional[str], want_video: bool) -> dict:
    if file is not None and file.filename:
        return await run_in_threadpool(studio.save_upload, file.file, file.filename)
    if url:
        return await run_in_threadpool(studio.fetch_url, url.strip(), want_video)
    if media_id:
        return studio.media(media_id)
    raise HTTPException(400, "Send a file, a url, or a media_id")


@app.post("/api/upload", tags=["media"], summary="Upload an audio/video file (or fetch a URL) → media_id")
async def upload(file: Optional[UploadFile] = File(None), url: Optional[str] = Form(None),
                 want_video: bool = Form(True)):
    return await _ingest(file, url, None, want_video)


class TranscribeForm(BaseModel):
    media_id: Optional[str] = None
    url: Optional[str] = None
    whisper_model: Optional[str] = None
    language: Optional[str] = Field(None, description="e.g. en, vi, fr — blank/auto to detect")
    languages: Optional[str] = Field(None, description="Auto-detect only among these, e.g. 'vi,en'")
    translate: bool = Field(False, description="Translate speech to English")


class RevoiceForm(TranscribeForm):
    voice: Optional[str] = None
    speaker: Optional[str] = None
    speed: float = Field(1.0, ge=0.25, le=4.0)
    noise_scale: Optional[float] = None
    noise_w: Optional[float] = None
    pause: float = 0.3
    volume: float = 1.0
    format: str = "mp3"
    mode: Literal["natural", "timed"] = "natural"
    segments: Optional[str] = Field(None, description='Edited transcript as JSON: [{"start":0,"end":2,"text":"…"}]')
    transcript: Optional[str] = Field(None, description="Edited transcript as plain text (natural mode)")
    make_video: bool = Field(False, description="For videos: also produce the video with the new voice")
    background_volume: float = Field(0.0, ge=0, le=1, description="Keep original audio underneath (0..1)")
    clone_source: bool = Field(False, description="Speak with the original speaker's own voice (auto-cloned)")

    def options(self) -> TTSOptions:
        return TTSOptions(voice=self.voice, speaker=self.speaker, speed=self.speed, noise_scale=self.noise_scale,
                          noise_w=self.noise_w, pause=self.pause, volume=self.volume)


def transcribe_form(
    media_id: Optional[str] = Form(None), url: Optional[str] = Form(None),
    whisper_model: Optional[str] = Form(None), language: Optional[str] = Form(None), translate: bool = Form(False),
    languages: Optional[str] = Form(None, description="Auto-detect only among these, e.g. 'vi,en'"),
) -> TranscribeForm:
    return TranscribeForm(media_id=media_id, url=url, whisper_model=whisper_model or None, language=language,
                          translate=translate, languages=languages or None)


def revoice_form(
    media_id: Optional[str] = Form(None), url: Optional[str] = Form(None),
    whisper_model: Optional[str] = Form(None), language: Optional[str] = Form(None), translate: bool = Form(False),
    voice: Optional[str] = Form(None), speaker: Optional[str] = Form(None),
    speed: float = Form(1.0, ge=0.25, le=4.0), noise_scale: Optional[float] = Form(None),
    noise_w: Optional[float] = Form(None), pause: float = Form(0.3, ge=0, le=5), volume: float = Form(1.0, ge=0.05, le=2),
    format: str = Form("mp3"), mode: Literal["natural", "timed"] = Form("natural"),
    segments: Optional[str] = Form(None, description='Edited transcript JSON: [{"start":0,"end":2,"text":"…"}]'),
    transcript: Optional[str] = Form(None, description="Edited transcript as plain text"),
    make_video: bool = Form(False), background_volume: float = Form(0.0, ge=0, le=1),
    clone_source: bool = Form(False, description="Keep the original speaker's voice (auto-cloned)"),
) -> RevoiceForm:
    return RevoiceForm(media_id=media_id, url=url, whisper_model=whisper_model or None, language=language,
                       translate=translate, voice=voice or None, speaker=speaker or None, speed=speed,
                       noise_scale=noise_scale, noise_w=noise_w, pause=pause, volume=volume, format=format, mode=mode,
                       segments=segments, transcript=transcript, make_video=make_video,
                       background_volume=background_volume, clone_source=clone_source)


def _transcribe_fn(media_id: str, f: TranscribeForm):
    return lambda progress: studio.transcribe(media_id, model=f.whisper_model, language=f.language,
                                              translate=f.translate, progress=progress, languages=f.languages)


def _revoice_fn(media_id: str, f: RevoiceForm, request: Request):
    segments = json.loads(f.segments) if f.segments else None
    base = base_of(request)

    def run(progress):
        meta = studio.revoice(media_id, f.options(), fmt=f.format, mode=f.mode, segments=segments,
                              transcript=f.transcript, model=f.whisper_model, language=f.language,
                              translate=f.translate, make_video=f.make_video,
                              background_volume=f.background_volume, clone_source=f.clone_source,
                              progress=progress)
        return public(meta, base)

    return run


@app.post("/api/transcribe", tags=["media"], summary="Audio/video → text with timestamps (waits for result)")
async def transcribe(form: Annotated[TranscribeForm, Depends(transcribe_form)], file: Optional[UploadFile] = File(None)):
    media = await _ingest(file, form.url, form.media_id, False)
    return await run_in_threadpool(_transcribe_fn(media["media_id"], form), None)


@app.post("/api/revoice", tags=["media"],
          summary="Audio/video → same words in a different voice (waits; ?response=audio returns the file)")
async def revoice(request: Request, form: Annotated[RevoiceForm, Depends(revoice_form)], file: Optional[UploadFile] = File(None),
                  response: Literal["json", "audio"] = "json"):
    media = await _ingest(file, form.url, form.media_id, form.make_video)
    item = await run_in_threadpool(_revoice_fn(media["media_id"], form, request), lambda p, m="": None)
    if response == "audio":
        return FileResponse(config.OUTPUTS_DIR / item["file"], media_type=item["mime"],
                            headers={"X-Audio-Id": item["id"], "X-Audio-Url": item["url"]})
    return item


@app.post("/api/jobs/transcribe", tags=["jobs"], summary="Background transcription with progress → job")
async def job_transcribe(form: Annotated[TranscribeForm, Depends(transcribe_form)], file: Optional[UploadFile] = File(None)):
    media = await _ingest(file, form.url, form.media_id, False)
    job = studio.jobs.submit("transcribe", _transcribe_fn(media["media_id"], form))
    return {"job_id": job.id, "media": media}


@app.post("/api/jobs/revoice", tags=["jobs"], summary="Background revoice with progress → job")
async def job_revoice(request: Request, form: Annotated[RevoiceForm, Depends(revoice_form)], file: Optional[UploadFile] = File(None)):
    media = await _ingest(file, form.url, form.media_id, form.make_video)
    job = studio.jobs.submit("revoice", _revoice_fn(media["media_id"], form, request))
    return {"job_id": job.id, "media": media}


# ---------------------------------------------------------------------------
# Voice cloning


@app.get("/api/clones", tags=["cloning"], summary="Cloned voices + engine status")
async def list_clones():
    return {"clones": studio.clones.clones(), "engines": studio.clones.status(), "languages": CLONE_LANGS}


def clone_form(
    media_id: Optional[str] = Form(None), url: Optional[str] = Form(None),
    name: str = Form(..., description="Name for the new voice"),
    language: Optional[str] = Form(None, description="vi, en, fr, de, es, it, pt, nl — blank to detect"),
    start: Optional[float] = Form(None, description="Start of the reference in seconds — blank picks the clearest speech"),
    seconds: float = Form(10.0, ge=3, le=30, description="Reference length (8–15 s works best)"),
    engine: Optional[str] = Form(None, description="vieneu | pocket — blank to choose by language"),
    denoise: bool = Form(True), consent: bool = Form(False, description="You have the speaker's permission"),
) -> dict:
    return dict(media_id=media_id, url=url, name=name, language=language or None, start=start, seconds=seconds,
                engine=engine or None, denoise=denoise, consent=consent)


def _clone_fn(media_id: str, f: dict):
    kw = {k: v for k, v in f.items() if k not in ("media_id", "url", "name")}
    return lambda progress: studio.create_clone(media_id, f["name"], progress=progress, **kw)


@app.post("/api/clones", tags=["cloning"], summary="Clone a voice from audio/video (waits for the result)")
async def create_clone(form: Annotated[dict, Depends(clone_form)], file: Optional[UploadFile] = File(None)):
    if not form["consent"]:
        raise HTTPException(400, "Confirm you have the speaker's permission to clone this voice (consent=true)")
    media = await _ingest(file, form["url"], form["media_id"], False)
    return await run_in_threadpool(_clone_fn(media["media_id"], form), lambda p, m="": None)


@app.post("/api/jobs/clone", tags=["jobs", "cloning"], summary="Clone a voice in the background → job")
async def job_clone(form: Annotated[dict, Depends(clone_form)], file: Optional[UploadFile] = File(None)):
    if not form["consent"]:
        raise HTTPException(400, "Confirm you have the speaker's permission to clone this voice (consent=true)")
    media = await _ingest(file, form["url"], form["media_id"], False)
    job = studio.jobs.submit("clone", _clone_fn(media["media_id"], form))
    return {"job_id": job.id, "media": media}


@app.post("/api/clones/{voice_id}/engine", tags=["cloning"],
          summary="Re-learn a cloned voice with another engine (vieneu | pocket) from its stored reference")
async def clone_engine(voice_id: str, engine: str = Query(..., pattern="^(vieneu|pocket)$")):
    return await run_in_threadpool(studio.switch_clone_engine, voice_id, engine)


@app.delete("/api/clones/{voice_id}", tags=["cloning"])
async def delete_clone(voice_id: str):
    await run_in_threadpool(studio.delete_clone, voice_id)
    return {"deleted": voice_id}


@app.get("/api/clones/{voice_id}/reference", tags=["cloning"], summary="The reference clip a voice was cloned from")
async def clone_reference(voice_id: str):
    return FileResponse(studio.clones.reference(voice_id), media_type="audio/wav")


@app.get("/api/jobs/{job_id}", tags=["jobs"])
async def job_status(job_id: str):
    job = studio.jobs.get(job_id)
    if not job:
        raise HTTPException(404, "Unknown job")
    return job.__dict__


# ---------------------------------------------------------------------------
# History


@app.get("/api/history", tags=["history"])
async def history(request: Request, limit: int = 50):
    base = base_of(request)
    items = []
    for meta in studio.history.list(limit=min(limit, 500)):
        item = public(meta, base)
        item.pop("segments", None)
        items.append(item)
    return {"items": items}


@app.get("/api/history/{item_id}", tags=["history"])
async def history_item(item_id: str, request: Request):
    return public(studio.history.get(item_id), base_of(request))


@app.delete("/api/history/{item_id}", tags=["history"])
async def history_delete(item_id: str):
    studio.history.delete(item_id)
    return {"deleted": item_id}


@app.post("/api/history/{item_id}/convert", tags=["history", "phone"],
          summary="Convert a generated file to another format (e.g. phone_ulaw)")
async def history_convert(item_id: str, request: Request, format: str = "phone_ulaw"):
    meta = await run_in_threadpool(studio.convert, item_id, format)
    return public(meta, base_of(request))


# ---------------------------------------------------------------------------
# Phone


@app.api_route("/phone/twiml", methods=["GET", "POST"], tags=["phone"],
               summary="TwiML webhook: point a Twilio number (or <Redirect>) here to play a Piper voice")
async def twiml(request: Request, text: Optional[str] = None, id: Optional[str] = None,
                voice: Optional[str] = None, speaker: Optional[str] = None, speed: float = 1.0,
                loop: int = 1, pause_after: int = 0):
    opts = TTSOptions(voice=voice, speaker=speaker, speed=speed)
    meta = await run_in_threadpool(lambda: studio.phone_audio(text=text, item_id=id, opts=opts, codec="phone_ulaw"))
    url = f"{phone_base(request)}/files/{meta['file']}"
    return Response(studio.twiml(url, loop, pause_after), media_type="application/xml")


class CallRequest(BaseModel):
    to: str = Field(..., description="E.164 number, e.g. +14155550123")
    text: Optional[str] = None
    audio_id: Optional[str] = None
    voice: Optional[str] = None
    speaker: Optional[Union[int, str]] = None
    speed: float = 1.0
    loop: int = 1


@app.post("/api/phone/call", tags=["phone"], summary="Place an outbound call via Twilio that plays the voice")
async def phone_call(body: CallRequest, request: Request):
    opts = TTSOptions(voice=body.voice, speaker=body.speaker, speed=body.speed)

    def run():
        meta = studio.phone_audio(text=body.text, item_id=body.audio_id, opts=opts, codec="phone_ulaw")
        return studio.place_call(body.to, f"{phone_base(request)}/files/{meta['file']}", body.loop)

    return await run_in_threadpool(run)


def _ensure_certificate() -> tuple[str, str]:
    """Self-signed certificate for the LAN https listener (valid for the Pi's IP and host names)."""
    import socket
    import subprocess

    cert, key = config.TLS_DIR / "cert.pem", config.TLS_DIR / "key.pem"
    if not (cert.exists() and key.exists()):
        config.TLS_DIR.mkdir(parents=True, exist_ok=True)
        host = socket.gethostname()
        san = f"IP:{config.lan_ip()},IP:127.0.0.1,DNS:{host},DNS:{host}.local,DNS:localhost"
        subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "3650",
                        "-subj", "/CN=PI TTS Pack", "-addext", f"subjectAltName={san}",
                        "-keyout", str(key), "-out", str(cert)], check=True, capture_output=True)
        key.chmod(0o600)
    return str(cert), str(key)


def main() -> None:
    import asyncio
    import contextlib
    import signal

    import uvicorn

    class Server(uvicorn.Server):
        @contextlib.contextmanager
        def capture_signals(self):  # one shared handler below stops every listener
            yield

    common = dict(log_level="info", proxy_headers=True, forwarded_allow_ips="127.0.0.1", timeout_keep_alive=30)
    servers = [Server(uvicorn.Config(app, host=config.HOST, port=config.PORT, **common))]
    if config.HTTPS_PORT:
        try:
            cert, key = _ensure_certificate()
            servers.append(Server(uvicorn.Config(app, host=config.HOST, port=config.HTTPS_PORT, ssl_certfile=cert,
                                                 ssl_keyfile=key, lifespan="off", **common)))
        except Exception as err:  # noqa: BLE001 — https is a convenience; plain http keeps working
            logging.warning("HTTPS listener disabled: %s", err)

    async def serve() -> None:
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, lambda: [setattr(s, "should_exit", True) for s in servers])
        await asyncio.gather(*(s.serve() for s in servers))

    asyncio.run(serve())


if __name__ == "__main__":
    main()
