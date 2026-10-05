"""Settings for PI TTS Pack, read from the environment (see .env)."""

import os
import socket
from pathlib import Path

VERSION = "1.2.0"

BASE_DIR = Path(os.environ.get("PIPER_HOME") or Path(__file__).resolve().parent.parent)
VOICES_DIR = BASE_DIR / "voices"
OUTPUTS_DIR = BASE_DIR / "outputs"
UPLOADS_DIR = BASE_DIR / "uploads"
SAMPLES_DIR = BASE_DIR / "samples"
MODELS_DIR = BASE_DIR / "models"
CLONES_DIR = BASE_DIR / "clones"
STATIC_DIR = Path(__file__).resolve().parent / "static"

for _d in (VOICES_DIR, OUTPUTS_DIR, UPLOADS_DIR, SAMPLES_DIR, MODELS_DIR, CLONES_DIR):
    _d.mkdir(parents=True, exist_ok=True)


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


HOST = _env("PIPER_HOST", "0.0.0.0")
PORT = int(_env("PIPER_PORT", "5050"))
# Second listener with a self-signed certificate: browsers only allow the microphone on https pages.
HTTPS_PORT = int(_env("PIPER_HTTPS_PORT", "5443") or 0)
TLS_DIR = BASE_DIR / "tls"
API_KEY = _env("PIPER_API_KEY")
# The key is always required from the internet; set this to also require it on the LAN.
KEY_ON_LAN = _env("PIPER_KEY_ON_LAN", "false").lower() in ("1", "true", "yes")

DEFAULT_VOICE = _env("PIPER_DEFAULT_VOICE", "en_US-lessac-medium")
MAX_LOADED_VOICES = int(_env("PIPER_MAX_LOADED_VOICES", "8"))
TTS_CONCURRENCY = int(_env("PIPER_TTS_CONCURRENCY", "2"))
MAX_TEXT_CHARS = int(_env("PIPER_MAX_TEXT_CHARS", "100000"))
MAX_UPLOAD_MB = int(_env("PIPER_MAX_UPLOAD_MB", "2048"))

# "auto" = base for English, small for other languages / translation (much more accurate there)
WHISPER_MODEL = _env("WHISPER_MODEL", "auto")
WHISPER_MODELS = ["auto", "tiny", "base", "small", "medium"]
WHISPER_THREADS = int(_env("WHISPER_THREADS", "4"))

# Voice cloning (VieNeu-TTS for Vietnamese/English, Pocket TTS for English + EU languages)
VIENEU_PRECISION = _env("VIENEU_PRECISION", "fp32").lower()  # fp32 = best quality, int8 = ~30% faster
POCKET_QUANTIZE = _env("POCKET_QUANTIZE", "true").lower() in ("1", "true", "yes")  # int8: ~2.5x faster on the Pi
CLONE_THREADS = int(_env("CLONE_THREADS", "4"))
CLONE_PRESETS = _env("CLONE_PRESETS", "true").lower() in ("1", "true", "yes")  # list the engines' preset voices
SUPERTONIC_STEPS = int(_env("SUPERTONIC_STEPS", "8"))  # 8 = fast (0.7x real time on the Pi), 16 = a touch cleaner

# Local folders the MCP server may read media from when given a path.
MEDIA_DIRS = [
    Path(p).expanduser().resolve()
    for p in _env("PIPER_MEDIA_DIRS", f"{UPLOADS_DIR}:{OUTPUTS_DIR}:~/Music:~/Videos:~/Downloads").split(":")
    if p
]

# Public URL (e.g. an ngrok / Cloudflare tunnel) — needed for Twilio to fetch audio.
PUBLIC_BASE_URL = _env("PUBLIC_BASE_URL").rstrip("/")
# Extra host names that count as local (e.g. a LAN DNS name for the Pi).
ALLOWED_HOSTS = [h.strip() for h in _env("PIPER_ALLOWED_HOSTS").split(",") if h.strip()]

TWILIO_ACCOUNT_SID = _env("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = _env("TWILIO_AUTH_TOKEN")
TWILIO_FROM_NUMBER = _env("TWILIO_FROM_NUMBER")


def twilio_enabled() -> bool:
    return bool(TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN and TWILIO_FROM_NUMBER)


def lan_ip() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))
            return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"


def base_url() -> str:
    """Best URL for links handed to clients that did not come in over HTTP (MCP)."""
    return PUBLIC_BASE_URL or f"http://{lan_ip()}:{PORT}"
