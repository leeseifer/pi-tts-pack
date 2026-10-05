# Credits and model sources

PI TTS Pack combines a local voice application with these upstream projects:

- [Piper](https://github.com/OHF-Voice/piper1-gpl) — text-to-speech engine, GPLv3.
- [Piper voices](https://huggingface.co/rhasspy/piper-voices) — downloaded models; consult each voice MODEL_CARD for its license. [Upstream voice documentation](https://github.com/OHF-Voice/piper1-gpl/blob/main/docs/VOICES.md).
- [faster-whisper](https://github.com/SYSTRAN/faster-whisper) and [converted Whisper models](https://huggingface.co/Systran) — transcription.
- [MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk) — MCP transport and tools.
- [VieNeu-TTS](https://github.com/pnnbao97/VieNeu-TTS), [Pocket TTS](https://huggingface.co/kyutai/pocket-tts), [Kokoro ONNX](https://github.com/thewh1teagle/kokoro-onnx), and [Supertonic](https://github.com/supertone-inc/supertonic) — optional engines.

The source code in this repository is licensed under GPL-3.0-or-later; see [LICENSE](../LICENSE). Dependencies and models retain their own licenses. Model weights and copied personal data are excluded from the public Git repository. The installer downloads requested models from their original sources.
