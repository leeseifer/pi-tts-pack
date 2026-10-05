# Connect PI TTS Pack to Claude

First, [install the pack on your Raspberry Pi](PI5_SETUP.md). The install command runs in the Pi's terminal or an SSH session connected to it.

This separate guide connects your Claude app to the service already running on the Pi. Use the **MCP address printed by the installer**. Examples below use `http://PI_IP:5050/mcp`; substitute your address and selected port. Your Claude client must be able to reach the Pi.

## Claude Code: one command

Run on the computer where you use Claude Code:

```bash
claude mcp add --scope user --transport http pi-tts-pack http://PI_IP:5050/mcp
```

Restart your Claude Code session and run `/mcp` to confirm the server. `--scope user` makes it available across your projects. This uses Claude Code's documented [HTTP MCP transport](https://code.claude.com/docs/en/mcp).

If you enabled a LAN API key:

```bash
claude mcp add --scope user --transport http pi-tts-pack http://PI_IP:5050/mcp --header "X-API-Key: YOUR_KEY"
```

## Claude Desktop: local network bridge

Use the desktop **local MCP configuration**, so the connection originates from your computer and can reach the Pi. Have Node.js/npm available on that computer. The bridge below uses `mcp-remote` to connect a local stdio client to the pack's HTTP endpoint. [Bridge documentation](https://github.com/punkpeye/mcp-remote).

Open Claude Desktop's Developer settings and edit `claude_desktop_config.json`. [Official local-server setup](https://modelcontextprotocol.io/docs/develop/connect-local-servers). Merge this server entry into `mcpServers` without deleting any existing servers:

```json
{
  "mcpServers": {
    "pi-tts-pack": {
      "command": "npx",
      "args": ["-y", "mcp-remote", "http://PI_IP:5050/mcp", "--allow-http"]
    }
  }
}
```

The `--allow-http` option is for the direct HTTP connection on your trusted LAN. A ready-to-edit example is in [examples/claude_desktop_config.json](examples/claude_desktop_config.json).

Common configuration locations:

- macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`
- Windows: `%APPDATA%\Claude\claude_desktop_config.json`

Quit and reopen Claude Desktop, then look for **pi-tts-pack** in its local MCP tools. If `npx` cannot be found, use its absolute path from `which npx` on macOS/Linux or `where npx` on Windows. For LAN key enforcement, add `"--header", "X-API-Key:YOUR_KEY"` to the `args` array.

## Claude web / cloud connectors

Claude's custom connectors are reached from Anthropic's cloud. They cannot connect directly to private addresses such as `192.168.x.x`. The desktop local bridge above and Claude Code work from your network. A cloud connector instead needs your own publicly reachable HTTPS endpoint and appropriate authentication; the one-command installer does not create a public tunnel. [Anthropic's network requirements](https://support.claude.com/en/articles/11175166-get-started-with-custom-connectors-using-remote-mcp).

## Try it

- “List the English and Vietnamese voices available in PI TTS Pack.”
- “Say Welcome back in English and return the MP3 audio.”
- “Transcribe this audio URL using PI TTS Pack.”
- “Change this video's narration to the selected voice.”

MCP tool names include `list_voices`, `text_to_speech`, `transcribe`, `speech_to_speech`, `voice_catalog`, `install_voice`, `clone_voice`, `delete_cloned_voice`, `phone_audio`, `phone_call`, and `list_formats`. Voice cloning requires consent; phone calls require your own Twilio settings. Ask for `include_audio=true` with `text_to_speech` when you want Claude to receive audio inline.

The installer verifies an MCP handshake and tool discovery. Actual visibility in your Claude app is confirmed by the client checks above.
