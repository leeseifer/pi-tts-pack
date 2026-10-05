"""MCP server (streamable HTTP at /mcp) exposing PI TTS Pack as tools."""

import json
from typing import Optional

import anyio
import anyio.from_thread
from mcp.server.mcpserver import Audio, Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings

from . import audio, config
from .core import Studio, TTSOptions, public

INSTRUCTIONS = """PI TTS Pack runs on a Raspberry Pi 5: offline neural text-to-speech (Piper) plus Whisper speech-to-text.
- text_to_speech: speak text with any installed voice. Returns a URL to the audio file.
- speech_to_speech: take an audio or video (URL, YouTube link, media_id, or file on the Pi) and re-speak it with another voice.
- transcribe: speech → text with timestamps.
- phone_audio / phone_call: telephony-ready audio (G.711 μ-law 8 kHz) and optional Twilio outbound calls.
- clone_voice: clone a voice from a 5–15 s recording (VieNeu for Vietnamese, Pocket TTS for English/EU languages);
  the clone then works as `voice` everywhere. speech_to_speech(keep_original_voice=true) dubs in the speaker's own voice.
For English narration/storytelling prefer engine=kokoro (e.g. kokoro-af_heart, kokoro-bf_emma, kokoro-am_michael) or supertonic.
Call list_voices first to pick a voice whose language matches the text. Only clone voices you have permission to use."""


def _security() -> TransportSecuritySettings:
    # main.AccessGuard already rejects foreign Host headers (DNS rebinding) unless they carry the API key,
    # and tunnel hostnames change, so the SDK's static allow-list is turned off.
    return TransportSecuritySettings(enable_dns_rebinding_protection=False)


def _opts(voice, speaker, speed) -> TTSOptions:
    return TTSOptions(voice=voice, speaker=speaker, speed=float(speed or 1.0))


def build_mcp(studio: Studio) -> MCPServer:
    mcp = MCPServer(name="pi-tts-pack", title="PI TTS Pack — voices, TTS & voice changer",
                    instructions=INSTRUCTIONS, version=config.VERSION)

    def base() -> str:
        return config.base_url()

    async def in_thread(fn, ctx: Optional[Context] = None):
        """Run blocking work off the event loop, forwarding progress to the MCP client."""

        def progress(p: float, msg: str = "") -> None:
            if ctx is None:
                return
            try:
                anyio.from_thread.run(ctx.report_progress, p, 1.0, msg or None)
            except Exception:  # noqa: BLE001 — progress is best-effort
                pass

        try:
            return await anyio.to_thread.run_sync(lambda: fn(progress))
        except (ValueError, LookupError, RuntimeError) as err:
            # Anticipated failures (bad voice, unreadable file, Twilio not configured…) go back to the model
            raise ToolError(str(err).strip("'\"")) from err

    @mcp.tool(structured_output=False)
    async def list_voices(language: Optional[str] = None) -> str:
        """List every voice: Piper voices, cloned voices (kind=clone) and cloning-engine presets (kind=preset).

        Args:
            language: optional filter, e.g. "en", "en_GB", "vi", "Vietnamese".
        Returns id, name, language, engine, kind and speaker count for each voice. Use the id as `voice` elsewhere.
        """
        voices = studio.voices.list(language)
        slim = [{k: v.get(k) for k in ("id", "name", "language", "language_name", "engine", "kind", "quality",
                                       "num_speakers", "description")} for v in voices]
        return json.dumps({"default_voice": studio.voices.resolve(None), "count": len(slim), "voices": slim})

    @mcp.tool(structured_output=False)
    async def text_to_speech(text: str, voice: Optional[str] = None, speaker: Optional[str] = None,
                             speed: float = 1.0, format: str = "mp3", include_audio: bool = False):
        """Convert text to speech with a Piper voice.

        Args:
            text: what to say. Blank lines add a longer pause.
            voice: voice id from list_voices (default voice if omitted). Loose names like "amy" work.
            speaker: speaker name or number for multi-speaker voices (e.g. en_US-libritts_r-medium has 0..903).
            speed: 0.5 (slow) .. 2.0 (fast). 1.0 = natural.
            format: wav | mp3 | ogg | flac | phone_ulaw | phone_alaw | phone_pcm8k | phone_pcm16k | phone_ulaw_raw
            include_audio: also return the audio inline (base64) so the client can play it directly.
        Returns JSON with id, url, duration, voice.
        """
        meta = await in_thread(lambda _p: studio.tts(text, _opts(voice, speaker, speed), format))
        result = json.dumps(public(meta, base()), ensure_ascii=False)
        if include_audio:
            return [result, Audio(path=config.OUTPUTS_DIR / meta["file"], format=None)]
        return result

    @mcp.tool(structured_output=False)
    async def speech_to_speech(source: str, voice: Optional[str] = None, speaker: Optional[str] = None,
                               speed: float = 1.0, format: str = "mp3", mode: str = "natural",
                               language: Optional[str] = None, translate_to_english: bool = False,
                               make_video: bool = False, background_volume: float = 0.0,
                               whisper_model: Optional[str] = None, keep_original_voice: bool = False,
                               ctx: Optional[Context] = None) -> str:
        """Re-speak an audio or video file with a different voice (Whisper transcription → Piper voice).

        Args:
            source: http(s) URL (direct file, or YouTube/TikTok/etc. page), a media_id / audio id from this
                server, or an absolute path on the Pi inside ~/Music, ~/Videos, ~/Downloads or the uploads folder.
            voice: target voice id. If omitted, a voice matching the detected language is chosen.
            speaker, speed, format: as in text_to_speech.
            mode: "natural" (smooth continuous speech) or "timed" (keeps original timing — best for dubbing video).
            language: spoken language code (e.g. "en", "vi"); auto-detected when omitted.
            translate_to_english: translate the speech to English before re-speaking (use an English voice).
            make_video: for video sources, also return the video with the new voice as its soundtrack.
            background_volume: 0..1 — keep the original audio underneath at this volume (0 = voice only).
            whisper_model: auto (default) | tiny | base | small | medium — bigger is more accurate but slower.
            keep_original_voice: clone the original speaker and use their voice (e.g. with translate_to_english
                for "same voice, new language"). Only with the speaker's permission. `voice` is then ignored.
        Returns JSON with url (audio), video_url (if make_video), transcript and detected language.
        """

        def work(progress):
            media = studio.resolve_source(source, want_video=make_video)
            return studio.revoice(media["media_id"], _opts(voice, speaker, speed), fmt=format, mode=mode,
                                  model=whisper_model, language=language, translate=translate_to_english,
                                  make_video=make_video, background_volume=background_volume,
                                  clone_source=keep_original_voice, progress=progress)

        meta = await in_thread(work, ctx)
        out = public(meta, base())
        out.pop("segments", None)
        return json.dumps(out, ensure_ascii=False)

    @mcp.tool(structured_output=False)
    async def transcribe(source: str, language: Optional[str] = None, translate_to_english: bool = False,
                         whisper_model: Optional[str] = None, ctx: Optional[Context] = None) -> str:
        """Transcribe speech in an audio or video file (same `source` options as speech_to_speech).

        Returns JSON with text, language, duration, media_id (reuse it as `source`) and timed segments.
        """

        def work(progress):
            media = studio.resolve_source(source)
            return studio.transcribe(media["media_id"], model=whisper_model, language=language,
                                     translate=translate_to_english, progress=progress)

        return json.dumps(await in_thread(work, ctx), ensure_ascii=False)

    @mcp.tool(structured_output=False)
    async def phone_audio(text: Optional[str] = None, audio_id: Optional[str] = None, voice: Optional[str] = None,
                          speaker: Optional[str] = None, speed: float = 1.0, codec: str = "ulaw") -> str:
        """Make telephony-ready audio to play on a phone call (IVR prompt, robocall, voicemail drop).

        Args:
            text: text to speak (or give audio_id to convert an existing generated file instead).
            audio_id: id of audio already generated by this server.
            voice, speaker, speed: as in text_to_speech.
            codec: ulaw (G.711 μ-law 8 kHz — Twilio, North America), alaw (Europe), pcm8k (Asterisk/FreePBX),
                pcm16k (HD voice), ulaw_raw (Asterisk native .ulaw).
        Returns JSON with url, a ready-to-use TwiML snippet, and the TwiML webhook URL.
        """
        meta = await in_thread(lambda _p: studio.phone_audio(text=text, item_id=audio_id,
                                                             opts=_opts(voice, speaker, speed), codec=codec))
        out = public(meta, base())
        out["twiml"] = studio.twiml(out["url"])
        out["twiml_webhook"] = f"{base()}/phone/twiml?id={meta['id']}"
        out["public_url_configured"] = bool(config.PUBLIC_BASE_URL)
        return json.dumps(out, ensure_ascii=False)

    @mcp.tool(structured_output=False)
    async def phone_call(to: str, text: Optional[str] = None, audio_id: Optional[str] = None,
                         voice: Optional[str] = None, speaker: Optional[str] = None, speed: float = 1.0,
                         loop: int = 1) -> str:
        """Place a real outbound phone call via Twilio and play the voice to whoever answers.

        Only works when Twilio credentials and PUBLIC_BASE_URL are configured on the server.
        Args:
            to: phone number in E.164 format, e.g. +14155550123.
            text or audio_id: what to play (text is synthesized with voice/speaker/speed).
            loop: how many times to repeat the audio.
        """

        def work(_p):
            meta = studio.phone_audio(text=text, item_id=audio_id, opts=_opts(voice, speaker, speed), codec="ulaw")
            return studio.place_call(to, public(meta, config.PUBLIC_BASE_URL or base())["url"], loop)

        return json.dumps(await in_thread(work))

    @mcp.tool(structured_output=False)
    async def voice_catalog(language: Optional[str] = None, only_not_installed: bool = False) -> str:
        """Search every downloadable Piper voice (~100 voices, 40+ languages).

        Args:
            language: filter such as "en", "vi", "de_DE", "Spanish".
            only_not_installed: hide voices that are already installed.
        """

        def work(_p):
            items = studio.voices.catalog()
            if language:
                lang = language.lower().replace("-", "_")
                items = [v for v in items if (v["language"] or "").lower().startswith(lang)
                         or lang in (v["language_name"] or "").lower()]
            if only_not_installed:
                items = [v for v in items if not v["installed"]]
            return items

        items = await in_thread(work)
        return json.dumps({"count": len(items), "voices": items}, ensure_ascii=False)

    @mcp.tool(structured_output=False)
    async def install_voice(voice_id: str) -> str:
        """Download and install a voice from the Piper catalog (e.g. "en_US-kathleen-low")."""
        info = await in_thread(lambda _p: studio.voices.install(voice_id))
        return json.dumps({"installed": info})

    @mcp.tool(structured_output=False)
    async def clone_voice(source: str, name: str, consent: bool, language: Optional[str] = None,
                          start_seconds: Optional[float] = None, length_seconds: float = 10.0,
                          engine: Optional[str] = None, ctx: Optional[Context] = None) -> str:
        """Clone a voice from a recording so it can be used as `voice` in every other tool.

        Args:
            source: audio/video with the target speaker — same options as speech_to_speech's `source`.
            name: name for the new voice (its id becomes clone-<name>).
            consent: must be true — confirms you have the speaker's permission to clone their voice.
            language: vi | en | fr | de | es | it | pt | nl; detected when omitted. Vietnamese uses VieNeu-TTS,
                other languages Pocket TTS (English falls back to VieNeu if Pocket's cloning weights are locked).
            start_seconds: where the reference starts; omitted = the clearest stretch of speech is picked.
            length_seconds: reference length, 3–30 s (8–15 s of clean single-speaker speech works best).
            engine: force "vieneu" or "pocket".
        Returns the new voice (id, name, language, engine, reference transcript).
        """
        if not consent:
            raise ToolError("Set consent=true only if you have the speaker's permission to clone their voice.")

        def work(progress):
            media = studio.resolve_source(source)
            return studio.create_clone(media["media_id"], name, language=language, start=start_seconds,
                                       seconds=length_seconds, engine=engine, consent=True, progress=progress)

        return json.dumps(await in_thread(work, ctx), ensure_ascii=False)

    @mcp.tool(structured_output=False)
    async def delete_cloned_voice(voice_id: str) -> str:
        """Delete a cloned voice (kind=clone) and its reference recording."""
        await in_thread(lambda _p: studio.delete_clone(voice_id))
        return json.dumps({"deleted": voice_id})

    @mcp.tool(structured_output=False)
    async def list_formats() -> str:
        """List output audio formats (standard and phone/telephony)."""
        return json.dumps({k: v["label"] for k, v in audio.FORMATS.items()}, ensure_ascii=False)

    return mcp


def mcp_http_app(mcp: MCPServer):
    return mcp.streamable_http_app(
        streamable_http_path="/mcp",
        transport_security=_security(),
        host=config.HOST,
        max_request_body_size=64 * 1024 * 1024,
    )
