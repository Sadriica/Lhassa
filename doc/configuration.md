# Configuration

All configuration is done through environment variables, loaded from a `.env` file at the project root. Copy `.env.example` to `.env` and edit it.

```bash
cp .env.example .env
```

---

## LLM Provider

Lhassa supports seven LLM providers. **You only need to set one API key** — Lhassa auto-detects the provider from whichever key is present, and that same provider is used for the voice pipeline, the built-in agent tool loop, and vision tools.

### API keys and auto-detection

Set one (or more) of these keys. The first one found is used:

| Variable | Provider | Auto-detected default model | Where to get it |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | `anthropic` | `claude-haiku-4-5-20251001` | [console.anthropic.com](https://console.anthropic.com) |
| `OPENAI_API_KEY` | `openai` | `gpt-4o-mini` | [platform.openai.com](https://platform.openai.com) |
| `GROQ_API_KEY` | `groq` | `llama-3.3-70b-versatile` | [console.groq.com](https://console.groq.com) |
| `OPENROUTER_API_KEY` | `openrouter` | `anthropic/claude-haiku-4-5-20251001` | [openrouter.ai/keys](https://openrouter.ai/keys) |
| `CEREBRAS_API_KEY` | `cerebras` | `llama3.3-70b` | [cloud.cerebras.ai](https://cloud.cerebras.ai) |
| `DEEPSEEK_API_KEY` | `deepseek` | `deepseek-v4-flash` | [platform.deepseek.com](https://platform.deepseek.com) — the metered cloud seat (subscription CLIs are still tried first in the fallback chain) |
| `OLLAMA_BASE_URL` | `ollama` | `llama3.2` | No key needed — set URL if Ollama isn't on localhost |

Auto-detection priority: `ANTHROPIC_API_KEY` → `OPENAI_API_KEY` → `GROQ_API_KEY` → `OPENROUTER_API_KEY` → `CEREBRAS_API_KEY` → `DEEPSEEK_API_KEY`.

Note on DeepSeek: V4 Flash spends reasoning tokens as output tokens — a very
small `max_tokens` budget can yield an empty reply; 512-768 tokens is a safe
floor.

`OLLAMA_BASE_URL` defaults to `http://localhost:11434`. To use Ollama you must set `LHASSA_LLM_PROVIDER=ollama` explicitly (no key to detect from).

Lhassa logs the resolved provider and model at startup so you can confirm it picked the right one:
```
INFO | LLM provider: anthropic (auto-detected from API key)  model=claude-haiku-4-5-20251001
```

### Override (optional)

If you want to force a specific provider or model, set these explicitly:

| Variable | Default | Description |
|---|---|---|
| `LHASSA_LLM_PROVIDER` | auto-detected | Override: `groq`, `anthropic`, `openai`, `openrouter`, `cerebras`, `deepseek`, `ollama` |
| `LHASSA_MODEL` | provider default | Override: any model name supported by the active provider |

### Provider examples

**Anthropic (also enables vision automatically):**
```dotenv
ANTHROPIC_API_KEY=sk-ant-...
# LHASSA_MODEL=claude-sonnet-4-6   # optional override; default: claude-haiku-4-5-20251001
```

**OpenAI (also enables vision automatically):**
```dotenv
OPENAI_API_KEY=sk-...
# LHASSA_MODEL=gpt-4o   # optional; default: gpt-4o-mini
```

**Groq (free tier, fast):**
```dotenv
GROQ_API_KEY=gsk_...
# LHASSA_MODEL=llama-3.3-70b-versatile   # default
```

**OpenRouter (hundreds of models):**
```dotenv
OPENROUTER_API_KEY=sk-or-...
LHASSA_MODEL=meta-llama/llama-3.3-70b-instruct   # or any OpenRouter model
```

**Ollama (fully local):**
```dotenv
LHASSA_LLM_PROVIDER=ollama   # required — no key to auto-detect from
LHASSA_MODEL=llama3.3
# OLLAMA_BASE_URL=http://localhost:11434   # default
```

**Groq for voice, Anthropic for vision and agents:**
```dotenv
GROQ_API_KEY=gsk_...
ANTHROPIC_API_KEY=sk-ant-...   # Groq wins provider detection; Anthropic is used for vision
```

### Provider compatibility notes

- **Voice pipeline**: pipecat bundles LLM services for Groq, Anthropic, OpenAI, OpenRouter, Ollama, and Cerebras. All providers use the same wake-word and tool-call flow.
- **Agents**: all providers use the OpenAI-compatible client except Anthropic, which uses the native Anthropic SDK. The tool loop works the same way for both.
- **Session memory extraction**: uses the active provider automatically. Anthropic falls back to a text prompt since it doesn't support `response_format: json_object`.
- **Vision tools** (`analyze_screen`, `analyze_image`): if `LHASSA_LLM_PROVIDER` is `anthropic`, `openai`, or `openrouter`, vision uses the same key automatically. Otherwise, vision falls back to `ANTHROPIC_API_KEY` → `OPENAI_API_KEY`.

---

## STT (Speech-to-Text)

| Variable | Required | Default | Description |
|---|---|---|---|
| `DEEPGRAM_API_KEY` | **Yes** | — | Deepgram API key for live speech recognition. Get one at [console.deepgram.com](https://console.deepgram.com). |
| `LHASSA_STT_LANGUAGE` | No | `en` | BCP-47 language code for speech recognition. See supported languages below. |

Deepgram is configured with the `nova-3-general` model and a keyterm boost for "Lhassa" to improve wake word recognition.

### Supported languages

Common options for `LHASSA_STT_LANGUAGE`:

| Code | Language |
|---|---|
| `en` | English (default) |
| `es` | Spanish |
| `fr` | French |
| `de` | German |
| `pt` | Portuguese |
| `nl` | Dutch |
| `hi` | Hindi |
| `ja` | Japanese |

Full list: [developers.deepgram.com/docs/languages](https://developers.deepgram.com/docs/languages)

---

## TTS (Text-to-Speech)

| Variable | Required | Default | Description |
|---|---|---|---|
| `CARTESIA_API_KEY` | **Yes** | — | Cartesia API key. Get one at [console.cartesia.ai](https://console.cartesia.ai). |
| `LHASSA_VOICE_ID` | No | `a0e99841-438c-4a64-b679-ae501e7d6091` | Cartesia voice ID used for Lhassa's speech output. |

### Changing Lhassa's voice

1. Go to [play.cartesia.ai](https://play.cartesia.ai)
2. Browse the voice library and preview voices
3. Click a voice you like and copy its **Voice ID** (a UUID)
4. Set it in your `.env`:
   ```
   LHASSA_VOICE_ID=your-voice-id-here
   ```

The default voice ID (`a0e99841-438c-4a64-b679-ae501e7d6091`) is a calm English voice. There are Spanish, British, and many other accent options available.

---

## Audio

| Variable | Required | Default | Description |
|---|---|---|---|
| `LHASSA_MIC_NAME` | No | `pipewire` (Linux) / `""` (macOS, Windows) | Substring matched against PyAudio device names (case-insensitive). Set to `""` to use the system default device. |

The default is `pipewire` on Linux (matches PipeWire's virtual device) and empty on macOS/Windows (uses the OS default). If Lhassa can't find your microphone, list available devices:

```bash
python -c "import pyaudio; p = pyaudio.PyAudio(); [print(i, p.get_device_info_by_index(i)['name']) for i in range(p.get_device_count())]"
```

Then set `LHASSA_MIC_NAME` to a substring of the device name you want.

---

## Web Search

| Variable | Required | Default | Description |
|---|---|---|---|
| `BRAVE_API_KEY` | No | — | Brave Search API key. When set, `web_search` uses the Brave Search API instead of DuckDuckGo. Get one at [api.search.brave.com](https://api.search.brave.com). |

Brave Search is generally faster and more reliable than DuckDuckGo. If `BRAVE_API_KEY` is not set, Lhassa falls back to DuckDuckGo automatically — no configuration needed.

---

## Vision tools

`analyze_screen` and `analyze_image` require a vision-capable LLM. The provider is configured independently of `LHASSA_LLM_PROVIDER` so you can, for example, use Groq for voice and Anthropic for vision.

| Variable | Required | Default | Description |
|---|---|---|---|
| `LHASSA_VISION_PROVIDER` | No | `auto` | Vision provider. Options: `auto`, `anthropic`, `openai`, `openrouter`. |
| `LHASSA_VISION_MODEL` | No | provider default | Model to use for vision. Leave empty to use the provider default. |

### Auto-detection

When `LHASSA_VISION_PROVIDER=auto` (the default), Lhassa resolves the provider in this order:

1. If `LHASSA_LLM_PROVIDER` is `anthropic`, `openai`, or `openrouter` **and** the matching key is set → use it (no extra keys needed)
2. If `ANTHROPIC_API_KEY` is set → use Anthropic
3. If `OPENAI_API_KEY` is set → use OpenAI
4. No vision key found → vision tools disabled (logged as a warning at startup)

### Provider defaults

| Provider | Default model | Notes |
|---|---|---|
| `anthropic` | `claude-haiku-4-5-20251001` | Fast and cheap, excellent at reading screenshots |
| `openai` | `gpt-4o-mini` | Good all-around vision |
| `openrouter` | `anthropic/claude-haiku-4-5-20251001` | Set `LHASSA_VISION_MODEL` to use a different model |

### Common setups

**Using Anthropic for everything** — vision works automatically (no extra config):
```dotenv
ANTHROPIC_API_KEY=sk-ant-...
# provider auto-detected as anthropic; vision uses the same key
```

**Groq for voice, Anthropic for vision:**
```dotenv
GROQ_API_KEY=gsk_...           # Groq wins auto-detection → voice uses Groq
ANTHROPIC_API_KEY=sk-ant-...   # picked up automatically by vision auto-detection
```

**Explicit OpenRouter vision model:**
```dotenv
LHASSA_VISION_PROVIDER=openrouter
OPENROUTER_API_KEY=sk-or-...
LHASSA_VISION_MODEL=google/gemini-flash-1.5
```

### Screenshot tool requirement

`analyze_screen` needs a screenshot utility. Lhassa tries each in order until one works:

**Linux** — install one of:
```bash
sudo pacman -S scrot        # Arch — recommended
sudo apt install scrot       # Debian/Ubuntu
# alternatives: maim, imagemagick (provides `import`), gnome-screenshot, spectacle (KDE)
```

**macOS** — `screencapture` is built-in. No installation needed.

**Windows** — PowerShell screenshot is built-in. For better results, install Pillow:
```
pip install Pillow
```

---

## Agent backend

| Variable | Required | Default | Description |
|---|---|---|---|
| `LHASSA_AGENT_BACKEND` | No | auto-detected | `claude_code` delegates to `claude --print`; `builtin` uses the internal LLM tool loop with `LHASSA_LLM_PROVIDER`. If not set, Lhassa detects automatically (see below). |
| `LHASSA_AGENT_DIR` | No | cwd at launch | Working directory for `claude --print`. Set this to your project root so Claude Code has the right file context. Only used when `LHASSA_AGENT_BACKEND=claude_code`. |

### Auto-detection

If `LHASSA_AGENT_BACKEND` is not set, Lhassa detects the right backend at startup:

| Condition | Resolved backend | Startup log |
|---|---|---|
| `claude` found in PATH | `claude_code` | `Agent backend: claude_code (auto-detected)` |
| `claude` not found | `builtin` | `Agent backend: builtin (claude CLI not found ...)` |

You can always override the detected value explicitly.

### Startup validation

Lhassa validates the agent backend before the voice pipeline starts, so misconfiguration surfaces immediately with a clear message rather than failing silently later:

```
# If LHASSA_AGENT_BACKEND=claude_code but claude is not installed:
RuntimeError: LHASSA_AGENT_BACKEND=claude_code but the 'claude' CLI was not found in PATH.
Install Claude Code:  https://claude.ai/code
Or set LHASSA_AGENT_BACKEND=builtin to use the built-in LLM tool loop.
```

### claude_code backend (uses your Claude subscription)

When `agent_backend=claude_code`, all three agent types run through `claude --print`:

- **No separate API key** — uses your Claude Code subscription (Pro/Max)
- **Full Claude Code toolset** — agents can read, edit, and write files (`Read`, `Edit`, `Write`, `Bash`, `Glob`, `Grep`)
- **Best available Claude model** — whichever model your subscription provides

Requirements: `claude` CLI installed and authenticated (`claude` works in your terminal).

Recommended setup — Groq (fast, free) for voice, Claude subscription for agents:

```dotenv
# Voice pipeline — Groq auto-detected from the key
GROQ_API_KEY=gsk_...

# Agents — set your project directory (or leave unset to use cwd)
LHASSA_AGENT_DIR=/home/yourname/code/myproject
```

### builtin backend (internal LLM tool loop)

When `agent_backend=builtin`, agents use the same provider as the voice pipeline with a 10-iteration tool loop (`web_search`, `shell`, `read_file`, `write_file`). Supports all `LHASSA_LLM_PROVIDER` options.

```dotenv
LHASSA_AGENT_BACKEND=builtin
# Uses LHASSA_LLM_PROVIDER and LHASSA_MODEL — no extra config needed
```

Output streams line by line to the dashboard's Agents panel regardless of which backend is active.

---

## GitHub

| Variable | Required | Default | Description |
|---|---|---|---|
| `GITHUB_TOKEN` | No | — | GitHub personal access token. Required for the GitHub tools (`github_list_repos`, `github_list_issues`, `github_get_issue`, `github_list_prs`, `github_get_pr`, `github_comment`, `github_notifications`). |

Create a token at [github.com/settings/tokens](https://github.com/settings/tokens). The token needs `repo` scope for private repositories, or no scope for public-only access.

If `GITHUB_TOKEN` is not set, calling any GitHub tool will return an error message.

---

## Microsoft Graph (Outlook email + calendar)

| Variable | Required | Default | Description |
|---|---|---|---|
| `MS_TENANT_ID` | No | — | Azure Active Directory tenant ID. Use `consumers` for personal Microsoft accounts, or your organisation's tenant UUID for work/school accounts. |
| `MS_CLIENT_ID` | No | — | Azure app registration client ID (Application ID). |

Both must be set to use email and calendar tools. See [outlook.md](outlook.md) for full setup instructions.

---

## Wake behaviour

| Variable | Required | Default | Description |
|---|---|---|---|
| `LHASSA_WAKE_TIMEOUT` | No | `300` | Seconds of silence before Lhassa goes back to sleep after being woken. Lower values save API costs on idle sessions. |

## Agent tuning

| Variable | Required | Default | Description |
|---|---|---|---|
| `LHASSA_MAX_AGENT_ITERATIONS` | No | `10` | Maximum tool-calling loop iterations per agent run. Increase for complex multi-step tasks; decrease to limit API cost. |

## General

| Variable | Required | Default | Description |
|---|---|---|---|
| `LHASSA_LOG_LEVEL` | No | `INFO` | Console log verbosity. Options: `DEBUG`, `INFO`, `WARNING`, `ERROR`. |
| `LHASSA_DEBUG` | No | `0` | Set to `1` to enable WakeDebug mode — shows live STT transcriptions in the terminal. Useful for diagnosing wake word recognition problems. |

### Log file

In addition to the console, Lhassa writes a detailed log to:

```
~/.lhassa/lhassa.log
```

The log file:
- Always captures `DEBUG` level (regardless of `LHASSA_LOG_LEVEL`)
- Includes file name and line number in every entry
- Rotates at **10 MB**
- Is kept for **7 days** before being deleted

To tail the log in real time:

```bash
tail -f ~/.lhassa/lhassa.log
```

### Debug mode

```bash
# Via Makefile
make dev

# Via env var
LHASSA_DEBUG=1 LHASSA_LOG_LEVEL=DEBUG uv run lhassa

# Via .env (persistent)
LHASSA_DEBUG=1
```

In debug mode, every STT frame is printed to the terminal:

```
[i] 'po'
[i] 'pots'
[F] 'Lhassa, what time is it?'
```

---

## Dashboard API

| Variable | Required | Default | Description |
|---|---|---|---|
| `LHASSA_API_HOST` | No | `127.0.0.1` | Host the API server binds to. Defaults to localhost only (the dashboard is unauthenticated and Lhassa can run privileged commands). Set `0.0.0.0` only to expose it on your LAN. |
| `LHASSA_API_PORT` | No | `7766` | Port for the dashboard and API. Change this if 7860 is in use. |

---

## Brain knobs (reflexes, spend, digest, delegation, self-improvement)

| Variable | Required | Default | Description |
|---|---|---|---|
| `LHASSA_AUTO_SYNC_REPOS` | No | *(empty)* | Comma-separated repo paths the `repo_changed` reflex auto-syncs (`sync_repo`) instead of spending a judgment. |
| `LHASSA_CONTEXT_TOKEN_BUDGET_MINIMAL` | No | `600` | Context budget for `minimal`-tier thinks (the policy layer picks the tier per trigger). |
| `LHASSA_BUDGET_SOFT_PCT` | No | `0.8` | Fraction of the daily token budget past which the router refuses the smart model and prefers $0 providers. |
| `LHASSA_DEFERRED_RETRY_BACKOFF` / `LHASSA_DEFERRED_MAX_RETRIES` | No | backoff / `3` | Deferred-task re-queue sweep: retry window (seconds) and cap (then `rechazada`). |
| `LHASSA_DIGEST_HOUR_MORNING` / `LHASSA_DIGEST_HOUR_EVENING` | No | `8` / `21` | Local hours the `digest` seed schedules the daily $0 Telegram digest at. |
| `LHASSA_DIGEST_STYLE` | No | `plain` | `plain` keeps the deterministic $0 first-person template; `persona` adds one cheap re-voice pass over the same facts. |
| `LHASSA_DIGEST_LANGUAGE` | No | *(empty)* | Language hint for the persona pass; also localizes the deterministic personal check-in when enabled (`es`, `en`, ...). |
| `LHASSA_DIGEST_PERSONAL_CHECKIN` | No | `0` | Adds the $0 personal agenda/check-in section to the digest and captures the next Telegram reply as a daily check-in for six hours. |
| `LHASSA_MONITOR_DELEGATE` | No | `0` | When on, the watcher converts monitor findings into minted `org_run` tasks (fingerprint-deduped). |
| `LHASSA_REPETITION_THRESHOLD` | No | `5` | Occurrences (with real cost) before `detect_repetition` proposes minting a deterministic action. |
| `LHASSA_KNOWLEDGE_ROOTS` | No | *(empty)* | CSV of directories (`~`-relative allowed) the `list_dir`/`read_file` actions and document perception (see `doc/memory.md`) may touch — opt-in, empty means nothing is reachable. |
| `LHASSA_PROTECTED_ROOTS` | No | *(empty)* | A SECOND, stricter tier of roots (may overlap `LHASSA_KNOWLEDGE_ROOTS`): a file action whose `path` resolves inside one of these ALWAYS parks a per-access approval, fail-closed, never lowered by the autonomy knob — see `doc/security.md`. |
| `LHASSA_EMBEDDER_BACKEND` | No | `auto` | Layer-4/knowledge embedder backend: `auto` (probe local Ollama, else hashing), `ollama` (force native Ollama, degrade to hashing if unreachable), `hash` (always the offline embedder). |
| `LHASSA_EMBEDDER_MODEL` | No | `nomic-embed-text` | Ollama model name used by the native embedder backend. |

See `lhassa config list` for the live view — these are also editable at runtime via the settings service where marked `EDITABLE`.

---

## Full .env Example

```dotenv
# ── Required: STT + TTS ───────────────────────────────────────────────────────
DEEPGRAM_API_KEY=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
CARTESIA_API_KEY=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx

# ── LLM: set ONE key — provider and model are auto-detected ───────────────────
#
# Groq (free tier, good for getting started):
GROQ_API_KEY=gsk_xxxxxxxxxxxxxxxxxxxx
#
# Anthropic (Claude; also enables vision tools automatically):
# ANTHROPIC_API_KEY=sk-ant-xxxxxxxxxxxx
#
# OpenAI (GPT; also enables vision tools automatically):
# OPENAI_API_KEY=sk-xxxxxxxxxxxx
#
# OpenRouter (access many models with one key):
# OPENROUTER_API_KEY=sk-or-xxxxxxxxxxxx
#
# Ollama (local — no key, but must set provider explicitly):
# LHASSA_LLM_PROVIDER=ollama
# LHASSA_MODEL=llama3.3
# OLLAMA_BASE_URL=http://localhost:11434

# ── Model override (optional — auto-set to provider default if not given) ─────
# LHASSA_MODEL=llama-3.3-70b-versatile

# ── STT language ──────────────────────────────────────────────────────────────
LHASSA_STT_LANGUAGE=en    # en, es, fr, de, pt, nl, hi, ja ...

# ── Voice ─────────────────────────────────────────────────────────────────────
LHASSA_VOICE_ID=a0e99841-438c-4a64-b679-ae501e7d6091

# ── Audio ─────────────────────────────────────────────────────────────────────
# LHASSA_MIC_NAME=         # leave empty for OS default; "pipewire" on Linux

# ── Dashboard ─────────────────────────────────────────────────────────────────
LHASSA_API_HOST=127.0.0.1
LHASSA_API_PORT=7860

# ── Logging ───────────────────────────────────────────────────────────────────
LHASSA_LOG_LEVEL=INFO
LHASSA_DEBUG=0

# ── Wake & agent behaviour ────────────────────────────────────────────────────
# LHASSA_WAKE_TIMEOUT=300          # seconds before auto-sleep (default 300)
# LHASSA_MAX_AGENT_ITERATIONS=10   # max tool-loop steps per agent run (default 10)

# ── Web search ────────────────────────────────────────────────────────────────
# BRAVE_API_KEY=BSA-xxxxxxxxxxxx   # optional; falls back to DuckDuckGo if not set

# ── Agent backend ────────────────────────────────────────────────────────────
# Auto-detected: claude_code if `claude` CLI is found, builtin otherwise.
# Uncomment to override:
# LHASSA_AGENT_BACKEND=claude_code   # use `claude --print` (Claude subscription)
# LHASSA_AGENT_BACKEND=builtin       # use internal LLM tool loop
# LHASSA_AGENT_DIR=/home/you/code/myproject   # directory for claude --print

# ── GitHub (optional) ─────────────────────────────────────────────────────────
# GITHUB_TOKEN=github_pat_xxxxxxxxxxxx

# ── Microsoft Graph (optional) ────────────────────────────────────────────────
# MS_TENANT_ID=consumers
# MS_CLIENT_ID=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
```
