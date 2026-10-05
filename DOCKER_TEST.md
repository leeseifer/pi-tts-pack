# PI TTS Pack: test on your Mac with Docker

These commands run **on your Mac**, with Docker Desktop open. The native [Pi installer](PI5_SETUP.md) still runs on the Raspberry Pi.

The container uses **Linux ARM64, Debian Bookworm, and Python 3.11**, matching the Pi's software architecture. On an Apple Silicon Mac it runs ARM64 directly; Docker Desktop can also emulate ARM64 on an Intel Mac. This tests the application and dependencies, rather than the Pi's physical hardware or performance. [Docker platform documentation](https://docs.docker.com/build/building/multi-platform/).

## One command from the project folder

```bash
bash docker-test.sh
```

The command builds the image and automatically downloads **21 Piper voices, Vietnamese VieNeu presets, UK/US Kokoro presets, and Whisper base/small** into the Docker volume. It prepares the default models before starting the service, waits for a healthy service, and runs real audio and MCP tests. First startup needs internet and several minutes; later starts reuse downloaded models. Allow 10–12 GB free for the image, model volume, and build workspace.

When it prints **Ready!**, open:

- **Voice studio:** http://localhost:5051
- **MCP:** http://localhost:5051/mcp
- **API documentation:** http://localhost:5051/docs

It saves a test report and playable samples in `docker-test-results/`. The test generates speech with **every one of the 21 Piper voices**, Vietnamese VieNeu, British Kokoro Emma, and American Kokoro Michael. It also checks the web UI, MP3 encoding, MCP discovery and speech generation, Whisper transcription, voice changing, and downloading saved MCP audio through the Mac's exposed port after a container restart.

The container keeps models, generated audio, uploads and new clones in its own persistent volume. Local extraction files and personal recordings are excluded from the image. Port 5051 is bound to the Mac's loopback interface for local testing.

## Connect Claude Code on this Mac

```bash
claude mcp add --scope user --transport http pi-tts-pack-test http://localhost:5051/mcp
```

Restart Claude Code and check `/mcp`. For Claude Desktop, follow [CLAUDE_MCP.md](CLAUDE_MCP.md), replacing its Pi address with `http://localhost:5051/mcp`.

## Start, stop, and inspect

```bash
# Start again; downloaded models and generated audio survive.
docker compose up -d --wait --wait-timeout 1200

# Watch progress during the first model download.
docker compose logs -f pi-tts-pack

# Stop the test container and keep its data volume.
docker compose down
```

The first-start wait uses Docker Compose's health checks, so **running** alone does not count as a successful test. [Compose wait option](https://docs.docker.com/reference/cli/docker/compose/up/).

## Optional settings

If another app uses port 5051:

```bash
PI_TTS_HTTP_PORT=5052 bash docker-test.sh
```

Keep using that variable on later Compose commands for this installation. The printed UI and MCP links use the selected port.

The default image includes Piper, Whisper, VieNeu, and Kokoro. To add Pocket and Supertonic:

```bash
PI_TTS_PACK_FULL=1 bash docker-test.sh
```

Supertonic models are prepared at startup. Pocket models may download when first used, and gated Pocket cloning needs your own model-access account. The acceptance check covers the default pack; it does not verify every additional Pocket or Supertonic voice.

For a smaller image and download with only two Piper voices and Whisper:

```bash
PI_TTS_VOICE_PACK=0 PI_TTS_ALL_VOICES=0 bash docker-test.sh
```

`PI_TTS_ALL_VOICES=1` is already the default. Updating an existing Docker installation with `bash docker-test.sh` adds missing default voices and keeps your existing models and recordings.

If you enable a Docker API key, pass `PI_TTS_DOCKER_API_KEY` and `PI_TTS_DOCKER_KEY_ON_LAN=true` as environment variables, and configure the same key in your Claude client. The Pi's existing `.env` file is not loaded into this container.
