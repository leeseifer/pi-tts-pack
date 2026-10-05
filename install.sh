#!/usr/bin/env bash
# PI TTS Pack — one-command installer for 64-bit Raspberry Pi OS / Debian / Ubuntu.
set -Eeuo pipefail

REPOSITORY="${PI_TTS_PACK_REPOSITORY:-https://github.com/leeseifer/pi-tts-pack.git}"
INSTALL_DIR="${PI_TTS_PACK_DIR:-$HOME/pi-tts-pack}"
FULL=0
ALL_VOICES=0
CHECK_ONLY=0
HTTP_PORT=""
HTTPS_PORT=""

usage() {
  cat <<'HELP'
PI TTS Pack — Easy to set up. Easy to use.
Usage: bash install.sh [--full] [--all-voices] [--check]
                       [--dir PATH] [--http-port PORT] [--https-port PORT]
Default: English + Vietnamese Piper, Whisper base/small, web UI, and Claude MCP.
--full        Install optional VieNeu, Pocket, Kokoro, and Supertonic packages.
--all-voices  Download 21 voices from the upstream Piper catalog.
--check       Check the host only; do not install or change anything.
--https-port  Use 0 to disable the optional HTTPS listener.
HELP
}

while (($#)); do
  case "$1" in
    --help|-h) usage; exit 0 ;;
    --full) FULL=1; shift ;;
    --all-voices) ALL_VOICES=1; shift ;;
    --check) CHECK_ONLY=1; shift ;;
    --dir) INSTALL_DIR="${2:?Missing path for --dir}"; shift 2 ;;
    --http-port) HTTP_PORT="${2:?Missing port for --http-port}"; shift 2 ;;
    --https-port) HTTPS_PORT="${2:?Missing port for --https-port}"; shift 2 ;;
    *) printf 'Unknown option: %s\n' "$1" >&2; usage; exit 2 ;;
  esac
done

fail() { printf '\n%s\n' "$*" >&2; exit 1; }
[[ "$(uname -s)" == Linux ]] || fail 'Run the install command on your Raspberry Pi, not on macOS or Windows.'
case "$(uname -m)" in aarch64|arm64) ;; *) fail 'Use a 64-bit ARM operating system (aarch64) on the Pi.' ;; esac
[[ "$(id -u)" != 0 ]] || fail 'Run as your normal Pi account; the installer uses sudo for system setup.'
command -v python3 >/dev/null || fail 'Python 3 is required. Install python3 first.'
command -v apt-get >/dev/null || fail 'Use Raspberry Pi OS, Debian, or Ubuntu with apt-get.'
[[ "$INSTALL_DIR" == /* && "$INSTALL_DIR" != / && ! "$INSTALL_DIR" =~ [[:space:]\|] ]] || fail 'Choose an absolute installation path without spaces or | characters.'
for port in "$HTTP_PORT" "$HTTPS_PORT"; do
  [[ -z "$port" || "$port" =~ ^[0-9]+$ ]] || fail 'Ports must be numbers.'
done
python3 - <<'PY'
import sys
if not (3, 11) <= sys.version_info[:2] <= (3, 13):
    raise SystemExit('Use Python 3.11 to 3.13; the source Pi uses Python 3.11.')
PY
if ((CHECK_ONLY)); then
  printf 'Host check passed: Linux ARM64, Python 3.11–3.13, normal user, apt-get.\n'
  printf 'Install directory: %s\n' "$INSTALL_DIR"
  exit 0
fi
trap 'printf "\nSetup stopped. The last error above explains which step failed. Rerun this command after fixing it.\n" >&2' ERR

printf '\nPI TTS Pack — Easy to set up. Easy to use.\n[1/6] Preparing system packages\n'
sudo -v
sudo apt-get update
sudo apt-get install -y git curl python3-venv python3-pip python3-dev \
  build-essential pkg-config ffmpeg libsndfile1 espeak-ng libespeak-ng1 \
  libgomp1 openssl ca-certificates

printf '\n[2/6] Getting PI TTS Pack\n'
if [[ -d "$INSTALL_DIR/.git" ]]; then
  ORIGIN="$(git -C "$INSTALL_DIR" remote get-url origin)"
  [[ "$ORIGIN" == "$REPOSITORY" ]] || fail 'This folder belongs to another repository. Choose a new --dir.'
  git -C "$INSTALL_DIR" diff --quiet && git -C "$INSTALL_DIR" diff --cached --quiet || fail 'This installation has local code edits. Keep them or choose a new --dir.'
  git -C "$INSTALL_DIR" pull --ff-only
elif [[ -e "$INSTALL_DIR" ]]; then
  fail 'The destination already exists and is not a managed installation. Choose a new --dir.'
else
  mkdir -p "$(dirname "$INSTALL_DIR")"
  git clone --depth 1 "$REPOSITORY" "$INSTALL_DIR"
fi
cd "$INSTALL_DIR"

printf '\n[3/6] Installing Python packages\n'
[[ -x .venv/bin/python ]] || python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip setuptools wheel
if ((FULL)); then
  .venv/bin/python -m pip install 'torch==2.14.0+cpu' --index-url https://download.pytorch.org/whl/cpu
  .venv/bin/python -m pip install -r requirements-pi5.txt --extra-index-url https://download.pytorch.org/whl/cpu
else
  .venv/bin/python -m pip install -r requirements-core.txt
fi
.venv/bin/python -m pip check

printf '\n[4/6] Preparing settings and voice models\n'
CONFIG_ARGS=()
[[ -z "$HTTP_PORT" ]] || CONFIG_ARGS+=(--http-port "$HTTP_PORT")
[[ -z "$HTTPS_PORT" ]] || CONFIG_ARGS+=(--https-port "$HTTPS_PORT")
read -r HTTP_PORT HTTPS_PORT <<< "$(.venv/bin/python scripts/configure.py "$INSTALL_DIR" "${CONFIG_ARGS[@]}")"
MODEL_ARGS=()
((ALL_VOICES == 0)) || MODEL_ARGS+=(--all-voices)
((FULL == 0)) || MODEL_ARGS+=(--full)
.venv/bin/python scripts/download_models.py "$INSTALL_DIR" "${MODEL_ARGS[@]}"

printf '\n[5/6] Starting the service\n'
UNIT_FILE="$(mktemp)"
sed -e "s|@PIPER_USER@|$(id -un)|g" -e "s|@PIPER_DIR@|$INSTALL_DIR|g" \
  pi-tts-pack.service > "$UNIT_FILE"
sudo install -m 644 "$UNIT_FILE" /etc/systemd/system/pi-tts-pack.service
rm -f "$UNIT_FILE"
sudo systemctl daemon-reload
sudo systemctl enable pi-tts-pack.service
sudo systemctl restart pi-tts-pack.service
READY=0
for ((attempt=1; attempt<=60; attempt++)); do
  if curl -fsS "http://127.0.0.1:$HTTP_PORT/health" >/dev/null 2>&1; then READY=1; break; fi
  sleep 1
done
if ((READY == 0)); then
  sudo journalctl -u pi-tts-pack.service -n 40 --no-pager
  fail 'The service did not become ready. Check the log above.'
fi
printf '\n[6/6] Checking real audio and MCP\n'
.venv/bin/python scripts/check_service.py "http://127.0.0.1:$HTTP_PORT" --env-file .env
LAN_IP="$(hostname -I | awk '{print $1}')"
LAN_IP="${LAN_IP:-127.0.0.1}"
printf '\nReady!\nWeb UI: http://%s:%s\nMCP: http://%s:%s/mcp\n' "$LAN_IP" "$HTTP_PORT" "$LAN_IP" "$HTTP_PORT"
if ((HTTPS_PORT)); then printf 'Microphone UI: https://%s:%s\n' "$LAN_IP" "$HTTPS_PORT"; fi
printf '\nConnect Claude Code from your computer:\nclaude mcp add --scope user --transport http pi-tts-pack http://%s:%s/mcp\n' "$LAN_IP" "$HTTP_PORT"
printf '\nClaude Desktop instructions: %s/CLAUDE_MCP.md\n' "$INSTALL_DIR"
