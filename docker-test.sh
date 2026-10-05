#!/usr/bin/env bash
# Build a Pi-style ARM64 Linux container locally; no SSH or Pi changes.
set -Eeuo pipefail
cd "$(dirname "$0")"
command -v docker >/dev/null || { printf 'Install and open Docker Desktop first.\n' >&2; exit 1; }
docker info >/dev/null 2>&1 || { printf 'Open Docker Desktop, wait for it to start, then rerun.\n' >&2; exit 1; }
docker compose version >/dev/null
RESULTS_DIR="${PI_TTS_TEST_RESULTS:-docker-test-results}"
HTTP_PORT="${PI_TTS_HTTP_PORT:-5051}"
trap 'printf "\nTest stopped. Check: docker compose logs --tail 80\n" >&2' ERR
printf 'Building and starting PI TTS Pack in Linux ARM64 on this computer.\n'
docker compose up --build -d --wait --wait-timeout 1200
printf '\nChecking real speech, MP3, MCP, transcription, and voice changing.\n'
docker compose exec -T pi-tts-pack python scripts/check_container.py http://127.0.0.1:5050
HOST_AUDIO_URL="$(docker compose exec -T pi-tts-pack python -c "import json; print(json.load(open('/data/test-results/results.json'))['host_audio_url'])")"
printf '\nChecking cached models and saved audio after a container restart.\n'
docker compose restart pi-tts-pack
docker compose up -d --wait --wait-timeout 180
mkdir -p "$RESULTS_DIR"
if [[ -n "${PI_TTS_DOCKER_API_KEY:-}" ]]; then
  curl -fsS -H "X-API-Key: $PI_TTS_DOCKER_API_KEY" "$HOST_AUDIO_URL" -o "$RESULTS_DIR/host-mcp-speech.wav"
else
  curl -fsS "$HOST_AUDIO_URL" -o "$RESULTS_DIR/host-mcp-speech.wav"
fi
docker compose exec -T pi-tts-pack python -c "import json; from pathlib import Path; p=Path('/data/test-results/results.json'); r=json.loads(p.read_text()); r['restart_and_persistence']='passed'; p.write_text(json.dumps(r,ensure_ascii=False,indent=2))"
docker compose cp pi-tts-pack:/data/test-results/. "$RESULTS_DIR/"
printf '\nReady! Mac test UI: http://localhost:%s\nMCP: http://localhost:%s/mcp\nResults and audio: %s\n' "$HTTP_PORT" "$HTTP_PORT" "$RESULTS_DIR"
