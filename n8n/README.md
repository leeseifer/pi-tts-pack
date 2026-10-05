# Optional n8n voice chat

This integration is separate from Piper's own web UI. [PI5_SETUP.md](../PI5_SETUP.md) does not install n8n, nginx, a reverse proxy, or an OpenAI account.

The original generated workflows and their access code were removed. The builder no longer includes the old OpenAI credential ID.

With an existing n8n installation, generate workflows using a new access code and a Piper address reachable from n8n:

```bash
cd ~/pi-tts-pack/n8n
PIPER_URL='http://PI_IP:5050' python3 build_workflow.py 'YOUR_NEW_ACCESS_CODE'
```

If n8n runs in Docker, `127.0.0.1` refers to the container; use the Pi's LAN address. The builder assumes LAN access without Piper key enforcement. If Piper requires a key, configure its HTTP-request nodes to send your new `X-API-Key` header after import.

Import the workflow and select your own OpenAI credential on its model node. The test workflow uses a stub reply and needs no OpenAI credential. Check the nodes against your installed n8n version before activating; the source builder targets n8n 2.28.

Serve `site/index.html` through your own HTTPS web server at `/voice`, on the same origin as n8n. It calls `/webhook/voice-chat/...`. Browser microphone access needs HTTPS; serving the embedded page directly through a sandboxed n8n webhook can restrict microphone access.
