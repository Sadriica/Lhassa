# Getting Started with Potts

Potts is a JARVIS-inspired voice AI assistant that runs entirely on your local Linux machine. You speak to it; it listens, thinks, and talks back. It can search the web, run shell commands, read and write files, send emails, manage your calendar, set reminders, and hand off complex work to specialised background agents — all through natural conversation.

Everything runs locally (except the three cloud API calls for speech, language, and voice synthesis). No app, no browser tab, no typing required.

---

## System Requirements

| Requirement | Details |
|---|---|
| **OS** | Linux (developed and tested on Arch Linux) |
| **Audio** | PipeWire (must be running — `pactl info` should mention PipeWire) |
| **Python** | 3.13 or newer |
| **Node.js** | 18 or newer (for the dashboard) |
| **Package manager** | [`uv`](https://docs.astral.sh/uv/) — fast Python package manager |
| **Internet** | Required for Groq, Deepgram, and Cartesia API calls |

Verify your versions before starting:

```bash
python --version      # should print 3.13.x or higher
node --version        # should print v18.x or higher
uv --version          # should print uv 0.x.x
pactl info | grep -i server  # should mention PipeWire
```

---

## Quick Start

### 1. Clone the repository

```bash
git clone <repo-url> ~/code/potts
cd ~/code/potts
```

### 2. Copy the environment file and fill in your API keys

```bash
cp .env.example .env
```

Open `.env` in your editor and fill in the three required keys:

```
GROQ_API_KEY=gsk_...
DEEPGRAM_API_KEY=...
CARTESIA_API_KEY=...
```

Everything else has sensible defaults and can be left as-is for your first run.

### 3. Install dependencies and build the dashboard

```bash
make install
```

This runs `uv sync` (Python deps) and `npm install && npm run build` (React dashboard). It takes a couple of minutes on first run because it downloads PyTorch for the Silero VAD model.

### 4. Run Potts

```bash
make run
```

---

## Where to Get Each API Key

### Groq (LLM inference)

Groq provides very fast inference for Llama models. The free tier is generous enough for everyday personal use.

1. Go to **[console.groq.com](https://console.groq.com)**
2. Sign up or log in
3. Navigate to **API Keys** in the left sidebar
4. Click **Create API Key** and copy the value

The key starts with `gsk_`.

### Deepgram (Speech-to-Text)

Deepgram converts your voice to text in real time.

1. Go to **[console.deepgram.com](https://console.deepgram.com)**
2. Sign up (free tier includes $200 in credits)
3. Go to **API Keys** in the left menu
4. Click **Create a New API Key**, give it a name, and copy the value

### Cartesia (Text-to-Speech)

Cartesia generates the voice you hear Potts speaking in.

1. Go to **[console.cartesia.ai](https://console.cartesia.ai)**
2. Sign up and verify your account
3. Go to **API Keys** and generate a new key

To preview and choose a different voice, visit **[play.cartesia.ai](https://play.cartesia.ai)**. Copy a voice ID from there and set it as `POTTS_VOICE_ID` in your `.env`.

---

## First Run Experience

When you run `make run`, you'll see output like this in the terminal:

```
12:34:01 | INFO     | Starting Potts...
12:34:02 | INFO     | Using audio device [3] PipeWire
12:34:03 | INFO     | No prior memory — first session.
12:34:04 | INFO     | Dashboard available at http://0.0.0.0:7860
12:34:04 | INFO     | Scheduler started.
12:34:05 | INFO     | Potts ready. Say 'Potts' to activate.

[Potts sleeping — say 'Potts' to wake]
```

The three services starting up are:
- **Voice pipeline** — microphone listening, STT, LLM, TTS
- **Dashboard API** — FastAPI server serving the web UI and REST/WebSocket endpoints
- **Scheduler** — background poller for reminders

### Waking Potts

Say **"Potts"** (or any of the recognised variants — see [voice-interaction.md](voice-interaction.md)). You'll hear a short high-pitched beep (880 Hz) and see:

```
[Potts active]
```

Now speak your request naturally. Potts will respond via your speakers and print the conversation to the terminal:

```
You: What's the weather like in Madrid today?
Potts: Let me check that for you.
[searching: weather Madrid today]
Potts: Currently in Madrid it's 18 degrees and partly cloudy, with a high of 23 expected this afternoon.
```

### Going Back to Sleep

After 5 minutes of silence, Potts goes back to sleep automatically. You'll hear a lower beep (440 Hz) and see:

```
[Potts sleeping — say 'Potts' to wake]
```

Just say "Potts" again to wake it back up.

---

## The Dashboard

While Potts is running, open your browser and go to:

```
http://localhost:7860
```

The dashboard shows Potts's current status, any running background agents with their live streaming output, memory facts, session history, scheduled reminders, and live metrics. You don't need the dashboard to use Potts — it's entirely optional, but useful for monitoring long-running tasks.

See [dashboard.md](dashboard.md) for a full description of every panel.

---

## Stopping Potts

Press **Ctrl+C** in the terminal. Potts will:

1. Stop listening
2. Save the session to memory (extract facts + write a one-sentence summary using the LLM)
3. Shut down cleanly

```
12:45:12 | INFO     | Potts shutting down.
12:45:12 | INFO     | Saving session to memory...
12:45:13 | INFO     | [memory] fact saved: name = Alice
12:45:13 | INFO     | [memory] session saved: Discussed weather and set a reminder for 3pm.
12:45:13 | INFO     | Session saved.
```

The next time you start Potts, it will remember what it learned about you from previous sessions.

---

## Troubleshooting First Run

**"Missing required environment variable: GROQ_API_KEY"**
You forgot to fill in one of the required keys in your `.env` file.

**"PipeWire not found — using system default audio device"**
Potts will still work, but you should investigate whether PipeWire is running (`systemctl --user status pipewire`).

**No audio / Potts doesn't respond**
Check that your microphone is working (`arecord -l`) and that your speakers are not muted.

**Dashboard shows a JSON message instead of the UI**
The React app hasn't been built yet. Run `make dashboard` to build it.
