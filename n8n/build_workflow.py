"""Build the n8n "Voice Chat" workflow (and a test variant with a stub AI) for n8n 2.28.

Usage: python3 build_workflow.py <access_code> [out_dir]
"""

import json
import os
import sys
import uuid
from pathlib import Path

HERE = Path(__file__).parent
PIPER = os.environ.get("PIPER_URL", "http://PI_IP:5050")
# Select your own OpenAI credential in n8n after importing the workflow.
OPENAI_CRED = {}

SYSTEM_PROMPT = """You are a friendly, helpful voice assistant. The user talks to you by voice and your reply is read aloud by a text-to-speech voice.
Rules:
- Reply in the same language the user used (Vietnamese or English).
- Keep it short and conversational: 1–3 sentences unless the user asks for detail.
- Plain spoken text only: no markdown, bullet points, emojis, URLs or code.
- Write numbers, dates and abbreviations the way they should be spoken.
- If the message is "[no speech detected]", kindly ask the user to say it again."""


def nid() -> str:
    return str(uuid.uuid4())


def build(access_code: str, stub_ai: bool = False) -> dict:
    html = (HERE / "voice-chat.html").read_text(encoding="utf-8")
    nodes, connections = [], {}

    def node(name, type_, version, params, pos, **extra):
        nodes.append({"parameters": params, "id": nid(), "name": name, "type": type_, "typeVersion": version,
                      "position": pos, **extra})
        return name

    def link(src, dst, kind="main", out=0):
        conns = connections.setdefault(src, {}).setdefault(kind, [])
        while len(conns) <= out:
            conns.append([])
        conns[out].append({"node": dst, "type": kind, "index": 0})

    webhook = "n8n-nodes-base.webhook"
    respond = "n8n-nodes-base.respondToWebhook"
    http = "n8n-nodes-base.httpRequest"
    code = "n8n-nodes-base.code"
    if_ = "n8n-nodes-base.if"

    def bool_if(name, expr, pos):
        return node(name, if_, 2.3, {
            "conditions": {
                "options": {"caseSensitive": True, "leftValue": "", "typeValidation": "strict", "version": 3},
                "conditions": [{"id": nid(), "leftValue": expr, "rightValue": "",
                                "operator": {"type": "boolean", "operation": "true", "singleValue": True}}],
                "combinator": "and"},
            "options": {}}, pos)

    # --- 1. the chat page -------------------------------------------------
    node("Chat page", webhook, 2.1, {"path": "voice-chat", "responseMode": "responseNode", "options": {}},
         [0, 0], webhookId=nid())
    node("Send page", respond, 1.5, {
        "respondWith": "text", "responseBody": html,
        "options": {"responseHeaders": {"entries": [{"name": "Content-Type", "value": "text/html; charset=utf-8"},
                                                    {"name": "Cache-Control", "value": "no-cache"}]}}}, [260, 0])
    link("Chat page", "Send page")

    # --- 2. voice list ----------------------------------------------------
    node("Voices", webhook, 2.1, {"path": "voice-chat/voices", "responseMode": "responseNode", "options": {}},
         [0, 220], webhookId=nid())
    node("Get voices", http, 4.4, {"url": f"{PIPER}/api/voices", "options": {"timeout": 20000}}, [260, 220])
    node("Voice options", code, 2, {"jsCode": r"""
const engines = { vieneu: 'VieNeu', pocket: 'Pocket', kokoro: 'Kokoro (narration)', supertonic: 'Supertonic' };
const out = [];
for (const v of $input.first().json.voices) {
  const group = v.kind === 'clone' ? 'My cloned voices'
    : `${v.language_name}${v.country ? ' · ' + v.country : ''}${engines[v.engine] ? ' · ' + engines[v.engine] : ''}`;
  if (v.speakers && v.speakers.length) {
    for (const s of v.speakers) out.push({ lang: v.language_family, group, value: `${v.id}|${s}`, label: `${v.name} · ${s.replace(/_/g, ' ')}` });
  } else {
    out.push({ lang: v.language_family, group, value: `${v.id}|`, label: v.name + (v.kind === 'clone' ? ' 🧬' : '') });
  }
}
return [{ json: { voices: out } }];
"""}, [520, 220])
    node("Send voices", respond, 1.5, {"respondWith": "json", "responseBody": "={{ $json }}", "options": {}}, [780, 220])
    link("Voices", "Get voices")
    link("Get voices", "Voice options")
    link("Voice options", "Send voices")

    # --- 3. talk ----------------------------------------------------------
    node("Talk", webhook, 2.1, {"httpMethod": "POST", "path": "voice-chat/talk", "responseMode": "responseNode",
                                "options": {}}, [0, 520], webhookId=nid())
    node("Prepare", code, 2, {"jsCode": f"""
// ▶ Change the access code here (the chat page asks for it once). Empty string = no code needed.
const ACCESS_CODE = {json.dumps(access_code)};
const DEFAULT_VOICE_VI = 'vi_VN-vais1000-medium|';
const DEFAULT_VOICE_EN = 'en_US-lessac-medium|';

const item = $input.first();
const h = item.json.headers || {{}};
const b = item.json.body || {{}};
const supplied = h['x-access-code'] || b.access_code || '';
return [{{
  json: {{
    authorized: !ACCESS_CODE || supplied === ACCESS_CODE,
    has_audio: !!(item.binary && item.binary.audio),
    user_text: String(b.text || '').trim(),
    session_id: String(b.session_id || 'default').slice(0, 64),
    voice_vi: b.voice_vi || DEFAULT_VOICE_VI,
    voice_en: b.voice_en || DEFAULT_VOICE_EN,
    speak_language: ['vi', 'en'].includes(b.language) ? b.language : '',   // '' = auto (vi or en)
  }},
  binary: item.binary,
}}];
"""}, [220, 520])
    bool_if("Authorized?", "={{ $json.authorized }}", [440, 520])
    node("Reject", respond, 1.5, {"respondWith": "json", "responseBody": '={{ { "error": "Wrong access code" } }}',
                                  "options": {"responseCode": 401}}, [660, 700])
    bool_if("Spoken?", "={{ $json.has_audio }}", [660, 460])
    node("Transcribe", http, 4.4, {
        "method": "POST", "url": f"{PIPER}/api/transcribe", "sendBody": True, "contentType": "multipart-form-data",
        "bodyParameters": {"parameters": [
            {"parameterType": "formBinaryData", "name": "file", "inputDataFieldName": "audio"},
            {"parameterType": "formData", "name": "whisper_model", "value": "auto"},
            {"parameterType": "formData", "name": "language", "value": "={{ $json.speak_language }}"},
            {"parameterType": "formData", "name": "languages", "value": "vi,en"}]},
        "options": {"timeout": 180000}}, [880, 380])
    node("Pick voice", code, 2, {"jsCode": r"""
// Runs after Transcribe (spoken) or straight from Prepare (typed): choose the reply voice by language.
const p = $('Prepare').first().json;
const input = $input.first().json;
const spoken = Array.isArray(input.segments);
const text = spoken ? String(input.text || '').trim() : p.user_text;
const vietnamese = /[ăâđêôơưáàảãạấầẩẫậắằẳẵặéèẻẽẹếềểễệíìỉĩịóòỏõọốồổỗộớờởỡợúùủũụứừửữựýỳỷỹỵ]/i;
const language = spoken ? (input.language || 'en') : (vietnamese.test(text) ? 'vi' : 'en');
const [voice, speaker] = String(language === 'vi' ? p.voice_vi : p.voice_en).split('|');
return [{ json: { user_text: text, language, voice, speaker: speaker || null, session_id: p.session_id, spoken } }];
"""}, [1100, 520])

    if stub_ai:
        node("AI Agent", code, 2, {"jsCode": r"""
const t = $input.first().json.user_text;
return [{ json: { output: t ? `Test reply. You said: ${t}` : 'Test reply. I did not hear anything.' } }];
"""}, [1320, 520])
    else:
        node("AI Agent", "@n8n/n8n-nodes-langchain.agent", 3.1, {
            "promptType": "define", "text": "={{ $json.user_text || '[no speech detected]' }}",
            "options": {"systemMessage": SYSTEM_PROMPT, "enableStreaming": False}}, [1320, 520])
        node("OpenAI Chat Model", "@n8n/n8n-nodes-langchain.lmChatOpenAi", 1.3, {
            "model": {"__rl": True, "mode": "list", "value": "gpt-5-mini"}, "options": {}},
             [1260, 760], credentials=OPENAI_CRED)
        node("Conversation memory", "@n8n/n8n-nodes-langchain.memoryBufferWindow", 1.4, {
            "sessionIdType": "customKey", "sessionKey": "={{ $('Pick voice').item.json.session_id }}",
            "contextWindowLength": 20}, [1420, 760])
        link("OpenAI Chat Model", "AI Agent", "ai_languageModel")
        link("Conversation memory", "AI Agent", "ai_memory")

    node("Reply voice", code, 2, {"jsCode": r"""
// Pick the voice from the language of the AI's reply (not the guess about the user's speech).
const p = $('Prepare').first().json;
const reply = String($input.first().json.output || '');
const vietnamese = /[ăâđêôơưáàảãạấầẩẫậắằẳẵặéèẻẽẹếềểễệíìỉĩịóòỏõọốồổỗộớờởỡợúùủũụứừửữựýỳỷỹỵ]/i;
const language = vietnamese.test(reply) ? 'vi' : 'en';
const [voice, speaker] = String(language === 'vi' ? p.voice_vi : p.voice_en).split('|');
return [{ json: { output: reply, reply_language: language, voice, speaker: speaker || null } }];
"""}, [1540, 520])
    node("Speak", http, 4.4, {
        "method": "POST", "url": f"{PIPER}/api/tts", "sendQuery": True,
        "queryParameters": {"parameters": [{"name": "response", "value": "audio"}]},
        "sendBody": True, "contentType": "json", "specifyBody": "json",
        "jsonBody": "={{ JSON.stringify({ text: $json.output, voice: $json.voice, speaker: $json.speaker, format: 'mp3' }) }}",
        "options": {"timeout": 180000, "response": {"response": {"responseFormat": "file", "outputPropertyName": "data"}}}},
         [1760, 520])
    node("Build reply", code, 2, {"jsCode": r"""
const v = $('Pick voice').first().json;
const r = $('Reply voice').first().json;
const audio = await this.helpers.getBinaryDataBuffer(0, 'data');
return [{ json: {
  transcript: v.spoken ? v.user_text : null,
  language: v.language,
  reply_language: r.reply_language,
  reply: r.output,
  voice: r.voice, speaker: r.speaker,
  mime: 'audio/mpeg', audio: audio.toString('base64'),
} }];
"""}, [1980, 520])
    node("Send reply", respond, 1.5, {"respondWith": "json", "responseBody": "={{ $json }}", "options": {}}, [2200, 520])

    link("Talk", "Prepare")
    link("Prepare", "Authorized?")
    link("Authorized?", "Spoken?", out=0)
    link("Authorized?", "Reject", out=1)
    link("Spoken?", "Transcribe", out=0)
    link("Spoken?", "Pick voice", out=1)
    link("Transcribe", "Pick voice")
    link("Pick voice", "AI Agent")
    link("AI Agent", "Reply voice")
    link("Reply voice", "Speak")
    link("Speak", "Build reply")
    link("Build reply", "Send reply")

    nodes.append({"parameters": {"content": (
        "## 🎙 Voice Chat (PI TTS Pack)\n"
        "Open **/webhook/voice-chat** on your n8n domain, tap the mic and talk.\n\n"
        "1. Speech → text: PI TTS Pack Whisper (on this Pi)\n"
        "2. Reply: AI Agent (OpenAI · swap the chat model node for Claude / Ollama any time)\n"
        "3. Text → speech: PI TTS Pack, voice picked per language (⚙︎ Voices on the page, clones included)\n\n"
        "Access code: see **Prepare** → `ACCESS_CODE`."), "height": 300, "width": 460},
        "id": nid(), "name": "About", "type": "n8n-nodes-base.stickyNote", "typeVersion": 1, "position": [-40, -380]})

    import secrets, string
    wf_id = "".join(secrets.choice(string.ascii_letters + string.digits) for _ in range(16))
    return {"id": wf_id, "name": "Voice Chat (PI TTS Pack)" + (" [TEST]" if stub_ai else ""), "nodes": nodes,
            "connections": connections, "settings": {"executionOrder": "v1"}, "active": False, "pinData": {}}


if __name__ == "__main__":
    code_value = sys.argv[1]
    out = Path(sys.argv[2]) if len(sys.argv) > 2 else HERE
    (out / "voice-chat.workflow.json").write_text(json.dumps(build(code_value), ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "voice-chat.test.workflow.json").write_text(json.dumps(build(code_value, stub_ai=True), ensure_ascii=False, indent=2), encoding="utf-8")
    print("written")
