"""Live ARM64 container acceptance: real REST/MCP audio, STT and voice changing."""
import argparse
import array
import io
import json
import os
import platform
import re
import subprocess
import urllib.parse
import wave
from pathlib import Path
from check_service import request, rpc_json


def wav_info(data):
    with wave.open(io.BytesIO(data)) as stream:
        seconds = stream.getnframes() / stream.getframerate()
        assert stream.getsampwidth() == 2 and seconds > 0.5, 'Invalid or empty WAV'
        samples = array.array('h', stream.readframes(stream.getnframes()))
        assert max(abs(value) for value in samples) > 100, 'Silent generated speech'
        return round(seconds, 2)


def check(base, output):
    assert platform.system() == 'Linux' and platform.machine() in ('aarch64', 'arm64'), 'Expected Linux ARM64'
    output.mkdir(parents=True, exist_ok=True)
    headers = {'X-API-Key': os.environ['PIPER_API_KEY']} if os.environ.get('PIPER_API_KEY') else {}
    health = json.loads(request(base, '/health', headers=headers)[0])
    assert health['ok'] and health['voices'] >= 2
    assert b'PI TTS Pack' in request(base, '/', headers=headers)[0]
    voices = json.loads(request(base, '/api/voices', headers=headers)[0])['voices']
    assert {'en_US-lessac-medium', 'vi_VN-vais1000-medium'} <= {voice['id'] for voice in voices}
    clones = json.loads(request(base, '/api/clones', headers=headers)[0])['clones']
    audio_results = {}
    for language, voice, text in [
        ('english', 'en_US-lessac-medium', 'Hello. This is a test of speech generation inside a Linux container.'),
        ('vietnamese', 'vi_VN-vais1000-medium', 'Xin chào, đây là bộ công cụ tạo giọng nói trên máy tính.'),
    ]:
        data = request(base, '/api/tts', {'text': text, 'voice': voice, 'format': 'wav'}, headers)[0]
        (output / f'{language}.wav').write_bytes(data)
        audio_results[language] = wav_info(data)
    mp3 = request(base, '/api/tts', {'text': 'PI TTS Pack creates MP3 files.',
                                    'voice': 'en_US-lessac-medium', 'format': 'mp3'}, headers)[0]
    (output / 'english.mp3').write_bytes(mp3)
    info = json.loads(subprocess.check_output(['ffprobe', '-v', 'error', '-show_entries',
                      'format=duration,format_name', '-of', 'json', str(output / 'english.mp3')]))
    assert info['format']['format_name'] == 'mp3' and float(info['format']['duration']) > .5
    initialized, init_headers = request(base, '/mcp', {'jsonrpc': '2.0', 'id': 1, 'method': 'initialize',
        'params': {'protocolVersion': '2025-06-18', 'capabilities': {},
                   'clientInfo': {'name': 'pi-tts-pack-container-check', 'version': '1.0'}}}, headers)
    result = rpc_json(initialized)['result']
    assert result['serverInfo']['name'] == 'pi-tts-pack'
    mcp_headers = {**headers, 'MCP-Protocol-Version': result['protocolVersion']}
    session = next((value for key, value in init_headers.items() if key.lower() == 'mcp-session-id'), None)
    if session:
        mcp_headers['Mcp-Session-Id'] = session
    request(base, '/mcp', {'jsonrpc': '2.0', 'method': 'notifications/initialized'}, mcp_headers)
    counter = 1

    def rpc(method, params):
        nonlocal counter
        counter += 1
        response = rpc_json(request(base, '/mcp', {'jsonrpc': '2.0', 'id': counter,
                          'method': method, 'params': params}, mcp_headers)[0])
        assert 'error' not in response, response
        return response['result']

    def call(name, arguments):
        response = rpc('tools/call', {'name': name, 'arguments': arguments})
        assert not response.get('isError'), response
        return json.loads(next(part['text'] for part in response['content'] if part['type'] == 'text'))

    def fetch_audio(meta, filename):
        # This test runs inside Docker. Public localhost links are checked from the Mac by docker-test.sh.
        url = urllib.parse.urlsplit(meta['url'])
        data = request(base, url.path + ('?' + url.query if url.query else ''), headers=headers)[0]
        (output / filename).write_bytes(data)
        return wav_info(data)

    tools = rpc('tools/list', {})['tools']
    assert {'list_voices', 'text_to_speech', 'transcribe', 'speech_to_speech'} <= {tool['name'] for tool in tools}
    listed = call('list_voices', {'language': 'vi'})
    assert listed['count'] >= 1
    speech = call('text_to_speech', {'text': 'Hello from the MCP server. This is a test of speech generation inside a Linux container.',
                   'voice': 'en_US-lessac-medium', 'format': 'wav'})
    audio_results['mcp'] = fetch_audio(speech, 'mcp-speech.wav')
    transcript = call('transcribe', {'source': speech['id'], 'language': 'en', 'whisper_model': 'base'})
    words = set(re.findall(r'[a-z]+', transcript['text'].lower()))
    assert {'test', 'speech', 'container'} <= words, transcript['text']
    revoiced = call('speech_to_speech', {'source': speech['id'], 'voice': 'en_US-lessac-medium',
                                       'speed': 1.15, 'format': 'wav', 'language': 'en', 'whisper_model': 'base'})
    audio_results['revoice'] = fetch_audio(revoiced, 'revoiced.wav')
    assert revoiced.get('text') and 'container' in revoiced['text'].lower(), revoiced
    report = {'status': 'passed', 'platform': f'{platform.system()}/{platform.machine()}',
              'python': platform.python_version(), 'version': health['version'], 'web_ui': 'passed',
              'voices': len(voices), 'personal_clones': len(clones), 'audio_seconds': audio_results,
              'mp3': 'passed', 'mcp_server': result['serverInfo']['name'], 'mcp_tools': len(tools),
              'transcription': transcript['text'], 'voice_changing': 'passed',
              'host_audio_url': speech['url'], 'history_id': speech['id']}
    (output / 'results.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('url')
    parser.add_argument('--output-dir', type=Path, default=Path('/data/test-results'))
    args = parser.parse_args()
    check(args.url, args.output_dir)
