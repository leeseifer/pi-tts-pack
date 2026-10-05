"""Exercise health, real audio synthesis, and MCP discovery against a running service."""
import argparse
import io
import json
import sys
import urllib.request
import wave
from pathlib import Path


def request(base, path, body=None, headers=None):
    headers = {'Accept': 'application/json, text/event-stream', **(headers or {})}
    data = None if body is None else json.dumps(body).encode()
    if data is not None:
        headers['Content-Type'] = 'application/json'
    req = urllib.request.Request(base.rstrip('/') + path, data=data, headers=headers)
    with urllib.request.urlopen(req, timeout=120) as response:
        content = response.read()
        response_headers = dict(response.headers.items())
    return content, response_headers


def rpc_json(content):
    if content.lstrip().startswith(b'{'):
        return json.loads(content)
    for line in content.decode().splitlines():
        if line.startswith('data: '):
            value = json.loads(line[6:])
            if 'result' in value or 'error' in value:
                return value
    raise ValueError('No MCP response received')


def check(base, key=None):
    headers = {'X-API-Key': key} if key else {}
    health = json.loads(request(base, '/health', headers=headers)[0])
    assert health['ok'] and health['voices'] > 0, health
    info = json.loads(request(base, '/api/clones', headers=headers)[0])
    audio = request(base, '/api/tts', {'text': 'PI TTS Pack is ready.', 'voice': 'en_US-lessac-medium', 'format': 'wav'}, headers)[0]
    with wave.open(io.BytesIO(audio)) as wav:
        duration = wav.getnframes() / wav.getframerate()
        assert duration > 0
    init, init_headers = request(base, '/mcp', {
        'jsonrpc': '2.0', 'id': 1, 'method': 'initialize',
        'params': {'protocolVersion': '2025-06-18', 'capabilities': {},
                   'clientInfo': {'name': 'pi-tts-pack-check', 'version': '1.0'}},
    }, headers)
    init = rpc_json(init)
    assert 'error' not in init, init
    server = init['result']['serverInfo']
    assert server['name'] == 'pi-tts-pack', server
    protocol = init['result']['protocolVersion']
    session = next((value for key, value in init_headers.items() if key.lower() == 'mcp-session-id'), None)
    mcp_headers = {**headers, 'MCP-Protocol-Version': protocol}
    if session:
        mcp_headers['Mcp-Session-Id'] = session
    request(base, '/mcp', {'jsonrpc': '2.0', 'method': 'notifications/initialized'}, mcp_headers)
    tools = rpc_json(request(base, '/mcp', {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list', 'params': {}}, mcp_headers)[0])
    names = {tool['name'] for tool in tools['result']['tools']}
    assert {'list_voices', 'text_to_speech', 'transcribe'} <= names, names
    print(json.dumps({'health': 'ok', 'audio_seconds': round(duration, 2), 'mcp_server': server['name'],
                      'mcp_tools': len(names), 'personal_clones': len(info['clones'])}, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('url')
    parser.add_argument('--env-file', type=Path)
    args = parser.parse_args()
    key = None
    if args.env_file:
        sys.path.insert(0, str(Path(__file__).parent))
        from configure import read_env
        key = read_env(args.env_file).get('PIPER_API_KEY') or None
    check(args.url, key)
