"""Synthesis, resampling, G.711 encoding and ffmpeg helpers."""

import json
import re
import subprocess
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterator, Optional

import numpy as np
from piper import PiperVoice, SynthesisConfig

# Telephone band-limit: keeps speech crisp on 8 kHz lines and avoids clipping.
_NARROWBAND = "highpass=f=200,lowpass=f=3400,volume=0.9"
_WIDEBAND = "highpass=f=100,lowpass=f=7000,volume=0.9"

FORMATS: dict[str, dict] = {
    "wav": {"ext": "wav", "mime": "audio/wav", "group": "Standard", "label": "WAV · original quality"},
    "mp3": {"ext": "mp3", "mime": "audio/mpeg", "group": "Standard", "label": "MP3 · 128 kbps",
            "args": ["-c:a", "libmp3lame", "-b:a", "128k"]},
    "ogg": {"ext": "ogg", "mime": "audio/ogg", "group": "Standard", "label": "OGG Opus · 48 kbps",
            "args": ["-c:a", "libopus", "-b:a", "48k"]},
    "flac": {"ext": "flac", "mime": "audio/flac", "group": "Standard", "label": "FLAC · lossless",
             "args": ["-c:a", "flac"]},
    "phone_ulaw": {"ext": "wav", "mime": "audio/wav", "group": "Phone", "phone": True,
                   "label": "μ-law 8 kHz WAV · Twilio, US/JP carriers",
                   "filter": _NARROWBAND, "args": ["-ar", "8000", "-ac", "1", "-c:a", "pcm_mulaw"]},
    "phone_alaw": {"ext": "wav", "mime": "audio/wav", "group": "Phone", "phone": True,
                   "label": "A-law 8 kHz WAV · EU / international PBX",
                   "filter": _NARROWBAND, "args": ["-ar", "8000", "-ac", "1", "-c:a", "pcm_alaw"]},
    "phone_pcm8k": {"ext": "wav", "mime": "audio/wav", "group": "Phone", "phone": True,
                    "label": "PCM 16-bit 8 kHz WAV · Asterisk, FreePBX, 3CX",
                    "filter": _NARROWBAND, "args": ["-ar", "8000", "-ac", "1", "-c:a", "pcm_s16le"]},
    "phone_pcm16k": {"ext": "wav", "mime": "audio/wav", "group": "Phone", "phone": True,
                     "label": "PCM 16-bit 16 kHz WAV · HD voice / G.722",
                     "filter": _WIDEBAND, "args": ["-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le"]},
    "phone_ulaw_raw": {"ext": "ulaw", "mime": "audio/basic", "group": "Phone", "phone": True,
                       "label": "Raw μ-law 8 kHz (.ulaw) · Asterisk native",
                       "filter": _NARROWBAND, "args": ["-ar", "8000", "-ac", "1", "-f", "mulaw", "-c:a", "pcm_mulaw"]},
}

# Raw encodings for the low-latency streaming endpoint.
STREAM_ENCODINGS = {
    "pcm": {"rate": None, "mime": "audio/L16"},
    "pcm16k": {"rate": 16000, "mime": "audio/L16"},
    "pcm8k": {"rate": 8000, "mime": "audio/L16"},
    "ulaw8k": {"rate": 8000, "mime": "audio/basic"},
    "alaw8k": {"rate": 8000, "mime": "audio/x-alaw-basic"},
}


def check_format(fmt: str) -> str:
    fmt = (fmt or "wav").lower()
    if fmt not in FORMATS:
        raise ValueError(f"Unknown format '{fmt}'. Choose one of: {', '.join(FORMATS)}")
    return fmt


# ---------------------------------------------------------------------------
# Synthesis


def syn_config(voice: PiperVoice, speaker_id: Optional[int], speed: float, noise_scale: Optional[float],
               noise_w: Optional[float], volume: float) -> SynthesisConfig:
    base = voice.config.length_scale or 1.0
    return SynthesisConfig(
        speaker_id=speaker_id,
        length_scale=base / max(min(speed, 4.0), 0.25),
        noise_scale=noise_scale,
        noise_w_scale=noise_w,
        volume=volume,
    )


def _paragraphs(text: str) -> list[str]:
    return [p.strip() for p in re.split(r"\n\s*\n|\r?\n", text) if p.strip()]


def synthesize(voice: PiperVoice, text: str, *, speaker_id: Optional[int] = None, speed: float = 1.0,
               noise_scale: Optional[float] = None, noise_w: Optional[float] = None, volume: float = 1.0,
               pause: float = 0.3) -> np.ndarray:
    """Return float32 mono audio at voice.config.sample_rate."""
    cfg = syn_config(voice, speaker_id, speed, noise_scale, noise_w, volume)
    sr = voice.config.sample_rate
    gap = np.zeros(int(sr * max(pause, 0.0)), dtype=np.float32)
    parts: list[np.ndarray] = []
    for para in _paragraphs(text):
        for chunk in voice.synthesize(para, cfg):
            parts.append(chunk.audio_float_array)
            if len(gap):
                parts.append(gap)
    if parts and len(gap):
        parts.pop()
    return np.concatenate(parts) if parts else np.zeros(0, dtype=np.float32)


def synthesize_timed(synth: Callable[[str, float], np.ndarray], sr: int, segments: list[dict],
                     total_duration: float, *, speed: float = 1.0, max_speedup: float = 1.5,
                     progress: Optional[Callable[[float], None]] = None) -> np.ndarray:
    """Place each segment at its original start time (for dubbing), speeding up lines that overrun.

    `synth(text, speed)` returns float32 mono audio at `sr` for any engine.
    """
    segs = [s for s in segments if (s.get("text") or "").strip()]
    end_time = max([total_duration] + [float(s.get("end", 0)) for s in segs])
    buf = np.zeros(int((end_time + 1.0) * sr), dtype=np.float32)
    cursor = 0
    for i, seg in enumerate(segs):
        start = float(seg.get("start", 0))
        slot_end = float(segs[i + 1]["start"]) if i + 1 < len(segs) else end_time + 0.5
        slot = slot_end - start
        clip = synth(seg["text"], speed)
        dur = len(clip) / sr
        if slot > 0.3 and dur > slot * 1.02:
            clip = synth(seg["text"], speed * min(dur / slot, max_speedup))
        pos = max(int(start * sr), cursor)
        if pos + len(clip) > len(buf):
            buf = np.concatenate([buf, np.zeros(pos + len(clip) - len(buf), dtype=np.float32)])
        buf[pos:pos + len(clip)] += clip
        cursor = pos + len(clip)
        if progress:
            progress((i + 1) / len(segs))
    return np.clip(buf[:max(cursor, int(end_time * sr))], -1.0, 1.0)


def change_tempo(pcm: np.ndarray, sr: int, speed: float) -> np.ndarray:
    """Pitch-preserving speed change (ffmpeg atempo) for engines without a native speed control."""
    if abs(speed - 1.0) < 0.01 or len(pcm) == 0:
        return pcm
    steps, s = [], float(speed)
    while s > 2.0:
        steps.append("atempo=2.0")
        s /= 2.0
    while s < 0.5:
        steps.append("atempo=0.5")
        s /= 0.5
    steps.append(f"atempo={s:.4f}")
    proc = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "f32le", "-ar", str(sr), "-ac", "1", "-i", "pipe:0",
         "-af", ",".join(steps), "-f", "f32le", "pipe:1"],
        input=pcm.astype(np.float32).tobytes(), capture_output=True, check=False)
    if proc.returncode != 0:
        raise RuntimeError("ffmpeg atempo failed: " + proc.stderr.decode(errors="replace")[-300:])
    return np.frombuffer(proc.stdout, dtype=np.float32).copy()


# ---------------------------------------------------------------------------
# Resampling + G.711 (used by the streaming endpoint)


def resample(x: np.ndarray, src: int, dst: int) -> np.ndarray:
    if src == dst or len(x) == 0:
        return x
    if dst < src:  # windowed-sinc low-pass before decimating
        taps = 101
        n = np.arange(taps) - (taps - 1) / 2
        cutoff = 0.45 * dst / src
        h = np.sinc(2 * cutoff * n) * np.hamming(taps)
        x = np.convolve(x, h / h.sum(), mode="same")
    t = np.arange(int(len(x) * dst / src)) * (src / dst)
    return np.interp(t, np.arange(len(x)), x).astype(np.float32)


def to_pcm16(x: np.ndarray) -> np.ndarray:
    return (np.clip(x, -1.0, 1.0) * 32767).astype(np.int16)


_ULAW_SEG_END = np.array([0x3F, 0x7F, 0xFF, 0x1FF, 0x3FF, 0x7FF, 0xFFF, 0x1FFF])


def lin2ulaw(pcm: np.ndarray) -> bytes:
    """ITU G.711 μ-law (same 14-bit algorithm as the reference g711.c / audioop)."""
    s = pcm.astype(np.int32) >> 2
    mask = np.where(s < 0, 0x7F, 0xFF)
    s = np.minimum(np.abs(s), 8159) + 33
    seg = np.searchsorted(_ULAW_SEG_END, s)
    segc = np.minimum(seg, 7)
    uval = np.where(seg >= 8, 0x7F, (segc << 4) | ((s >> (segc + 1)) & 0x0F))
    return ((uval ^ mask) & 0xFF).astype(np.uint8).tobytes()


_ALAW_SEG_END = np.array([0x1F, 0x3F, 0x7F, 0xFF, 0x1FF, 0x3FF, 0x7FF, 0xFFF])


def lin2alaw(pcm: np.ndarray) -> bytes:
    s = pcm.astype(np.int32) >> 3
    mask = np.where(s >= 0, 0xD5, 0x55)
    s = np.where(s >= 0, s, -s - 1)
    seg = np.searchsorted(_ALAW_SEG_END, s)
    quant = np.where(seg < 2, s >> 1, s >> np.minimum(seg, 7)) & 0x0F
    aval = np.where(seg >= 8, 0x7F, (np.minimum(seg, 7) << 4) | quant)
    return ((aval ^ mask) & 0xFF).astype(np.uint8).tobytes()


def encode_stream_chunk(pcm: np.ndarray, sr: int, encoding: str) -> bytes:
    rate = STREAM_ENCODINGS[encoding]["rate"] or sr
    pcm16 = to_pcm16(resample(pcm, sr, rate))
    if encoding == "ulaw8k":
        return lin2ulaw(pcm16)
    if encoding == "alaw8k":
        return lin2alaw(pcm16)
    return pcm16.tobytes()


def stream(voice: PiperVoice, text: str, encoding: str, **synth_kw) -> Iterator[bytes]:
    sr = voice.config.sample_rate
    cfg = syn_config(voice, synth_kw.get("speaker_id"), synth_kw.get("speed", 1.0), synth_kw.get("noise_scale"),
                     synth_kw.get("noise_w"), synth_kw.get("volume", 1.0))
    gap = np.zeros(int(sr * synth_kw.get("pause", 0.3)), dtype=np.float32)
    for para in _paragraphs(text):
        for chunk in voice.synthesize(para, cfg):
            yield encode_stream_chunk(np.concatenate([chunk.audio_float_array, gap]), sr, encoding)


# ---------------------------------------------------------------------------
# Files / ffmpeg


def _ffmpeg(args: list[str], stdin: Optional[bytes] = None) -> None:
    proc = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *args],
                          input=stdin, capture_output=True, check=False)
    if proc.returncode != 0:
        raise RuntimeError("ffmpeg failed: " + proc.stderr.decode(errors="replace").strip()[-600:])


def write_wav(path: Path, audio: np.ndarray, sr: int) -> Path:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(to_pcm16(audio).tobytes())
    return path


def encode(audio: np.ndarray, sr: int, fmt: str, dest_stem: Path) -> Path:
    """Write float audio to dest_stem.<ext> in the requested format."""
    spec = FORMATS[check_format(fmt)]
    out = dest_stem.with_name(f"{dest_stem.name}.{spec['ext']}")
    if fmt == "wav":
        return write_wav(out, audio, sr)
    args = ["-f", "s16le", "-ar", str(sr), "-ac", "1", "-i", "pipe:0"]
    if spec.get("filter"):
        args += ["-af", spec["filter"]]
    _ffmpeg(args + spec["args"] + [str(out)], stdin=to_pcm16(audio).tobytes())
    return out


def convert(src: Path, fmt: str, dest_stem: Path) -> Path:
    spec = FORMATS[check_format(fmt)]
    out = dest_stem.with_name(f"{dest_stem.name}.{spec['ext']}")
    args = ["-i", str(src), "-vn", "-ac", "1"]
    if spec.get("filter"):
        args += ["-af", spec["filter"]]
    _ffmpeg(args + spec.get("args", ["-c:a", "pcm_s16le"]) + [str(out)])
    return out


def extract_for_stt(src: Path, dest: Path) -> Path:
    _ffmpeg(["-i", str(src), "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(dest)])
    return dest


def extract_clip(src: Path, dest: Path, start: float, seconds: float, sr: int = 24000) -> Path:
    """Mono reference clip for cloning: trimmed, leading silence removed, rumble filtered."""
    _ffmpeg(["-ss", f"{start:.2f}", "-t", f"{seconds:.2f}", "-i", str(src), "-vn", "-ac", "1", "-ar", str(sr),
             "-af", "highpass=f=60,silenceremove=start_periods=1:start_threshold=-50dB", "-c:a", "pcm_s16le", str(dest)])
    return dest


def mix_background(voice_wav: Path, original: Path, bg_volume: float, dest: Path) -> Path:
    """Lay the new voice over the (quieted) original soundtrack."""
    _ffmpeg([
        "-i", str(voice_wav), "-i", str(original),
        "-filter_complex",
        f"[1:a]aformat=channel_layouts=mono,volume={bg_volume:.3f}[bg];"
        "[0:a][bg]amix=inputs=2:duration=longest:normalize=0,alimiter=limit=0.95[out]",
        "-map", "[out]", "-ac", "1", "-c:a", "pcm_s16le", str(dest),
    ])
    return dest


def mux_video(video: Path, audio: Path, dest_stem: Path) -> Path:
    """Replace a video's soundtrack. Keeps the video stream when the container allows it."""
    ext = video.suffix.lower()
    if ext in (".webm",):
        out, acodec = dest_stem.with_name(dest_stem.name + ".webm"), ["-c:a", "libopus", "-b:a", "96k"]
    elif ext in (".mkv",):
        out, acodec = dest_stem.with_name(dest_stem.name + ".mkv"), ["-c:a", "aac", "-b:a", "160k"]
    else:
        out, acodec = dest_stem.with_name(dest_stem.name + ".mp4"), ["-c:a", "aac", "-b:a", "160k"]
    base = ["-i", str(video), "-i", str(audio), "-map", "0:v:0", "-map", "1:a:0"]
    try:
        _ffmpeg(base + ["-c:v", "copy", *acodec, "-movflags", "+faststart", str(out)])
    except RuntimeError:
        out = dest_stem.with_name(dest_stem.name + ".mp4")
        _ffmpeg(base + ["-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-c:a", "aac", "-b:a", "160k",
                        "-movflags", "+faststart", str(out)])
    return out


@dataclass
class MediaInfo:
    duration: float
    has_audio: bool
    has_video: bool


def probe(path: Path) -> MediaInfo:
    proc = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration:stream=codec_type,disposition",
         "-of", "json", str(path)],
        capture_output=True, check=False,
    )
    if proc.returncode != 0:
        raise ValueError("Not a readable audio/video file")
    data = json.loads(proc.stdout or b"{}")
    streams = data.get("streams", [])
    has_video = any(
        s.get("codec_type") == "video" and not (s.get("disposition") or {}).get("attached_pic") for s in streams
    )
    has_audio = any(s.get("codec_type") == "audio" for s in streams)
    duration = float((data.get("format") or {}).get("duration") or 0.0)
    if duration <= 0 and has_audio:
        # Browser MediaRecorder webm files carry no duration header — measure by decoding.
        run = subprocess.run(["ffmpeg", "-hide_banner", "-i", str(path), "-vn", "-f", "null", "-"],
                             capture_output=True, text=True, check=False)
        stamps = re.findall(r"time=(\d+):(\d+):(\d+(?:\.\d+)?)", run.stderr)
        if stamps:
            h, m, s = stamps[-1]
            duration = int(h) * 3600 + int(m) * 60 + float(s)
    return MediaInfo(duration=duration, has_audio=has_audio, has_video=has_video)
