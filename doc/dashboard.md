# Dashboard

The Potts dashboard is a real-time web interface for monitoring what Potts is doing. It shows voice status, running agents with their streaming output, memory facts, session history, scheduled reminders, and live metrics.

---

## URL

```
http://localhost:7766
```

The dashboard is served by the FastAPI backend alongside the REST and WebSocket APIs. The WebSocket connection automatically upgrades to `wss://` when accessed over HTTPS. The server binds to `127.0.0.1` (localhost only) by default — the dashboard is unauthenticated and Potts can run privileged commands, so it must not be exposed on the network. Set `POTTS_API_HOST=0.0.0.0` only if you understand it then becomes reachable by any device on your LAN:

```
http://<your-machine-lan-ip>:7766
```

You can find your LAN IP with `ip addr show` or `hostname -I`.

---

## Architecture

| Layer | Technology |
|---|---|
| Backend | FastAPI + uvicorn |
| Frontend | React 19 + Tailwind CSS v4 |
| Real-time updates | WebSocket (`/ws`) |
| Build tool | Vite 8 |
| Static file serving | FastAPI `StaticFiles` (serves `dashboard/dist/`) |

The React app is pre-built and served as static files by FastAPI. There is no separate Node.js process when running `make run` — just the single Python process serving everything.

On connection, the WebSocket sends a **full snapshot** of current state immediately, so the dashboard renders without waiting for the next event. After that, it receives incremental events as they happen.

---

## WebSocket Auto-Reconnect

The dashboard automatically reconnects to the WebSocket every 3 seconds if the connection drops. This means:

- If Potts restarts, the dashboard will reconnect and re-render with the new state.
- If your browser goes to sleep and wakes up, it reconnects without a page refresh.
- Multiple browser tabs can be open simultaneously — each is an independent WebSocket client.

---

## Panel Descriptions

### Status Bar (top)

Shows two indicators at a glance:

| Indicator | Description |
|---|---|
| **WebSocket** | Green dot = connected to Potts API. Grey = disconnected (Potts not running or restarting). |
| **Voice** | Green dot = voice pipeline is active. Grey = voice is idle. |

### Agents Panel (left, 2/3 width)

Lists all agent runs for the current session. Each card shows:

- **Agent type** (`code`, `research`, `system`)
- **Status badge** — `running` (amber), `done` (green), `error` (red)
- **Task** — the full task description
- **Output** — streams in real time as the agent generates tokens. Full output is always visible.

Cards are sorted most-recent-first. Agents from the current session persist until Potts is restarted. If you open the dashboard after an agent has already finished, the output is still visible.

### Session History Panel (left, below Agents)

Shows the 20 most recent session summaries from the database, newest first. Each entry shows:
- Date (YYYY-MM-DD)
- One-sentence summary of that session

This is a static snapshot loaded from the REST API when the page opens. Refresh the page to see updated history after a new session completes.

### Metrics Panel (right column, top)

Shows live counters for the current Potts process:

| Metric | What it counts |
|---|---|
| LLM calls | LLM API calls made (voice pipeline + agent tool loop iterations + session extraction) |
| Tool calls | Times a tool was invoked (web_search, shell, monitor events, etc.) |
| Sessions | Sessions saved to memory |
| Agents launched | Background agents started |

Metrics are in-memory only — they reset to zero each time Potts starts.

### Scheduled Tasks Panel (right column, middle)

Shows all scheduled reminders from the database (last 50). Each row shows:
- Task name
- Scheduled time
- Message
- Status (pending / done)

### Memory / Facts Panel (right column, bottom)

Shows all stored facts from `~/.potts/memory.db` as a table:
- Key
- Value
- Last updated timestamp

This is a static snapshot. Refresh the page to see facts updated after a new session.

---

## Building the Dashboard

The dashboard must be built before it will appear at `http://localhost:7766`. `make install` does this for you on first setup.

To rebuild after editing dashboard source files:

```bash
make dashboard
# equivalent to:
cd dashboard && npm run build
```

The compiled output lands in `dashboard/dist/` and is served directly by FastAPI.

If the dashboard hasn't been built, visiting `http://localhost:7766` returns a JSON message:

```json
{
  "message": "Potts API running. Dashboard not built yet.",
  "hint": "Run: cd dashboard && npm run build"
}
```

---

## Development Mode with Hot Reload

For working on the dashboard's React/Tailwind source:

```bash
make dashboard-dev
# equivalent to:
cd dashboard && npm run dev
```

This starts Vite's dev server, typically on port **5173**. The dev server proxies `/api` and `/ws` to the real Potts backend at `localhost:7766`, so you need Potts running at the same time:

```bash
# Terminal 1
make run

# Terminal 2
make dashboard-dev
```

Then open `http://localhost:5173` for the hot-reloading dev version. Changes to `dashboard/src/` appear instantly.

---

## REST API Endpoints

All endpoints return JSON. No authentication is required.

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/status` | Voice active flag, uptime, metrics, agent count |
| `GET` | `/api/agents` | All agent runs for the current session |
| `GET` | `/api/memory/facts` | All stored facts |
| `GET` | `/api/memory/sessions` | Last 20 session summaries |
| `GET` | `/api/scheduled` | Last 50 scheduled tasks (any status) |
| `GET` | `/api/metrics` | Live counters (LLM calls, tool calls, etc.) |
| `WS` | `/ws` | WebSocket event stream |

### Example: check status

```bash
curl http://localhost:7766/api/status
```

```json
{
  "voice_active": true,
  "metrics": {
    "llm_calls": 5,
    "tool_calls": 3,
    "sessions": 0,
    "agents_launched": 1
  },
  "agent_count": 1,
  "uptime_seconds": 342
}
```

### Example: read facts

```bash
curl http://localhost:7766/api/memory/facts
```

```json
[
  {"key": "name", "value": "Alice", "updated": "2026-03-27T18:42:11+00:00"},
  {"key": "job", "value": "Software engineer", "updated": "2026-03-25T14:10:05+00:00"}
]
```

### WebSocket event types

| Event type | When it fires | Key fields |
|---|---|---|
| `snapshot` | On connection | `voice_active`, `metrics`, `agents[]` |
| `agent_started` | Agent begins | `run_id`, `agent`, `task` |
| `agent_token` | Each streaming token | `run_id`, `token` |
| `agent_done` | Agent completes | `run_id`, `output` |
| `agent_error` | Agent fails | `run_id`, `error` |
| `scheduled_task_fired` | Reminder triggers | `task` |
| `process_done` | Watched process exits | `pid`, `label` |
| `file_event` | Watched file matched | `path`, `line` |
| `command_done` | Watched command succeeded | `command`, `output` |

---

## Changing the Port

Set `POTTS_API_PORT` in your `.env`:

```
POTTS_API_PORT=8080
```

Then access the dashboard at `http://localhost:8080`.
