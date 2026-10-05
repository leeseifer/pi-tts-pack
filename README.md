# PI TTS Pack

**Easy to set up. Easy to use.** Turn a Raspberry Pi 5 into a local voice studio with text-to-speech, transcription, voice changing, a browser UI, and Claude MCP tools.

## One command to install

SSH into your Pi, then paste:

```bash
curl -fsSL https://raw.githubusercontent.com/leeseifer/pi-tts-pack/main/install.sh | bash
```

Use a 64-bit Raspberry Pi OS/Debian/Ubuntu installation with Python 3.11–3.13, internet access, and a normal account with sudo. Python 3.11 on Bookworm matches the source Pi. The installer asks for sudo if needed, downloads models, starts the boot service, and checks real audio and MCP. Existing settings and recordings survive a reinstall.

When it prints **Ready!**, open the **Web UI** address. Type your text, choose a voice, and press Generate. English and Vietnamese voices plus Whisper base/small are installed by default. If the default ports are occupied, it prints the alternatives it selected.

## Connect Claude Code

On your computer, replace `PI_IP` with your Pi's address and use the port printed by the installer:

```bash
claude mcp add --scope user --transport http pi-tts-pack http://PI_IP:5050/mcp
```

Restart your Claude Code session and check `/mcp`. Try: **“Use PI TTS Pack to say Hello in English and return the audio.”**

For **Claude Desktop**, use the local bridge in [CLAUDE_MCP.md](CLAUDE_MCP.md). Claude's cloud connectors need a publicly reachable server; they cannot directly reach a private Pi address.

## What you get

- Local text-to-speech in MP3, WAV, OGG, FLAC, and phone formats.
- Audio/video transcription and voice changing.
- Browser UI, REST API, and 11 MCP tools.
- Optional VieNeu, Pocket TTS, Kokoro, and Supertonic engines, plus new voice cloning.
- No saved personal clones, keys, or recordings in the public repository.

For optional engines, use the same installer with `--full`:

```bash
curl -fsSL https://raw.githubusercontent.com/leeseifer/pi-tts-pack/main/install.sh | bash -s -- --full
```

Models for optional engines may download on first use; Pocket cloning may require your own model-access login. Add `--all-voices` to download 21 upstream Piper catalog voices.

## Help

[Pi setup and options](PI5_SETUP.md) · [Claude MCP](CLAUDE_MCP.md) · [API reference](docs/API.md) · [Optional n8n](n8n/README.md) · [Credits](docs/CREDITS.md)

```bash
sudo systemctl restart pi-tts-pack
journalctl -u pi-tts-pack -f
```

Source code: GPL-3.0-or-later. Models are downloaded from upstream and retain their own licenses; see [credits](docs/CREDITS.md).
