# PI TTS Pack: Raspberry Pi 5 setup

**All commands in this guide run on the Raspberry Pi.** Use the Pi's own terminal or an SSH session connected to the Pi. Do not run these commands in your Mac's local terminal.

## Install with one command

On your Pi 5, use a 64-bit Raspberry Pi OS/Debian/Ubuntu installation with internet, Python 3.11–3.13, and a normal account with sudo access. Paste this one command into the Pi terminal:

```bash
curl -fsSL https://raw.githubusercontent.com/leeseifer/pi-tts-pack/main/install.sh | bash
```

The installer handles system packages, the Python environment, **21 Piper voices, Vietnamese VieNeu presets, UK/US Kokoro presets**, Whisper base/small, settings, and the `pi-tts-pack` boot service. It downloads the default voice models during installation, then generates actual Vietnamese and British/American English speech and checks MCP discovery. Default ports are 5050 and 5443; occupied defaults are moved to available alternatives and printed at the end.

The reference Pi uses Bookworm/Python 3.11. Current Raspberry Pi OS releases can use a newer Python; the installer accepts 3.11–3.13. Use 64-bit OS, not 32-bit. [Raspberry Pi OS documentation](https://www.raspberrypi.com/documentation/computers/os.html).

Allow 10–12 GB free for installation and download workspace, plus space for your recordings. The smaller `--minimal` install needs approximately 3 GB. Extra engines need additional space. Cooling and enough RAM for the engines you load help with sustained use.

## Use

1. Open the Web UI address printed under **Ready!**.
2. Type or paste text and choose a voice.
3. Press Generate, play the result, or download it.

For audio/video, upload a file and choose transcription or another voice. For a microphone, use the printed HTTPS address and grant browser microphone permission after handling the local self-signed certificate.

The MCP server runs on the Pi. Once installation is complete, use the separate [Claude connection guide](CLAUDE_MCP.md) to connect your Claude app to the printed MCP address.

## Optional installer flags

Run these on the Pi too. Append flags after `bash -s --`. Each example is still one command.

```bash
# Add Pocket and Supertonic; Pocket models may download on first use.
curl -fsSL https://raw.githubusercontent.com/leeseifer/pi-tts-pack/main/install.sh | bash -s -- --full

# Smaller download: two Piper voices + Whisper, without the extra voice engines.
curl -fsSL https://raw.githubusercontent.com/leeseifer/pi-tts-pack/main/install.sh | bash -s -- --minimal

# Choose ports yourself; HTTPS 0 disables that listener.
curl -fsSL https://raw.githubusercontent.com/leeseifer/pi-tts-pack/main/install.sh | bash -s -- --http-port 5051 --https-port 5444

# Check the host without installing anything.
curl -fsSL https://raw.githubusercontent.com/leeseifer/pi-tts-pack/main/install.sh | bash -s -- --check
```

The default directory is `~/pi-tts-pack`. `--dir /absolute/path` selects another empty directory. An existing unrelated directory is rejected. Rerunning the installer updates an unmodified Git checkout and preserves `.env`, clones, uploaded media, outputs, and models. Local code edits are retained and cause an update to stop instead of overwriting them.

The default voice pack uses a CPU PyTorch build. `--full` adds Pocket and Supertonic; Supertonic models are also prepared during setup. Pocket's upstream documentation explains its [CPU-only install](https://huggingface.co/kyutai/pocket-tts#cpu-only-installation). Optional-engine model access is configured with your own account; no source account credentials are distributed. `--all-voices` is still accepted, but all 21 Piper voices are already included by default.

## Settings and service

Edit `~/pi-tts-pack/.env`, then restart:

```bash
nano ~/pi-tts-pack/.env
sudo systemctl restart pi-tts-pack
```

The initial settings allow direct LAN requests. `PIPER_API_KEY` and `PIPER_KEY_ON_LAN=true` enable API-key checks on the LAN. Public API/MCP requests are refused while the key is empty. Keys and local media are ignored by Git.

Useful commands:

```bash
sudo systemctl status pi-tts-pack --no-pager
journalctl -u pi-tts-pack -n 80 --no-pager
journalctl -u pi-tts-pack -f
curl -fsS http://localhost:5050/health
~/pi-tts-pack/.venv/bin/python ~/pi-tts-pack/scripts/check_service.py http://localhost:5050 --env-file ~/pi-tts-pack/.env
```

Use the printed HTTP port if different. The last command verifies health, generates WAV audio, and checks the server's MCP identity and tools. It uses a configured key without printing it.

To stop startup on boot while keeping your files:

```bash
sudo systemctl disable --now pi-tts-pack
```

## Troubleshooting

| Problem | Action |
|---|---|
| Installer says unsupported system | Run the command in the Pi's terminal or an SSH session connected to the Pi. The Pi needs Linux ARM64, Python 3.11–3.13, and apt-get. |
| Package download fails | Check internet and free disk space; rerun the installer. |
| Destination already exists | Choose an empty directory with `--dir`. |
| Service cannot start | Read `journalctl -u pi-tts-pack -n 80`; check configured ports. |
| No voices appear | Rerun the installer to complete model downloads; reset language/type/search filters. Default models must finish downloading before setup prints Ready. |
| Want additional voices | Use **+ More voices** for the upstream Piper catalog, or install the additional engines with `--full`. |
| Claude cannot see tools | Check the exact printed MCP port and follow [CLAUDE_MCP.md](CLAUDE_MCP.md). |
| API returns 401 | Supply your new API key if enabled. |
| Microphone is blocked | Use HTTPS and allow microphone access. |
| Memory pressure | Use one Piper voice at a time, concurrency 1, and base/small transcription. |

The public Git repository contains source and setup files. Large voice/model files are obtained from upstream; they are not embedded in Git history.
