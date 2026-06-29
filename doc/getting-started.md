# Getting Started with Potts

Potts is a JARVIS-inspired voice AI assistant. You speak to it; it listens, thinks, and talks back. It can search the web, run shell commands, read and write files, send emails, manage your calendar, set reminders, and hand off complex work to background agents — all through natural conversation.

Runs on **Linux, macOS, and Windows**. Everything runs locally except three cloud API calls: Deepgram (speech-to-text), your LLM provider (reasoning), and Cartesia (text-to-speech).

---

## System Requirements

| Requirement | Details |
|---|---|
| **OS** | Linux, macOS 13+, or Windows 10/11 |
| **Python** | 3.13 or newer |
| **Node.js** | 18 or newer (for the dashboard) |
| **Package manager** | [`uv`](https://docs.astral.sh/uv/) |
| **Audio (Linux)** | PipeWire recommended; any ALSA/PulseAudio device works |
| **Audio (macOS/Win)** | Built-in CoreAudio / WASAPI — works out of the box |

Verify everything before starting:

```bash
python --version    # 3.13.x or higher
node --version      # v18.x or higher
uv --version        # uv 0.x.x
```

Install `uv` if you don't have it:

```bash
# Linux / macOS
curl -LsSf https://astral.sh/uv/install.sh | sh

# Windows (PowerShell)
powershell -c "irm https://astral.sh/uv/install.ps1 | iex"
```

---

## Quick Start

### 1. Clone the repository

```bash
git clone <repo-url> ~/code/potts
cd ~/code/potts
```

### 2. Get your API keys

You need **one LLM key** plus two keys for speech. Pick whichever LLM provider you prefer:

**LLM — choose one:**

| Provider | Sign up | Notes |
|---|---|---|
| **Groq** | [console.groq.com](https://console.groq.com) → API Keys | Free tier, fast |
| **Anthropic** | [console.anthropic.com](https://console.anthropic.com) → API Keys | Claude models |
| **OpenAI** | [platform.openai.com](https://platform.openai.com) → API Keys | GPT models |
| **OpenRouter** | [openrouter.ai](https://openrouter.ai) → Keys | Access many models |
| **Cerebras** | [cloud.cerebras.ai](https://cloud.cerebras.ai) | Very fast inference |

Potts auto-detects the provider from whichever key you set — **no extra config needed**.

If you set `ANTHROPIC_API_KEY` or `OPENAI_API_KEY`, vision tools (`analyze_screen`, `analyze_image`) also work automatically with the same key.

**Deepgram** (speech-to-text — free tier includes $200 in credits)
1. [console.deepgram.com](https://console.deepgram.com) → API Keys → Create a New API Key

**Cartesia** (text-to-speech — pay-as-you-go, very cheap)
1. [console.cartesia.ai](https://console.cartesia.ai) → API Keys → Generate key
2. To preview voices: [play.cartesia.ai](https://play.cartesia.ai) — copy a voice ID and set it as `POTTS_VOICE_ID`

### 3. Configure your environment

```bash
cp .env.example .env
```

Open `.env` and fill in your keys. Minimum required — pick one LLM provider:

```dotenv
# Option A — Groq (free tier, recommended for getting started)
GROQ_API_KEY=gsk_...
DEEPGRAM_API_KEY=...
CARTESIA_API_KEY=...

# Option B — Anthropic (also enables vision tools automatically)
ANTHROPIC_API_KEY=sk-ant-...
DEEPGRAM_API_KEY=...
CARTESIA_API_KEY=...

# Option C — OpenAI (also enables vision tools automatically)
OPENAI_API_KEY=sk-...
DEEPGRAM_API_KEY=...
CARTESIA_API_KEY=...
```

Potts will log which provider and model it resolved at startup, so you can confirm it picked the right one.

**Language:** Potts defaults to English (`POTTS_STT_LANGUAGE=en`). If you want a different language, set it in `.env`:

```dotenv
POTTS_STT_LANGUAGE=es   # Spanish
POTTS_STT_LANGUAGE=fr   # French
POTTS_STT_LANGUAGE=de   # German
```

**Agents:** Potts auto-detects whether to use Claude Code or the built-in LLM for background agents. Check which you have:

```bash
which claude   # prints a path if Claude Code is installed, blank if not
```

- **Claude Code found:** agents run via `claude --print` (your Claude subscription). Set `POTTS_AGENT_DIR` to the project directory you want them to operate in.
- **Claude Code not found:** agents use the built-in LLM tool loop with your configured provider. No extra config needed.

```dotenv
# Only needed if claude is installed:
POTTS_AGENT_DIR=/home/yourname/code/myproject
```

Everything else in `.env` has sensible defaults and can be left as-is.

### 4. Install dependencies and build the dashboard

```bash
make install
```

This runs `uv sync` (Python deps, including PyTorch for voice activity detection) and `npm install && npm run build` (React dashboard). First run takes 2–5 minutes.

### 5. Run Potts

```bash
make run
```

---

## First Run Experience

You should see output like this:

```
12:34:01 | INFO     | Starting Potts...
12:34:02 | INFO     | Agent backend: builtin (auto-detected: claude CLI not found)  ...
12:34:02 | INFO     | Using audio device [3] 'PipeWire'
12:34:03 | INFO     | No prior memory — first session.
12:34:04 | INFO     | Dashboard available at http://localhost:7860
12:34:04 | INFO     | Scheduler started.
12:34:05 | INFO     | Potts ready. Say 'Potts' to activate.

[Potts sleeping — say 'Potts' to wake]
```

About 1.5 seconds after startup, Potts speaks a greeting in your configured language — this confirms your speakers and TTS are working.

### Waking Potts

Say **"Potts"**. You'll hear a short high beep and see `[Potts active]`. Now speak your request naturally:

```
You: What's the weather like in London today?
[searching: weather London today]
Potts: Currently in London it's 12 degrees and overcast, with rain expected in the afternoon.
```

After 5 minutes of silence, Potts goes back to sleep automatically — you'll hear a lower beep and see `[Potts sleeping]`. Say "Potts" again to wake it.

### The Dashboard

Open [http://localhost:7860](http://localhost:7860) while Potts is running to see live status, agent output, memory, reminders, and metrics. The dashboard is optional — Potts works entirely without it.

### Stopping Potts

Press **Ctrl+C**. Potts saves the session to memory (extracts facts and writes a summary) before exiting. The next session will start with that context.

---

## Optional Features

These require additional setup but are not needed for core voice functionality.

### Background agents

When you ask Potts to delegate a complex task, it spawns a background agent. The agent backend is auto-detected (see step 3 above), but you can override it:

```dotenv
POTTS_AGENT_BACKEND=claude_code   # force claude --print
POTTS_AGENT_BACKEND=builtin       # force built-in LLM tool loop
```

See [agents.md](agents.md) for details on what each agent type does.

### Vision tools (analyze_screen, analyze_image)

If your `POTTS_LLM_PROVIDER` is already `anthropic`, `openai`, or `openrouter`, vision works automatically — no extra config needed.

Otherwise, set one of:
```dotenv
ANTHROPIC_API_KEY=sk-ant-...   # Claude Haiku — fast and accurate
OPENAI_API_KEY=sk-...          # GPT-4o-mini — good alternative
```

You also need a screenshot tool for `analyze_screen`:
```bash
sudo pacman -S scrot    # or: maim, imagemagick, spectacle
```

See [configuration.md](configuration.md) for advanced options (`POTTS_VISION_PROVIDER`, `POTTS_VISION_MODEL`).

### GitHub tools

```dotenv
GITHUB_TOKEN=github_pat_...   # github.com/settings/tokens — needs repo, notifications scopes
```

### Outlook email and calendar

See [outlook.md](outlook.md) for the full Azure app registration setup.

### Better web search (Brave Search)

```dotenv
BRAVE_API_KEY=BSA-...   # api.search.brave.com — free tier available
```

Falls back to DuckDuckGo if not set.

---

## Troubleshooting

**Potts doesn't understand what I'm saying**

1. Run `make dev` to see live transcriptions. You'll see what Deepgram is hearing in real time.
2. Make sure `POTTS_STT_LANGUAGE` matches the language you're speaking.
3. Say "Potts" clearly — the wake word detection is tuned for the name "Potts" specifically.

**No audio / Potts doesn't speak**

Check that your speakers are not muted. The startup greeting plays ~1.5 seconds after launch — if you hear nothing, check `POTTS_MIC_NAME`:

```bash
# List all audio devices:
python -c "import pyaudio; p = pyaudio.PyAudio(); [print(i, p.get_device_info_by_index(i)['name']) for i in range(p.get_device_count())]"
```

Set `POTTS_MIC_NAME` in `.env` to a substring of the device name you want to use. Set it to `""` (empty) to use the system default.

**"PipeWire not found — using system default audio device"** (Linux)

PipeWire is not running or not named "pipewire" in PyAudio. Check with:
```bash
systemctl --user status pipewire
pactl info | grep -i "server name"
```
Or set `POTTS_MIC_NAME=` (empty) in `.env` to use the system default device directly.

**"POTTS_AGENT_BACKEND=claude_code but the 'claude' CLI was not found in PATH"**

Either install Claude Code (`npm install -g @anthropic-ai/claude-code`) or add `POTTS_AGENT_BACKEND=builtin` to your `.env`.

**"Missing required environment variable: ..."**

A required key is missing from `.env`. Check that `GROQ_API_KEY`, `DEEPGRAM_API_KEY`, and `CARTESIA_API_KEY` are all set.

**Dashboard shows a JSON message instead of the UI**

The React app hasn't been built yet. Run `make dashboard`.

**analyze_screen returns "Vision requires ANTHROPIC_API_KEY or OPENAI_API_KEY"**

Vision tools need their own key — see the Optional Features section above.

**Agent says "claude exited 127" or similar**

`POTTS_AGENT_BACKEND=claude_code` is set but the `claude` binary can't be found. Either install Claude Code or set `POTTS_AGENT_BACKEND=builtin`.
