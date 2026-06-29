# Potts

[![CI](https://github.com/yourname/potts/actions/workflows/ci.yml/badge.svg)](https://github.com/yourname/potts/actions/workflows/ci.yml)

Replace `yourname/potts` in the badge URL above if your GitHub remote uses a different owner or repo name.

A JARVIS-inspired voice AI assistant. You speak to it; it listens, thinks, and talks back. It can search the web, run shell commands, read and write files, send emails, manage your calendar, set reminders, watch processes and files, analyse your screen, and hand off complex work to background agents — all through natural conversation.

Runs on **Linux, macOS, and Windows**. Everything runs locally except three cloud API calls: Deepgram (speech-to-text), your LLM provider (reasoning), and Cartesia (text-to-speech).

---

## Quick start

### Requirements

| | Linux | macOS | Windows |
|---|---|---|---|
| Python | 3.13+ | 3.13+ | 3.13+ |
| Node.js | 18+ | 18+ | 18+ |
| Package manager | [`uv`](https://docs.astral.sh/uv/) | [`uv`](https://docs.astral.sh/uv/) | [`uv`](https://docs.astral.sh/uv/) |
| Audio | PipeWire (recommended) | Built-in CoreAudio | Built-in WASAPI |

### 1. Clone

```bash
git clone https://github.com/yourname/potts ~/code/potts
cd ~/code/potts
```

### 2. Get API keys

You need **one LLM key** and two speech keys:

| Service | Sign up | Notes |
|---|---|---|
| **Groq** | [console.groq.com](https://console.groq.com) | Free tier — recommended for getting started |
| **Anthropic** | [console.anthropic.com](https://console.anthropic.com) | Claude — also enables vision tools |
| **OpenAI** | [platform.openai.com](https://platform.openai.com) | GPT — also enables vision tools |
| **Deepgram** | [console.deepgram.com](https://console.deepgram.com) | Speech-to-text — free $200 credit |
| **Cartesia** | [console.cartesia.ai](https://console.cartesia.ai) | Text-to-speech |

### 3. Configure

```bash
cp .env.example .env
```

Minimum `.env` (using Groq as the LLM):

```dotenv
GROQ_API_KEY=gsk_...
DEEPGRAM_API_KEY=...
CARTESIA_API_KEY=...
```

Potts auto-detects the LLM provider from the key you set. Setting `ANTHROPIC_API_KEY` or `OPENAI_API_KEY` also enables vision tools automatically.

### 4. Install and run

```bash
make install   # first time: installs Python deps + builds the dashboard (~2–5 min)
make run       # start Potts
```

Open [http://localhost:7766](http://localhost:7766) for the live dashboard (optional).

---

## Usage

Say **"Potts"** to wake it. Potts listens, calls tools as needed, and replies aloud. After 5 minutes of silence it goes back to sleep — say "Potts" again to wake it. The sleep timeout is configurable via `POTTS_WAKE_TIMEOUT`.

```
You: Potts, what's the weather in London today?
Potts: Currently 12 degrees and overcast, with rain expected this afternoon.

You: Potts, remind me in 20 minutes to take my medication.
Potts: Reminder set for 20 minutes from now.

You: Potts, delegate to the code agent: refactor this function to be async.
Potts: Code agent started in the background. I'll let you know when it's done.
```

See [doc/examples.md](doc/examples.md) for a full reference of things you can say and what Potts will do.

---

## Features

- **Voice pipeline** — wake word detection, Deepgram STT, Cartesia TTS, VAD
- **Multi-language** — set `POTTS_STT_LANGUAGE` to speak in Spanish, French, German, Portuguese, and more; Potts responds in the same language
- **Multi-provider LLM** — Groq, Anthropic, OpenAI, OpenRouter, Cerebras, Ollama
- **Tools** — web search (Brave/DuckDuckGo), shell, file read/write
- **Background agents** — code, research, system; powered by Claude Code CLI or built-in tool loop
- **Monitor tools** — watch processes, files, and commands; get notified when done
- **Vision tools** — analyse screen and images with a vision LLM (cross-platform screenshots)
- **Reminders & scheduler** — schedule reminders by time or delay, persistent across restarts
- **Memory** — extracts facts from each conversation; context is restored on the next session
- **Outlook** — read/send email, read/create calendar events (via Microsoft Graph)
- **GitHub** — list repos, issues, PRs, notifications; post comments
- **Dashboard** — live React dashboard with metrics, agent output, memory viewer, session history
- **Cross-platform** — Linux, macOS, Windows; platform-aware notifications, screenshots, audio

---

## Docs

| Document | Contents |
|---|---|
| [doc/examples.md](doc/examples.md) | **Quick reference — exact phrases to use with Potts** |
| [doc/getting-started.md](doc/getting-started.md) | Full setup walkthrough |
| [doc/configuration.md](doc/configuration.md) | All environment variables |
| [doc/tools.md](doc/tools.md) | Every tool with examples |
| [doc/agents.md](doc/agents.md) | Agent backends in depth |
| [doc/architecture.md](doc/architecture.md) | How it all fits together |
| [doc/outlook.md](doc/outlook.md) | Outlook/Azure setup |
| [doc/dashboard.md](doc/dashboard.md) | Dashboard guide |

---

## Development

Run the backend test suite:

```bash
uv run pytest -q -p no:cacheprovider
```

Run the dashboard browser tests:

```bash
cd dashboard
npm run test:e2e
```

CI runs both on pushes to `main` and on pull requests via [`.github/workflows/ci.yml`](.github/workflows/ci.yml).

---

## License

AGPL-3.0-or-later — see [LICENSE](LICENSE).
