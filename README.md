# PI TTS Pack

**Easy to set up. Easy to use.** Turn a Raspberry Pi 5 into a local voice studio with text-to-speech, transcription, voice changing, a browser UI, and Claude MCP tools.

## Install on Raspberry Pi 5 — one command

**Run this command on your Raspberry Pi 5.** Open a terminal on the Pi, or use an SSH session already connected to it. Do not run it in your Mac's local terminal.

```bash
curl -fsSL https://raw.githubusercontent.com/leeseifer/pi-tts-pack/main/install.sh | bash
```

Use a 64-bit Raspberry Pi OS/Debian/Ubuntu installation with Python 3.11–3.13, internet access, and a normal account with sudo. Python 3.11 on Bookworm matches the source Pi. The installer asks for sudo if needed, downloads models, starts the boot service, and checks real audio and MCP. Existing settings and recordings survive a reinstall.

When it prints **Ready!**, open the **Web UI** address. Type your text, choose a voice, and press Generate. The default setup downloads **74 voices: 21 Piper, 25 Vietnamese VieNeu, and 28 UK/US Kokoro presets**, plus Whisper base/small. It prepares the models and checks actual Vietnamese and UK/US speech before declaring setup ready. If the default ports are occupied, it prints the alternatives it selected.

To test the pack on your **Mac** in a separate Linux ARM64 Docker container, follow [DOCKER_TEST.md](DOCKER_TEST.md). The Pi installer above remains Pi-only.

For additional voices, open **+ More voices**. Search by name or choose a language, tick the voices you want, and press **Install selected**. Downloads show progress and size; when complete, the new voice is selected and the studio's filters are cleared so it is visible. The browser also lists the installed VieNeu and Kokoro presets: press **Use voice** to select one without downloading it again.

## Connect Claude MCP

The installer starts the MCP server on your Pi and prints its address. After the Pi is ready, follow the separate [Claude connection guide](CLAUDE_MCP.md) for Claude Code or Claude Desktop.

Try: **“Use PI TTS Pack to say Hello in English and return the audio.”**

## What you get

- Local text-to-speech in MP3, WAV, OGG, FLAC, and phone formats.
- Audio/video transcription and voice changing.
- Browser UI, REST API, and 11 MCP tools.
- Vietnamese VieNeu and UK/US Kokoro voice packs ready after installation, plus new VieNeu voice cloning.
- Optional Pocket TTS and Supertonic engines.
- No saved personal clones, keys, or recordings in the public repository.

For additional Pocket TTS and Supertonic engines, run the same installer **on the Pi** with `--full`:

```bash
curl -fsSL https://raw.githubusercontent.com/leeseifer/pi-tts-pack/main/install.sh | bash -s -- --full
```

The default public voice models download automatically during setup; there is no manual voice-pack upload. Pocket models may download on first use, and Pocket cloning may require your own model-access login. For a smaller install with only two Piper voices and transcription, add `--minimal`. `--all-voices` remains accepted for compatibility; the 21-voice Piper pack is already the default.

## Help

[Pi setup and options](PI5_SETUP.md) · [Mac Docker test](DOCKER_TEST.md) · [Claude MCP](CLAUDE_MCP.md) · [API reference](docs/API.md) · [Optional n8n](n8n/README.md) · [Credits](docs/CREDITS.md)

Run service commands on the Pi:

```bash
sudo systemctl restart pi-tts-pack
journalctl -u pi-tts-pack -f
```

Source code: GPL-3.0-or-later. Models are downloaded from upstream and retain their own licenses; see [credits](docs/CREDITS.md).
