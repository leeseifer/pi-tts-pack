# PI TTS Pack: test on your Mac with Docker

These commands run **on your Mac**, with Docker Desktop open. The native [Pi installer](PI5_SETUP.md) still runs on the Raspberry Pi.

The container uses **Linux ARM64, Debian Bookworm, and Python 3.11**, matching the Pi's software architecture. On an Apple Silicon Mac it runs ARM64 directly; Docker Desktop can also emulate ARM64 on an Intel Mac. This tests the application and dependencies, rather than the Pi's physical hardware or performance. [Docker platform documentation](https://docs.docker.com/build/building/multi-platform/).

## One command from the project folder

```bash
bash docker-test.sh
```

The command builds the image, downloads English/Vietnamese Piper voices and Whisper base/small into a new Docker volume, waits for a healthy service, and runs real audio and MCP tests. First startup needs internet and several minutes; later starts reuse downloaded models.

When it prints **Ready!**, open:

- **Voice studio:** http://localhost:5051
- **MCP:** http://localhost:5051/mcp
- **API documentation:** http://localhost:5051/docs

It saves a test report and playable English/Vietnamese samples in `docker-test-results/`. The test covers the web UI, English and Vietnamese WAV generation, MP3 encoding, MCP discovery and speech generation, Whisper transcription, voice changing, and downloading saved MCP audio through the Mac's exposed port after a container restart.

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

The default image tests the core Piper and Whisper engines. To build the larger image with optional VieNeu, Pocket, Kokoro and Supertonic packages:

```bash
PI_TTS_PACK_FULL=1 bash docker-test.sh
```

Optional models may download when first used, and gated Pocket cloning needs your own model-access account. For all 21 catalog voices, add `PI_TTS_ALL_VOICES=1` before the command. An optional-engine build does not by itself verify every optional voice; the acceptance check exercises the core engines.

If you enable a Docker API key, pass `PI_TTS_DOCKER_API_KEY` and `PI_TTS_DOCKER_KEY_ON_LAN=true` as environment variables, and configure the same key in your Claude client. The Pi's existing `.env` file is not loaded into this container.
