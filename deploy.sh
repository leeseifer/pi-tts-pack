#!/usr/bin/env bash
# Sync Piper Studio to the Pi and restart the service.
set -euo pipefail
HOST="${PI_HOST:?Set PI_HOST to your Pi account and address, for example user@192.168.1.50}"
REMOTE_DIR="${PI_REMOTE_DIR:?Set PI_REMOTE_DIR to the existing project folder under the Pi account home directory}"
[[ "$REMOTE_DIR" =~ ^[A-Za-z0-9_.-]+$ ]] || { echo "PI_REMOTE_DIR must be a simple folder name" >&2; exit 1; }
cd "$(dirname "$0")"
python3 - <<'PY'
import ast
from pathlib import Path
for path in Path('app').glob('*.py'):
    ast.parse(path.read_text(), filename=str(path))
PY
rsync -az --exclude __pycache__ app requirements.txt requirements-pi5.txt requirements-pi5-constraints.txt env.example pi-tts-pack.service README.md PI5_SETUP.md CLAUDE_MCP.md scripts deploy.sh "$HOST:$REMOTE_DIR/"
ssh -o BatchMode=yes "$HOST" 'sudo systemctl restart pi-tts-pack && for i in $(seq 1 40); do curl -sf localhost:5050/health && exit 0; sleep 1; done; journalctl -u pi-tts-pack -n 40 --no-pager; exit 1'
echo
