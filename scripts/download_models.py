"""Fetch models from their upstream projects; model weights stay outside Git."""
import argparse
import os
import subprocess
import sys
from pathlib import Path

STARTER_VOICES = ['en_US-lessac-medium', 'vi_VN-vais1000-medium']
ALL_VOICES = [
    'de_DE-thorsten-high', 'en_GB-alan-medium', 'en_GB-cori-high',
    'en_GB-jenny_dioco-medium', 'en_GB-northern_english_male-medium',
    'en_US-amy-medium', 'en_US-hfc_female-medium', 'en_US-hfc_male-medium',
    'en_US-joe-medium', 'en_US-john-medium', 'en_US-kristin-medium',
    'en_US-lessac-medium', 'en_US-libritts_r-medium', 'en_US-norman-medium',
    'en_US-ryan-high', 'es_ES-davefx-medium', 'fr_FR-siwis-medium',
    'vi_VN-25hours_single-low', 'vi_VN-vais1000-medium',
    'vi_VN-vivos-x_low', 'zh_CN-huayan-medium',
]


def download(root, all_voices=False, whisper=True, full=False):
    voices = root / 'voices'
    voices.mkdir(parents=True, exist_ok=True)
    for voice in ALL_VOICES if all_voices else STARTER_VOICES:
        if (voices / f'{voice}.onnx').exists() and (voices / f'{voice}.onnx.json').exists():
            print(f'Voice ready: {voice}', flush=True)
            continue
        subprocess.run([sys.executable, '-m', 'piper.download_voices', '--download-dir', str(voices), voice], check=True)
    if whisper:
        from faster_whisper.utils import download_model
        for model in ['base', 'small']:
            print(f'Preparing Whisper {model}', flush=True)
            download_model(model, cache_dir=str(root / 'models'))
    if full:
        # These two engines need local preset assets before they appear in the UI.
        # Separate processes release each loaded model before preparing the next.
        for engine in ['KokoroEngine', 'SupertonicEngine']:
            print(f'Preparing {engine} preset models', flush=True)
            subprocess.run([sys.executable, '-c', f'from app.clones import {engine}; {engine}()._load()'],
                           cwd=root, env={**os.environ, 'PIPER_HOME': str(root.resolve())}, check=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('directory', type=Path)
    parser.add_argument('--all-voices', action='store_true')
    parser.add_argument('--skip-whisper', action='store_true')
    parser.add_argument('--full', action='store_true')
    args = parser.parse_args()
    download(args.directory, args.all_voices, not args.skip_whisper, args.full)
