# Architecture

This document describes how Potts is built: the data flow for a voice turn, the concurrent task structure, the state singleton, the WebSocket fan-out, and the file layout.

---

## High-Level Diagram

```
┌─────────────────────────────────────────────────────────────────┐
│                         Potts Process                           │
│                                                                 │
│  ┌──────────┐   ┌───────────┐   ┌─────────────────────────┐   │
│  │          │   │  Voice    │   │   FastAPI / uvicorn      │   │
│  │ Scheduler│   │ Pipeline  │   │   REST  +  WebSocket     │   │
│  │  (30s    │   │ (pipecat) │   │   + React static files   │   │
│  │  poll)   │   │           │   │                          │   │
│  └────┬─────┘   └─────┬─────┘   └────────────┬─────────────┘   │
│       │               │                       │                 │
│       └───────────────┴───────── AppState ────┘                 │
│                           (singleton)                           │
└─────────────────────────────────────────────────────────────────┘

Voice Pipeline (pipecat):

  Microphone
      │
      ▼
  LocalAudioTransport.input()
      │  (raw PCM frames)
      ▼
  DeepgramSTTService          ◄── Deepgram API (WebSocket)
      │  (TranscriptionFrame)
      ▼
  [WakeDebug]  (if POTTS_DEBUG=1)
      │
      ▼
  LLMContextAggregator.user()
      │  WakePhraseUserTurnStartStrategy ──► 880 Hz beep on wake
      │  SileroVADAnalyzer (end-of-speech)  440 Hz beep on sleep
      │  WakeWordOnlyMuteStrategy
      │
      ▼ (complete user turn)
  GroqLLMService              ◄── Groq API (HTTPS)
      │  (LLMTextFrame / tool calls)
      ▼
  ChatPrinter  (terminal output)
      │
      ▼
  CartesiaTTSService          ◄── Cartesia API (HTTPS)
      │  (audio frames)
      ▼
  LocalAudioTransport.output()
      │
      ▼
  Speaker (PipeWire)
      │
      ▼
  LLMContextAggregator.assistant()
```

---

## Three Concurrent Tasks

`main.py` runs three asyncio tasks simultaneously:

```python
api_task       = asyncio.create_task(run_api_server(...))
scheduler_task = asyncio.create_task(run_scheduler())
# Voice pipeline runs directly (primary task):
_context, messages = await run_voice_pipeline()
```

| Task | Module | What it does |
|---|---|---|
| **Voice pipeline** | `potts/voice/pipeline.py` | Microphone → STT → LLM → TTS → speaker. The primary task — when it exits (Ctrl+C), the other two are cancelled. |
| **API server** | `potts/api/server.py` | Serves REST endpoints, WebSocket fan-out, and the React static files. |
| **Scheduler** | `potts/scheduler.py` | Polls the database every 30 seconds for due reminders and fires them. |

On shutdown (KeyboardInterrupt / Ctrl+C):
1. The voice pipeline stops
2. `api_task.cancel()` and `scheduler_task.cancel()` are called
3. Both tasks handle `CancelledError` cleanly
4. `close_session()` runs to save memory — this is the only step that still uses `asyncio.run()` after the event loop closes

---

## How a Voice Turn Works End-to-End

```
1. SLEEPING
   Deepgram is streaming audio frames to the STT service.
   WakePhraseUserTurnStartStrategy is scanning transcriptions for wake words.

2. WAKE WORD DETECTED
   A transcription matches a wake phrase (e.g. "pots").
   → 880 Hz beep plays
   → "[Potts active]" printed to terminal
   → WakePhraseUserTurnStartStrategy signals the aggregator to start a new turn

3. LISTENING
   SileroVADAnalyzer watches incoming audio for speech.
   Audio is muted to the LLM while the bot is speaking (WakeWordOnlyMuteStrategy),
   except wake phrases pass through to allow interruption.

4. END OF SPEECH (VAD silence detected)
   SileroVAD detects that the user has stopped talking.
   The LLMContextAggregator assembles the full user message and pushes it to the LLM.

5. LLM CALL
   GroqLLMService sends the context (system prompt + history + user message + tools) to Groq.
   state.metrics["groq_calls"] is incremented.
   LLMTextFrames stream back token by token.
   → ChatPrinter writes tokens to the terminal in real time

6. TOOL CALL (optional)
   If the LLM calls a tool (e.g. web_search), the tool handler runs asynchronously.
   The result is injected back into the context.
   The LLM continues generating after receiving the result.

7. TTS
   CartesiaTTSService converts the LLM response to audio.
   Audio is played through PipeWire via LocalAudioTransport.output().

8. SLEEP TIMEOUT
   If no wake word is heard within 5 minutes of the last activation,
   WakePhraseUserTurnStartStrategy fires the timeout callback.
   → 440 Hz beep plays
   → "[Potts sleeping]" printed to terminal
   → Returns to step 1
```

---

## AppState Singleton

`potts/state.py` defines `AppState` — a single shared object that connects the voice pipeline, the API server, the scheduler, and the agents:

```python
state = AppState()  # module-level singleton
```

| Attribute | Type | Set by | Read by |
|---|---|---|---|
| `pipeline_task` | `PipelineTask \| None` | Voice pipeline | Notifier (to inject TTS frames) |
| `llm_context` | `LLMContext \| None` | Voice pipeline | main.py (session recovery on shutdown) |
| `is_voice_active` | `bool` | main.py | Notifier, API `/api/status` |
| `agent_runs` | `dict[str, AgentRun]` | Agents | API `/api/agents`, WebSocket |
| `metrics` | `dict` | Pipeline, agents, tools | API `/api/metrics`, dashboard |
| `_ws_clients` | `set[asyncio.Queue]` | API WebSocket handler | `state.broadcast()` |

The `broadcast()` method fans out a JSON event to all connected WebSocket queues:

```python
async def broadcast(self, event: dict):
    for q in list(self._ws_clients):
        try:
            q.put_nowait(event)
        except asyncio.QueueFull:
            dead.append(q)  # slow client dropped
```

Each WebSocket connection gets its own `asyncio.Queue(maxsize=256)`. The API server reads from the queue and sends to the client. If a client falls behind, its queue fills up and it's dropped automatically.

---

## WebSocket Fan-Out for Real-Time Dashboard Updates

```
Agent.run() token arrives
        │
        ▼
state.broadcast({"type": "agent_token", "run_id": ..., "token": ...})
        │
        ├──► Queue for browser tab 1  ──► ws.send_text(...)
        ├──► Queue for browser tab 2  ──► ws.send_text(...)
        └──► Queue for browser tab N  ──► ws.send_text(...)
```

Events that trigger broadcasts:
- `agent_started` — new agent run began
- `agent_token` — one streaming token from an agent
- `agent_done` — agent completed successfully
- `agent_error` — agent failed
- `scheduled_task_fired` — a reminder was triggered

---

## SQLite Schema

Database location: `~/.potts/memory.db`

```sql
CREATE TABLE facts (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    key      TEXT NOT NULL UNIQUE,   -- snake_case fact name
    value    TEXT NOT NULL,
    updated  TEXT NOT NULL           -- ISO 8601 UTC
);

-- facts_key UNIQUE INDEX on key column

CREATE TABLE sessions (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    date     TEXT NOT NULL,          -- ISO 8601 UTC
    summary  TEXT NOT NULL           -- one sentence
);

CREATE TABLE scheduled_tasks (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL,
    run_at     TEXT NOT NULL,        -- ISO 8601 UTC
    message    TEXT NOT NULL,        -- spoken when fired
    status     TEXT NOT NULL DEFAULT 'pending',  -- pending | done
    created_at TEXT NOT NULL         -- ISO 8601 UTC
);
```

---

## File Structure

```
potts/                          ← project root
├── .env.example                ← copy to .env and fill in keys
├── .env                        ← your secrets (gitignored)
├── main.py                     ← entry point: setup_logging + asyncio.run(_run_all)
├── pyproject.toml              ← project metadata, dependencies (uv)
├── Makefile                    ← install / run / dev / dashboard / clean
│
├── potts/                      ← Python package
│   ├── config.py               ← Config class, reads all env vars
│   ├── state.py                ← AppState singleton (shared mutable state)
│   ├── scheduler.py            ← background reminder poller (30s interval)
│   │
│   ├── voice/
│   │   ├── pipeline.py         ← pipecat pipeline: STT→LLM→TTS, wake word
│   │   ├── beep.py             ← 880 Hz / 440 Hz audio feedback tones
│   │   ├── mute_strategy.py    ← mutes mic while bot speaks (wake word exception)
│   │   └── notifier.py         ← TTS or notify-send routing
│   │
│   ├── llm/
│   │   └── prompt.py           ← BASE_PROMPT + memory context injection
│   │
│   ├── tools/
│   │   ├── registry.py         ← tool schemas + all handler functions
│   │   └── graph.py            ← Microsoft Graph: MSAL auth + email/calendar
│   │
│   ├── agents/
│   │   ├── base.py             ← BaseAgent: AsyncGroq streaming + state tracking
│   │   ├── code.py             ← CodeAgent (software engineering)
│   │   ├── research.py         ← ResearchAgent (information synthesis)
│   │   └── system.py           ← SystemAgent (Arch Linux administration)
│   │
│   ├── memory/
│   │   ├── store.py            ← SQLite CRUD: facts, sessions, scheduled_tasks
│   │   └── session.py          ← close_session: LLM fact extraction + summary save
│   │
│   └── api/
│       └── server.py           ← FastAPI app: REST + WebSocket + static files
│
├── dashboard/                  ← React frontend
│   ├── src/
│   │   ├── App.jsx             ← root component, WebSocket client, event router
│   │   └── components/
│   │       ├── StatusBar.jsx   ← voice active / WebSocket connected indicators
│   │       ├── AgentsPanel.jsx ← streaming agent output cards
│   │       ├── MetricsPanel.jsx
│   │       ├── MemoryPanel.jsx ← facts table
│   │       ├── HistoryPanel.jsx← session summaries
│   │       └── ScheduledPanel.jsx
│   ├── package.json
│   └── vite.config.js          ← dev proxy: /api + /ws → localhost:7860
│
└── doc/                        ← this documentation
    ├── getting-started.md
    ├── voice-interaction.md
    ├── tools.md
    ├── agents.md
    ├── memory.md
    ├── scheduler.md
    ├── dashboard.md
    ├── outlook.md
    ├── configuration.md
    └── architecture.md
```

---

## Key Design Decisions

**Why pipecat?**
Pipecat handles the hard parts of voice pipelines: audio transport, STT/TTS service integration, VAD, frame routing, and interruption support. Potts uses it as the pipeline backbone and extends it with a custom mute strategy and wake word event handlers.

**Why a singleton AppState instead of passing state around?**
The three concurrent tasks (voice, API, scheduler) need to share state without complex dependency injection. A module-level singleton imported from anywhere is simple and practical for a personal assistant that runs as a single process.

**Why SQLite?**
Zero dependencies, zero setup, survives restarts, inspectable with standard tools. Memory and reminders don't need the power of a real database server.

**Why asyncio throughout?**
The voice pipeline, API server, and scheduler all need to run concurrently. asyncio tasks share a single thread with cooperative multitasking, which is ideal for I/O-bound work like LLM API calls and audio streaming.
