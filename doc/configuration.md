# Configuration

All configuration is done through environment variables, loaded from a `.env` file at the project root. Copy `.env.example` to `.env` and edit it.

```bash
cp .env.example .env
```

---

## LLM

| Variable | Required | Default | Description |
|---|---|---|---|
| `GROQ_API_KEY` | **Yes** | — | Groq API key for LLM inference. Get one at [console.groq.com](https://console.groq.com). |
| `POTTS_MODEL` | No | `llama-3.3-70b-versatile` | Groq model ID to use for the voice pipeline and agents. |
| `ANTHROPIC_API_KEY` | No | — | Anthropic API key. Set this to swap to Claude instead of Groq (requires changing the LLM service in `pipeline.py`). |

### Changing the model

To use a different Groq model, set `POTTS_MODEL` in your `.env`:

```
POTTS_MODEL=llama-3.1-8b-instant
```

Fast, cheap models like `llama-3.1-8b-instant` give snappier responses. Larger models like `llama-3.3-70b-versatile` give better reasoning and tool use.

### Swapping to Anthropic

To use Claude instead of Groq:

1. Set `ANTHROPIC_API_KEY` and `POTTS_MODEL` in your `.env`:
   ```
   ANTHROPIC_API_KEY=sk-ant-...
   POTTS_MODEL=claude-sonnet-4-6
   ```
2. Edit `potts/voice/pipeline.py` — replace `GroqLLMService` with `AnthropicLLMService` from pipecat.
3. Edit `potts/agents/base.py` — replace `AsyncGroq` with the Anthropic equivalent.

The rest of the codebase (tools, memory, scheduler) is model-agnostic.

---

## STT (Speech-to-Text)

| Variable | Required | Default | Description |
|---|---|---|---|
| `DEEPGRAM_API_KEY` | **Yes** | — | Deepgram API key for live speech recognition. Get one at [console.deepgram.com](https://console.deepgram.com). |

Deepgram is configured internally with:
- **Model:** `nova-3-general`
- **Language:** `es` (Spanish)
- **Keyterm boost:** `["Potts"]` — tells the model to prioritise transcribing "Potts"

To change the language, edit `potts/voice/pipeline.py` — look for `DeepgramSTTService.Settings`.

---

## TTS (Text-to-Speech)

| Variable | Required | Default | Description |
|---|---|---|---|
| `CARTESIA_API_KEY` | **Yes** | — | Cartesia API key. Get one at [console.cartesia.ai](https://console.cartesia.ai). |
| `POTTS_VOICE_ID` | No | `a0e99841-438c-4a64-b679-ae501e7d6091` | Cartesia voice ID used for Potts's speech output. |

### Changing Potts's voice

1. Go to [play.cartesia.ai](https://play.cartesia.ai)
2. Browse the voice library and preview voices
3. Click a voice you like and copy its **Voice ID** (a UUID)
4. Set it in your `.env`:
   ```
   POTTS_VOICE_ID=your-voice-id-here
   ```

The default voice ID (`a0e99841-438c-4a64-b679-ae501e7d6091`) is a calm English voice. There are Spanish, British, and many other accent options available.

---

## General

| Variable | Required | Default | Description |
|---|---|---|---|
| `POTTS_LOG_LEVEL` | No | `INFO` | Console log verbosity. Options: `DEBUG`, `INFO`, `WARNING`, `ERROR`. |
| `POTTS_DEBUG` | No | `0` | Set to `1` to enable WakeDebug mode — shows live STT transcriptions in the terminal. Useful for diagnosing wake word recognition problems. |

### Log file

In addition to the console, Potts writes a detailed log to:

```
~/.potts/potts.log
```

The log file:
- Always captures `DEBUG` level (regardless of `POTTS_LOG_LEVEL`)
- Includes file name and line number in every entry
- Rotates at **10 MB**
- Is kept for **7 days** before being deleted

To tail the log in real time:

```bash
tail -f ~/.potts/potts.log
```

### Debug mode

```bash
# Via Makefile
make dev

# Via env var
POTTS_DEBUG=1 POTTS_LOG_LEVEL=DEBUG uv run potts

# Via .env (persistent)
POTTS_DEBUG=1
```

In debug mode, every STT frame is printed to the terminal:

```
[i] 'po'
[i] 'pots'
[F] 'Potts, what time is it?'
```

---

## Dashboard API

| Variable | Required | Default | Description |
|---|---|---|---|
| `POTTS_API_HOST` | No | `0.0.0.0` | Host the API server binds to. `0.0.0.0` makes it accessible on all network interfaces (LAN + localhost). Use `127.0.0.1` to restrict to localhost only. |
| `POTTS_API_PORT` | No | `7860` | Port for the dashboard and API. Change this if 7860 is in use. |

---

## Microsoft Graph (Outlook email + calendar)

| Variable | Required | Default | Description |
|---|---|---|---|
| `MS_TENANT_ID` | No | — | Azure Active Directory tenant ID. Use `consumers` for personal Microsoft accounts, or your organisation's tenant UUID for work/school accounts. |
| `MS_CLIENT_ID` | No | — | Azure app registration client ID (Application ID). |

Both must be set to use email and calendar tools. See [outlook.md](outlook.md) for full setup instructions.

---

## GitHub

| Variable | Required | Default | Description |
|---|---|---|---|
| `GITHUB_TOKEN` | No | — | Personal access token for GitHub. Currently available for use in the `shell` tool or custom agent prompts. Not wired to any built-in tool. |

---

## Full .env Example

```dotenv
# ── Required ──────────────────────────────────────────────────────────────────
GROQ_API_KEY=gsk_xxxxxxxxxxxxxxxxxxxx
DEEPGRAM_API_KEY=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
CARTESIA_API_KEY=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx

# ── Voice ─────────────────────────────────────────────────────────────────────
POTTS_VOICE_ID=a0e99841-438c-4a64-b679-ae501e7d6091
POTTS_MODEL=llama-3.3-70b-versatile

# ── Dashboard ─────────────────────────────────────────────────────────────────
POTTS_API_HOST=0.0.0.0
POTTS_API_PORT=7860

# ── Logging ───────────────────────────────────────────────────────────────────
POTTS_LOG_LEVEL=INFO
POTTS_DEBUG=0

# ── Microsoft Graph (optional) ────────────────────────────────────────────────
# MS_TENANT_ID=consumers
# MS_CLIENT_ID=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx

# ── GitHub (optional) ─────────────────────────────────────────────────────────
# GITHUB_TOKEN=github_pat_xxxxxxxxxxxx

# ── Anthropic (optional, replaces Groq) ──────────────────────────────────────
# ANTHROPIC_API_KEY=sk-ant-xxxxxxxxxxxx
# POTTS_MODEL=claude-sonnet-4-6
```
