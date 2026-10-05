# PI TTS Pack

Offline text-to-speech and voice changer on a Raspberry Pi 5, with a web UI, a REST API and an MCP server.

Installation: [PI5_SETUP.md](../PI5_SETUP.md). Performance figures in this reference were copied from the source deployment and have not been remeasured from the cleaned pack.

- **Text → voice** with 21 Piper voices, Vietnamese VieNeu presets, and UK/US Kokoro presets prepared during default setup. Additional Piper voices can be installed from the upstream catalog.
- **Audio / Video → different voice**: Whisper transcribes the speech, Piper speaks it again. It can also
  translate to English, keep the original timing for dubbing, and return the video with the new voice.
- **Phone-ready audio**: G.711 μ-law and A-law, 8/16 kHz PCM, raw `.ulaw`, real-time μ-law streaming,
  a TwiML webhook, and optional Twilio outbound calls
- **Voice cloning** from a 5–15 s recording: VieNeu-TTS for Vietnamese (and English), Pocket TTS for
  English, French, German, Spanish, Italian, Portuguese and Dutch. Clones work everywhere, including dubbing a
  video in the original speaker's own voice. There are also 25 VieNeu and 27 Pocket preset voices.
- **MCP server** at `/mcp` (streamable HTTP) for Claude and other agents

| | URL |
|---|---|
| Web UI | http://PI_IP:5050 |
| API docs (Swagger) | http://PI_IP:5050/docs |
| MCP endpoint | http://PI_IP:5050/mcp |

For installation and service setup, start with [PI5_SETUP.md](../PI5_SETUP.md).

## Service

```bash
sudo systemctl status pi-tts-pack      # running? (starts on boot)
sudo systemctl restart pi-tts-pack     # after editing .env
journalctl -u pi-tts-pack -f           # logs
```

Files on the Pi are under `~/pi-tts-pack/`:

| Path | What it holds |
|---|---|
| `app/` | the code |
| `voices/` | `.onnx` voice models |
| `outputs/` | generated audio (history) |
| `uploads/` | uploaded media and cached transcripts |
| `models/` | Whisper models |
| `.env` | settings (see `env.example`) |

## REST API

```bash
# Text → MP3
curl -X POST http://PI_IP:5050/api/tts -H 'Content-Type: application/json' \
  -d '{"text":"Hello from my Pi","voice":"en_US-amy-medium","format":"mp3"}' -o hello.mp3

# Quick GET: works as an <audio src> or a Twilio <Play> URL
curl "http://PI_IP:5050/api/tts?text=Your%20order%20is%20ready&voice=amy&format=phone_ulaw" -o prompt.wav

# Audio/video → new voice (JSON with url, video_url, transcript)
curl -F file=@clip.mp4 -F voice=en_US-ryan-high -F mode=timed -F make_video=true http://PI_IP:5050/api/revoice

# …or a link (direct file, YouTube, TikTok…), returning the audio file directly
curl -F url="https://www.youtube.com/watch?v=..." -F voice=vi_VN-vais1000-medium \
  "http://PI_IP:5050/api/revoice?response=audio" -o dub.mp3
```

**Voices** can be given as a full id (`en_GB-jenny_dioco-medium`) or a short name (`jenny`, `alan`, `ryan`).
Multi-speaker voices take `speaker` (for example `en_US-libritts_r-medium` has speakers 0–903).

**Formats**: `wav mp3 ogg flac phone_ulaw phone_alaw phone_pcm8k phone_pcm16k phone_ulaw_raw`

**Revoice options**:

| Option | Values |
|---|---|
| `mode` | `natural` or `timed` (keeps original timing, for dubbing) |
| `translate` | `true` translates the speech to English |
| `language` | e.g. `vi`; auto-detected if omitted |
| `whisper_model` | `auto`, `tiny`, `base`, `small` or `medium` |
| `background_volume` | `0`–`1`: keeps the original audio underneath |
| `segments` | JSON list of edited lines; skips transcription |
| `transcript` | edited plain text; skips transcription |

**Background jobs with progress**: `POST /api/jobs/revoice`, then poll `GET /api/jobs/{job_id}`.

## Narration voices (English)

Two extra English engines with preset voices (no cloning), good for storytelling and dubbing:

| Engine | Voices | Speed on the Pi 5 | Notes |
|---|---|---|---|
| Kokoro-82M (fp32) | 28 (`kokoro-af_heart`, `kokoro-af_bella`, `kokoro-bf_emma`, `kokoro-am_michael`, …) | ~0.9× real time | Best-rated small open model; voices are graded A–F, best first |
| Supertonic 3 | 10 (`supertonic-f1`…`supertonic-m5`) | ~0.7× real time | `SUPERTONIC_STEPS=16` is slightly cleaner but 2× slower |

Pick them in the web UI with the **Kokoro presets** / **Supertonic presets** type filter, or use the ids anywhere a
`voice` is accepted. Models live in `~/pi-tts-pack/models/kokoro` and `~/pi-tts-pack/models/supertonic-3`.
Kokoro's int8 export is slower and noisier on the Pi, so only the fp32 model is used.

## Voice cloning

In the web UI, click **🧬 Clone a voice** and pick a recording, the loaded audio or video, or a link. Name it,
tick the consent box, and click **Create voice**. Leave the start time blank and it picks the clearest stretch
of speech. Only clone voices you have permission to use.

```bash
curl -F file=@my-voice.m4a -F name=Minh -F consent=true http://PI_IP:5050/api/clones
curl -X POST http://PI_IP:5050/api/tts -H 'Content-Type: application/json' \
  -d '{"text":"Xin chào!","voice":"clone-minh","format":"phone_ulaw"}' -o minh.wav

# Dub a video in the speaker's own voice, translated to English
curl -F file=@clip.mp4 -F clone_source=true -F translate=true -F mode=timed -F make_video=true \
  http://PI_IP:5050/api/revoice
```

| Engine | Languages | Speed on the Pi 5 | Enrolment |
|---|---|---|---|
| VieNeu-TTS v3 Turbo (fp32) | vi, en | ~1.3× real time | ~9 s |
| VieNeu-TTS v3 Turbo (int8, `VIENEU_PRECISION=int8`) | vi, en | ~0.9× real time | ~7 s |
| Pocket TTS (int8) | en, fr, de, es, it, pt, nl | ~1.0× real time | ~5 s (unlocked ✓) |

Speed is processing time relative to the length of the audio; below 1× is faster than real time.

**Unlocking Pocket TTS cloning.** Pocket's cloning weights are gated. Accept the terms at
https://huggingface.co/kyutai/pocket-tts, then on the Pi run:

```bash
~/pi-tts-pack/.venv/bin/hf auth login
sudo systemctl restart pi-tts-pack
```

Until then, English clones use VieNeu, and Pocket offers only its preset voices.

Cloned voices are stored in `~/pi-tts-pack/clones/<id>/` (`meta.json`, `reference.wav` and the voice profile).
Delete them in the UI or with `DELETE /api/clones/{id}`.

## MCP

The MCP server offers these tools:

| Tool | What it does |
|---|---|
| `list_voices` | list installed voices |
| `text_to_speech` | text → audio (optionally returned inline) |
| `speech_to_speech` | audio/video → same words in another voice, or in the original speaker's cloned voice (`keep_original_voice`) |
| `transcribe` | speech → text with timestamps |
| `phone_audio` | telephony-ready audio file plus a TwiML snippet |
| `phone_call` | outbound Twilio call (needs Twilio settings) |
| `voice_catalog` | search all downloadable voices |
| `install_voice` | download a voice from the catalog |
| `list_formats` | list output formats |
| `clone_voice` | clone a voice from a recording (needs `consent=true`) |
| `delete_cloned_voice` | delete a cloned voice |

Add it to Claude Code:

```bash
claude mcp add --transport http piper http://PI_IP:5050/mcp
```

Claude Desktop (via `mcp-remote`) goes in `claude_desktop_config.json`:

```json
{ "mcpServers": { "piper": { "command": "npx", "args": ["-y", "mcp-remote", "http://PI_IP:5050/mcp", "--allow-http"] } } }
```

MCP `source` arguments accept a URL, a `media_id` or audio id from this server, or a file path on the Pi
inside `~/Music`, `~/Videos`, `~/Downloads` or `~/pi-tts-pack/uploads`.

## Using it as a phone caller

1. **Audio files for any PBX.** Pick a Phone format. μ-law 8 kHz WAV plays natively on Twilio, Asterisk,
   FreePBX and 3CX. For Asterisk, `phone_ulaw_raw` gives a `.ulaw` file for `Playback()`.
2. **Twilio inbound.** Point a number's *A call comes in* webhook at
   `https://<PUBLIC_BASE_URL>/phone/twiml?voice=amy&text=Thanks%20for%20calling`.
3. **Twilio outbound.** Set `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_FROM_NUMBER` and
   `PUBLIC_BASE_URL` in `.env`, restart the service, then:
   ```bash
   curl -X POST http://PI_IP:5050/api/phone/call -H 'Content-Type: application/json' \
     -d '{"to":"+14155550123","text":"Hi, this is a reminder about your appointment tomorrow.","voice":"amy"}'
   ```
4. **Real-time bots and SIP bridges.** `GET /api/tts/stream?text=…&encoding=ulaw8k` streams raw
   8 kHz μ-law sentence by sentence. Other encodings: `pcm`, `pcm16k`, `pcm8k`, `alaw8k`.

Twilio has to fetch the audio from the internet, so `PUBLIC_BASE_URL` must be a public https URL for the
Pi, such as a Cloudflare Tunnel, ngrok or Tailscale Funnel.

## Exposing it to the internet (ngrok)

```bash
ngrok http PI_IP:5050     # on the Mac (already signed in to ngrok)
```

Then put the `https://….ngrok-free.app` address into `PUBLIC_BASE_URL` in `~/pi-tts-pack/.env` and restart
the service. A free ngrok URL changes every time ngrok restarts.

Access rules:

- **On the LAN:** no key needed, unless `PIPER_KEY_ON_LAN=true`.
- **From the internet:** requests are recognised by the proxy's `X-Forwarded-For` header, a public client IP,
  or a non-LAN `Host`. They must send `PIPER_API_KEY` as `X-API-Key: …`, `Authorization: Bearer …` or
  `?key=…`. If no key is set, internet access is refused.
- **Always open:** the web page itself and `/files/…` (unguessable file ids, so Twilio `<Play>` works).

Show the key:

```bash
ssh PI_USER@PI_IP
# Set your own PIPER_API_KEY in .env on the Pi.
```

Remote MCP:

```bash
claude mcp add --transport http piper https://<ngrok-url>/mcp --header "X-API-Key: <key>"
```

## Settings (`~/pi-tts-pack/.env`)

| Variable | Default | Meaning |
|---|---|---|
| `PIPER_PORT` | 5050 | HTTP port |
| `PIPER_API_KEY` | empty | Required from the internet on `/api`, `/mcp` and `/phone` (`X-API-Key` header, Bearer token or `?key=`) |
| `PIPER_KEY_ON_LAN` | false | Also require the key on the LAN |
| `PIPER_DEFAULT_VOICE` | en_US-lessac-medium | Voice used when none is given |
| `WHISPER_MODEL` | auto | `auto` uses base for English and small for other languages and translation |
| `PUBLIC_BASE_URL` | (empty) | Public URL, needed for Twilio |
| `PIPER_ALLOWED_HOSTS` | (empty) | Extra Host names allowed on `/mcp` (`*` turns the check off) |

## Deploying changes from the Mac

```bash
./deploy.sh      # sync app/ to the Pi and restart the service
```
