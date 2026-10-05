"""Prepare the persistent data volume; reuse complete Whisper snapshots offline."""
import os
from pathlib import Path
from faster_whisper.utils import download_model
from huggingface_hub.errors import LocalEntryNotFoundError
from scripts.download_models import download

root = Path(os.environ.get('PIPER_HOME', '/data'))
full = os.environ.get('PI_TTS_PACK_FULL', '0') == '1'
print('Preparing PI TTS Pack models in', root, flush=True)
download(root, all_voices=os.environ.get('PI_TTS_ALL_VOICES', '0') == '1', whisper=False, full=full)
for model in ['base', 'small']:
    try:
        cached = Path(download_model(model, cache_dir=str(root / 'models'), local_files_only=True))
        if not all((cached / name).is_file() for name in ['config.json', 'model.bin', 'tokenizer.json']):
            raise FileNotFoundError('Incomplete Whisper snapshot')
        print(f'Whisper {model} ready in persistent cache', flush=True)
    except (LocalEntryNotFoundError, FileNotFoundError):
        print(f'Downloading Whisper {model}; first start only', flush=True)
        download_model(model, cache_dir=str(root / 'models'))
print('Models ready. Starting web UI and MCP.', flush=True)
