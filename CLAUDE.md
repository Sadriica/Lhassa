# Lhassa — Claude Code context

## What this is
A cross-platform voice AI assistant (Linux, macOS, Windows). Voice pipeline: Deepgram STT → LLM → Cartesia TTS via pipecat. FastAPI serves the React dashboard and WebSocket fan-out. SQLite stores memory at `~/.lhassa/memory.db`.

## Commands
```bash
make install      # uv sync + npm install + npm run build (first time)
make run          # start Lhassa
make dev          # start with LHASSA_DEBUG=1 LHASSA_LOG_LEVEL=DEBUG
make dashboard    # rebuild React dashboard only
```

## Project layout
```
main.py                      # entry point — runs voice + API + scheduler concurrently
lhassa/config.py              # all config; validate() called once at startup in main.py
lhassa/voice/pipeline.py      # pipecat pipeline (wake word, VAD, STT, LLM, TTS)
lhassa/voice/mute_strategy.py # WAKE_PHRASES_CORE + WAKE_PHRASES_ES — single source of truth
lhassa/voice/notifier.py      # desktop notification router (Linux/macOS/Windows)
lhassa/llm/provider.py        # multi-provider LLM factory + cached async client singletons
lhassa/llm/prompt.py          # BASE_PROMPT + build_system_prompt()
lhassa/tools/registry.py      # all 23 tool schemas + handlers
lhassa/tools/search.py        # web search — Brave API with DuckDuckGo fallback
lhassa/tools/vision.py        # screenshot + vision LLM analysis (Linux/macOS/Windows)
lhassa/tools/monitor.py       # watch_process / watch_file / watch_command (background tasks)
lhassa/tools/github.py        # GitHub REST API client
lhassa/tools/graph.py         # Microsoft Graph client (Outlook email + calendar)
lhassa/agents/base.py         # BaseAgent — built-in tool loop (LHASSA_MAX_AGENT_ITERATIONS, 60s timeout)
lhassa/agents/claude_code.py  # ClaudeCliAgent — runs `claude --print` as subprocess
lhassa/agents/code.py         # CodeAgent — software engineering tasks
lhassa/agents/research.py     # ResearchAgent — research and synthesis
lhassa/agents/system.py       # SystemAgent — OS-aware system administration
lhassa/memory/store.py        # SQLite helpers (facts, sessions, scheduled_tasks)
lhassa/memory/session.py      # close_session() — called on shutdown to extract facts
lhassa/api/server.py          # FastAPI + WebSocket broadcast
lhassa/scheduler.py           # polls scheduled_tasks every 30s
lhassa/state.py               # AppState singleton — metrics, agent runs, WebSocket fan-out
dashboard/src/               # React + Vite dashboard
```

## Key design decisions

### Config is class-level, not instance-level
`lhassa/config.py` uses class-level attribute assignments evaluated at module import time (after `load_dotenv()`). `validate()` is called once in `main.py` before the pipeline starts — it's the single place all config errors surface. Do not move required key checks into `_require()`-style import-time calls; this was deliberately changed so importing config doesn't crash in tests or tooling.

### Provider auto-detection
`_detect_provider()` checks API keys in priority order: `ANTHROPIC_API_KEY` → `OPENAI_API_KEY` → `GROQ_API_KEY` → `OPENROUTER_API_KEY` → `CEREBRAS_API_KEY`. Setting one key is enough — no `LHASSA_LLM_PROVIDER` needed. Vision tools auto-detect from the same key.

### Cached async clients
`lhassa/llm/provider.py` caches `AsyncAnthropic`/`AsyncOpenAI` instances in `_agent_clients` so multiple agent runs reuse the same connection pool. `lhassa/tools/search.py`, `github.py`, and `graph.py` each cache a single `httpx.AsyncClient` in `_http`. All are closed in `main._close_http_clients()` on shutdown.

### Wake phrases — two layers
- `WAKE_PHRASES_CORE` + `WAKE_PHRASES_ES` in `mute_strategy.py` — single source of truth.
- `WakeWordOnlyMuteStrategy` uses the full `WAKE_VARIANTS` union (lets audio through during bot speech — false positives are harmless).
- `WakePhraseUserTurnStartStrategy` in `pipeline.py` uses language-aware set: core always, ES extras only when `LHASSA_STT_LANGUAGE` starts with `"es"`. Keep this distinction.
- Wake timeout is `config.wake_timeout` (default 300s, override with `LHASSA_WAKE_TIMEOUT`).

### Agent backends
- `claude_code`: `claude --print "<task>"` subprocess, streams stdout line-by-line to dashboard
- `builtin`: `BaseAgent` with OpenAI-compat loop (all providers) or Anthropic loop; same tool set both ways
- Auto-detected at import time via `shutil.which("claude")`. `LHASSA_AGENT_BACKEND` overrides.
- Max tool-loop iterations: `LHASSA_MAX_AGENT_ITERATIONS` (default 10).

### System agent — OS-aware prompts
`agents/system.py` and `agents/claude_code.py` build their system prompts/hints at import time using `platform.system()`. They auto-detect the distro on Linux via `platform.freedesktop_os_release()` and the package manager via `shutil.which`.

### WebSocket fan-out
`state.broadcast(dict)` puts a JSON message on every connected client's `asyncio.Queue`. Agents, monitors, and the scheduler all call this to stream output to the dashboard in real time.

### Memory extraction
`close_session()` in `lhassa/memory/session.py` is called on shutdown. It sends the last 40 conversation turns to the LLM and asks for JSON `{facts, summary}`. Facts are upserted into SQLite; summaries are stored capped at 100 rows. Uses `response_format: json_object` for OpenAI-compat providers; plain system-prompt instruction for Anthropic.

### Cross-platform
- **Screenshot**: `screencapture` (macOS), PowerShell + PIL fallback (Windows), scrot/maim/ImageMagick (Linux)
- **Notifications**: `osascript` via env-var injection (macOS), PowerShell BurntToast (Windows), `notify-send` (Linux)
- **Mic default**: `"pipewire"` on Linux, `""` (system default) on macOS/Windows
- **Beep**: PipeWire device scan only on Linux

## Conventions

- Tools are defined in two places: schema in `TOOLS` (ToolsSchema) and handler `handle_<name>` in `registry.py`. When adding a tool, add both and register in `register_tools()`.
- All tool handlers call `_bump_tool()` to increment the metrics counter.
- All tool handlers catch exceptions and call `params.result_callback(error_message)` — never let a handler raise.
- `read_file` enforces a 512 KB size cap in both `registry.py` (voice pipeline) and `agents/base.py` (agent tool loop).
- Background tasks (agents, monitors) are `asyncio.create_task(...)` — they run concurrently with the voice pipeline.
- Print statements with ANSI codes (`\033[2m...\033[0m`) are used for terminal UX (dim tool status lines). Logger is for structured logs. Both coexist intentionally.

## Do not
- Move `_require()` / import-time key validation back into `Config` class body.
- Add Spanish wake phrases to `WAKE_PHRASES_CORE` — they belong in `WAKE_PHRASES_ES`.
- Use `assert` for runtime checks — use `if ... raise RuntimeError(...)`.
- Add `LHASSA_LLM_PROVIDER` to examples where auto-detection from the key is sufficient.
- Change `log_level="warning"` on uvicorn — uvicorn's access logs are intentionally suppressed.
- Add Linux-specific tools/commands without a cross-platform fallback.

## Required env vars (minimum to run)
```
DEEPGRAM_API_KEY=    # STT
CARTESIA_API_KEY=    # TTS
GROQ_API_KEY=        # (or any other LLM key — auto-detected)
```

## Optional env vars (tuning)
```
LHASSA_WAKE_TIMEOUT=300               # seconds before sleeping after wake (default 300)
LHASSA_MAX_AGENT_ITERATIONS=10        # max tool-loop steps per agent run (default 10)
LHASSA_MIC_NAME=                      # audio device substring; "" = OS default
LHASSA_STT_LANGUAGE=en                # BCP-47 language code for Deepgram
LHASSA_AGENT_BACKEND=                 # claude_code | builtin (auto-detected)
LHASSA_AGENT_DIR=                     # working directory for claude --print agents
LHASSA_VISION_PROVIDER=auto           # auto | anthropic | openai | openrouter
LHASSA_DEBUG=0                        # 1 = show live STT in terminal
LHASSA_AUDIT_MAX=100                  # max audit events kept in memory + audit.json (minimum 10)
LHASSA_MEMORY_SNAPSHOT_INTERVAL=1800  # seconds between periodic memory snapshots (minimum 60)
LHASSA_SHELL_OUTPUT_MAX=2000          # max chars captured from shell commands (voice: 2000, agents: 3000 default)
LHASSA_FILE_CONTENT_MAX=4000          # max chars returned from read_file
```
