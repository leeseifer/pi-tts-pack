"""Configure a new install while preserving existing settings and runtime data."""
import argparse
import socket
from pathlib import Path


def read_env(path):
    values = {}
    if path.exists():
        for line in path.read_text().splitlines():
            if line.strip() and not line.lstrip().startswith('#') and '=' in line:
                key, value = line.split('=', 1)
                values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def free_port(preferred, used):
    for port in range(preferred, min(preferred + 100, 65536)):
        if port in used:
            continue
        with socket.socket() as sock:
            try:
                sock.bind(('0.0.0.0', port))
            except OSError:
                continue
        return port
    raise ValueError(f'No available port near {preferred}; choose an explicit port.')


def configure(root, http_port=None, https_port=None):
    path = root / '.env'
    text = path.read_text() if path.exists() else (root / 'env.example').read_text()
    old = read_env(path)
    # Reinstalls keep ports: the running service may own them.
    http = http_port if http_port is not None else int(old.get('PIPER_PORT') or free_port(5050, set()))
    https = https_port if https_port is not None else (
        int(old['PIPER_HTTPS_PORT']) if 'PIPER_HTTPS_PORT' in old else free_port(5443, {http})
    )
    if not 1 <= http <= 65535 or not 0 <= https <= 65535 or http == https:
        raise ValueError('HTTP and HTTPS ports must be valid and different; HTTPS 0 disables it.')
    updates = {'PIPER_PORT': str(http), 'PIPER_HTTPS_PORT': str(https)}
    if not path.exists():
        updates.update({'TTS_CONCURRENCY': '1', 'PIPER_MAX_LOADED_VOICES': '4'})
    lines, seen = [], set()
    for line in text.splitlines():
        key = line.split('=', 1)[0].strip() if '=' in line and not line.lstrip().startswith('#') else None
        if key in updates:
            line = f'{key}={updates[key]}'
            seen.add(key)
        lines.append(line)
    lines.extend(f'{key}={value}' for key, value in updates.items() if key not in seen)
    path.write_text('\n'.join(lines) + '\n')
    path.chmod(0o600)
    return http, https


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('directory', type=Path)
    parser.add_argument('--http-port', type=int)
    parser.add_argument('--https-port', type=int)
    args = parser.parse_args()
    print(*configure(args.directory, args.http_port, args.https_port))
