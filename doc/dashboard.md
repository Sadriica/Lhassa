# Dashboard

The Lhassa dashboard is the brain's real-time control surface: the org chart, work-flow editor, skills, an agentic run panel, watchlist/approvals for the trust loop, analytics, fleet health, and the full activity/episode log; not just a voice-status monitor. It is one of the world-facing shells composed on top of the API (`api/server.py` + domain routers), alongside the CLI, Telegram, and voice. The API ships in the **api pack** (`packages/lhassa-api`, `uv sync --package lhassa-api`; `make install` installs every pack); the dashboard source stays at the checkout root in `dashboard/`.

---

## URL

```
http://localhost:7766
```

The dashboard is served by the FastAPI backend alongside the REST and WebSocket APIs. The WebSocket connection automatically upgrades to `wss://` when accessed over HTTPS. The server binds to `127.0.0.1` (localhost only) by default. Every `/api`, `/checkin`, and `/report` request (including the `/ws` upgrade) goes through `require_local_or_token` middleware (`api/server.py`): a **loopback** client (`127.0.0.1`/`::1`/`localhost`) is allowed with no token, but any non-local request is refused (`403`) unless it presents `LHASSA_API_TOKEN` as a `Bearer` header, `X-Lhassa-Api-Token` header, or (for the WebSocket) a `?token=` query param. Lhassa can run privileged commands, so don't set `LHASSA_API_TOKEN` and expose the port without understanding the exposure. Set `LHASSA_API_HOST=0.0.0.0` only if you understand it then becomes reachable by any device on your LAN:

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

The React app is pre-built and served as static files by FastAPI. There is no separate Node.js process when running `make run`: just the single Python process serving everything.

On connection, the WebSocket sends a **full snapshot** of current state immediately, so the dashboard renders without waiting for the next event. After that, it receives incremental events as they happen.

---

## WebSocket Auto-Reconnect

The dashboard automatically reconnects to the WebSocket every 3 seconds if the connection drops. This means:

- If Lhassa restarts, the dashboard will reconnect and re-render with the new state.
- If your browser goes to sleep and wakes up, it reconnects without a page refresh.
- Multiple browser tabs can be open simultaneously; each is an independent WebSocket client.

---

## Sections

The sidebar (`dashboard/src/lib/sections.js`) is grouped into five clusters; the id
drives hash routing (`#<id>`), localStorage persistence, and the command palette
(`Cmd/Ctrl-K`):

| Group | Section id | Label | Component(s) |
|---|---|---|---|
| Command | `overview` | Overview | `OverviewPanel.jsx` |
| Command | `inbox` | Inbox | `InboxPanel.jsx` |
| Command | `run` | Run task | `AgenticPanel.jsx` |
| Autonomy | `watchlist` | Watchlist | `WatchlistPanel.jsx` |
| Autonomy | `flows` | Flows | `WorkflowsPanel.jsx` (the n8n-style work-flow editor) |
| Autonomy | `flow-runs` | Flow runs | `DurableRunsPanel` (durable runs, `/api/flow-runs`) |
| Autonomy | `projects` | Projects | `ProjectsPanel.jsx` |
| Autonomy | `approvals` | Approvals | `ApprovalQueuePanel.jsx` |
| Capability | `capabilities` | Capabilities | `CapabilitiesPanel.jsx` |
| Capability | `tools` | Actions | `QuarantinePanel.jsx` (self-written-tool gates/graduation) |
| Capability | `skills` | Skills | `SkillsPanel.jsx` |
| Capability | `brains` | Brains | `BrainsPanel.jsx` (org Flow/Tree/Templates + relationship overlay) |
| Capability | `knowledge` | Knowledge | `KnowledgePanel.jsx` (indexed documents + episode recall) |
| Operations | `artifacts` | Artifacts | `ArtifactsPanel.jsx` |
| Operations | `research` | Research | `ResearchPanel.jsx` |
| Operations | `integrity` | Integrity | `IntegrityPanel.jsx` |
| Operations | `calendar` | Calendar | `CalendarPanel.jsx` |
| Operations | `postulaciones` | Postulaciones | `PostulacionesPanel.jsx` |
| Operations | `fleet` | Readiness | `FleetPanel.jsx` + `HealthPanel.jsx` |
| Operations | `reports` | Analytics | `ExecutionAnalyticsPanel.jsx` + `ReportsPanel.jsx` + `AIUsagePanel.jsx` + `MetricsPanel.jsx` |
| Operations | `activity` | Activity | `AgentsPanel.jsx` + `RecentEpisodesPanel.jsx` + `HistoryPanel.jsx` + `AuditPanel.jsx` + `ScheduledPanel.jsx` + `MemoryPanel.jsx` |
| System | `settings` | Settings | `SettingsPanel.jsx` (+ the about card) |

Notes on names that don't match their id: the `tools` section is labelled
**Actions** in the sidebar (it's the self-written-tool quarantine/graduation
view, not the voice-tool catalog in `doc/tools.md`); `fleet` is labelled
**Readiness**; `reports` is labelled **Analytics**.

**Pack-aware nav.** A section may name the capability pack it needs (the optional
`pack` field in `sections.js`): `research` needs the research pack, `calendar` the
calendar pack, `postulaciones` the cv pack. On load the dashboard asks
`GET /api/packs` (the loaded `PACKS` manifests, `{"packs": [{name, summary}]}`) and
hides the sections whose pack is absent, in the nav rail and the command palette
alike. If `/api/packs` fails (an older backend, a network error) every section shows.

### Status Bar (top)

Shows connection/voice indicators at a glance:

| Indicator | Description |
|---|---|
| **WebSocket** | Green dot = connected to Lhassa API. Grey = disconnected (Lhassa not running or restarting). |
| **Voice** | Green dot = voice pipeline is active. Grey = voice is idle. |

### Overview

`OverviewPanel.jsx`, backed by `GET /api/overview`: one defensive aggregate:
brains, skills, pending approvals, today's spend, health, recent activity, and
tool states, all gathered independently so one failing subsystem never 500s the
whole panel. It also includes the personal secretary snapshot (`personal`):
priorities for today, the latest check-in, and nearby tasks/approvals, rendered
in the **Today with you** card. It is the same digest `lhassa status` prints on
the CLI, with dashboard-only personal context added for the home surface.

### Run task

`AgenticPanel.jsx`: the dashboard side of the **agentic run surface**
(`POST /api/code`, backed by `agents/local_cli.py`): pick an installed local AI
CLI (claude/codex/opencode, `GET /api/code/clis`), launch a task, and watch it
stream token-by-token over `/ws` (`agent_started`/`agent_token`/`agent_done`).
`GET /api/code/runs` lists recent runs so the panel can list/resume; a run can
be stopped mid-flight (`POST /api/code/{run_id}/stop`).

### Flows

`WorkflowsPanel.jsx` (with `WorkflowEditor.jsx` and `components/workflow/*`);
the n8n-style canvas over `flows/model.py`'s `trigger → task → gate → output`
(+ `branch`) DAG. `GET/POST /api/flows`, `GET/PUT/PATCH/DELETE /api/flows/{id}`,
`POST /api/flows/{id}/run`.

`lhassa seed apply personal` creates the canonical personal secretary flows
visible here: `personal-morning-briefing` and `personal-evening-briefing`. Both
pin their task nodes to the `personal` department brain, but their node order
differs so morning planning and evening review can evolve independently.

### Projects

`ProjectsPanel.jsx`: a read-only view over the project entity
(`core/projects.py`): `GET /api/projects` lists projects (kind + status
badges; a toggle shows archived), and opening one loads its scoped runs,
delivered artifacts, and recent episodes (`GET /api/projects/{id}/{runs,
artifacts,episodes}`). Each run's evidence rung; `prepared` / `observed` /
`verified`: renders as a badge, mirroring the same three-rung contract
`lhassa/memory/evidence.py` computes server-side. Creation is a CLI/chat act
(`lhassa project create`), not a dashboard form.

### Approvals

`ApprovalQueuePanel.jsx`: the HITL queue (`security/approval.py`). Each pending
row carries a `summary: {title, detail}` from `approval.describe()` (e.g. `"Graduate
self-written tool 'x'"`, `"Work-flow paused at gate 'y'"`, `"Org task for 'z'"`),
so the queue shows a described, human-readable title rather than a raw payload
dump. Approve/deny buttons resolve through the same path as the Telegram
`/approve`/`/deny` commands.

### Actions (tools section)

`QuarantinePanel.jsx`: the self-improvement pipeline's view: candidate tools
in quarantine, their gate results (segura/correcta/mejor), and a graduate action
(`POST /api/tools/{name}/graduate`, refused with `409` if the gates weren't
passed). This is not the voice-tool catalog (`doc/tools.md`); it is one layer
up; the tools *the brain writes for itself*.

### Brains

`BrainsPanel.jsx`: the org chart (`OrgTree.jsx`/`OrgFlow.jsx`), skill
assignment, use-case templates, and the relationship overlay (`reads`/
`notifies`/`reviews`/`collaborates` edges) drawn dashed over the command tree.
The `personal` seed adds the `personal` department under Lhassa for assistant /
secretary work.

### Readiness (fleet section)

`FleetPanel.jsx` + `HealthPanel.jsx`: instance/fleet readiness and health
checks (`health/checks.py`).

### Analytics (reports section)

Four panels together: `ExecutionAnalyticsPanel.jsx` (`GET /api/analytics/summary`,
`/flows`, `/brains`, `/runs`: run/execution analytics, `analytics_routes.py`,
prefix `/api/analytics`), `ReportsPanel.jsx` (`GET /api/reports?n=50` + `GET
/api/projections`: episode outcomes summary and a linear cost projection),
`AIUsagePanel.jsx` (`GET /api/llm/{ledger,limits,ccusage}`), and
`MetricsPanel.jsx` (live in-process counters).

### Activity

The catch-all operations view, three panels of history/log surfaces plus two
side panels:

- **Agents Panel** (`AgentsPanel.jsx`); every agent run (the `code`/`research`/
  `system` sub-agents from `doc/agents.md`, and `lhassa code` CLI runs). Each
  card shows agent/CLI name, a status badge (`running` amber / `done` green /
  `error` red), the task, and the streaming output. Sorted most-recent-first;
  runs persist until Lhassa restarts, and `snapshot` on WS connect replays
  existing runs to a newly opened tab.
- **Recent Episodes Panel** (`RecentEpisodesPanel.jsx`); the append-only
  episode log, loaded via `GET /api/reports?n=0` (every episode for the
  instance) with a client-side search box plus importance/trigger/order
  filters applied over that loaded page. The backend also exposes
  `GET /api/episodes/search?q=…` (`reports_routes.py`), a BM25-ranked SQLite
  FTS5 full-text search (`core/db.py:search_episodes`); it exists and is
  tested, but as of this writing no dashboard panel calls it yet; the client
  still filters the `/api/reports` page in-browser.
- **History Panel** (`HistoryPanel.jsx`); session summaries (legacy
  `memory/session.py`/`store.py` voice-session store).
- **Audit Panel** (`AuditPanel.jsx`); the in-memory audit event feed
  (`audit_event` WS events + the snapshot's `audit` array).
- **Scheduled Panel** (`ScheduledPanel.jsx`); the ad-hoc `schedule_reminder`
  tool's tasks (`GET /api/scheduled`, last 50, any status); see
  `doc/scheduler.md` for how this differs from the brain's own task/heartbeat
  scheduling.
- **Memory Panel** (`MemoryPanel.jsx`); stored facts (`GET /api/memory/facts`).

### Settings

`SettingsPanel.jsx`: the editable-settings surface over `settings.py`'s
`EDITABLE` allowlist (`GET/PATCH /api/config`), each field showing its default,
override state, and restart hint (secrets masked). It ends in an **about
card**: the flag (*"Build the brain, rent the body."*) and identity sentence
from `doc/narrative.md`, rendered in Lhassa's own first-person register, with a
pointer to the docs.

---

## Building the Dashboard

The dashboard must be built before it will appear at `http://localhost:7766`. `make install` does this for you on first setup.

To rebuild after editing dashboard source files:

```bash
make dashboard
# equivalent to:
cd dashboard && npm run build
```

The compiled output lands in `dashboard/dist/` and is served directly by FastAPI. The api pack finds it from the checkout (the nearest ancestor of `lhassa/api/_shared.py` holding a `dashboard/` directory, else `./dashboard/dist`); set `LHASSA_DASHBOARD_DIST` to serve a build from anywhere else.

If the dashboard hasn't been built, visiting `http://localhost:7766` returns a JSON message:

```json
{
  "message": "Lhassa API running. Dashboard not built yet.",
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

This starts Vite's dev server, typically on port **5173**. The dev server proxies `/api` and `/ws` to the real Lhassa backend at `localhost:7766`, so you need Lhassa running at the same time:

```bash
# Terminal 1
make run

# Terminal 2
make dashboard-dev
```

Then open `http://localhost:5173` for the hot-reloading dev version. Changes to `dashboard/src/` appear instantly.

---

## REST API Endpoints

All endpoints return JSON. Loopback requests need no token; non-local requests
need `LHASSA_API_TOKEN` (see the auth note under URL above). This is not the
full route list (each domain router documents its own detail); it's the set
the dashboard's panels actually call, plus the always-relevant liveness ones.

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/status` | Voice active flag, uptime, metrics, agent count |
| `GET` | `/api/health` | Liveness + config snapshot (provider, dashboard built, ws client count) |
| `GET` | `/api/overview` | The Overview panel's one defensive aggregate |
| `GET` | `/api/agents` | All agent runs for the current session |
| `GET` | `/api/code/clis` / `POST /api/code` / `GET /api/code/runs` / `POST /api/code/{id}/stop` | The Run task panel (agentic local-CLI door) |
| `GET/POST /api/flows`, `GET/PUT/PATCH/DELETE /api/flows/{id}`, `POST /api/flows/{id}/run` | The Flows editor |
| `GET` | `/api/projects[/{id}]`, `/api/projects/{id}/{runs,artifacts,episodes}` | The Projects panel |
| `GET` | `/api/watchlist` | Read-only preview of what would wake the brain right now |
| `GET/POST` | `/api/approvals`, `/api/approvals/{id}` (body `{action: "approve"\|"deny"}`) | The Approvals queue |
| `GET` | `/api/tools`, `POST /api/tools/{name}/graduate` | The Actions (quarantine/gates) panel |
| `GET/POST/PUT/DELETE` | `/api/org/*`, `/api/skills/*` | The Brains panel (topology, route, templates, relationships, skills) |
| `GET` | `/api/fleet` | The Readiness panel |
| `GET` | `/api/analytics/{summary,flows,brains,runs}` | The Analytics/Execution panel |
| `GET` | `/api/reports`, `/api/projections` | Episode/cost reporting |
| `GET` | `/api/episodes/search` | FTS5 episode recall (Knowledge panel, Activity search) |
| `GET` | `/api/flow-runs`, `/api/artifacts`, `/api/research/*`, `/api/integrity/*`, `/api/calendar/*`, `/api/postulaciones` | The Flow runs, Artifacts, Research, Integrity, Calendar and Postulaciones panels |
| `GET` | `/api/llm/{ledger,limits,ccusage}` | The AI Usage panel |
| `GET` | `/api/metrics` | Live counters (LLM calls, tool calls, etc.) |
| `GET` | `/api/audit` | The audit event feed |
| `GET` | `/api/scheduled` | Last 50 ad-hoc reminder tasks (any status); see `doc/scheduler.md` |
| `GET` | `/api/memory/facts` | All stored facts |
| `GET` | `/api/memory/sessions` | Last 20 session summaries |
| `GET/PATCH` | `/api/config` | The Settings panel (the editable-settings service) |
| `GET/POST` | `/api/export`, `/api/import` | Whole-brain state export/import (audit + memory snapshot) |
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
| `snapshot` | On connection | `voice_active`, `metrics`, `audit[]`, `agents[]` |
| `agent_started` | Agent/CLI run begins | `run_id`, `agent`, `task` |
| `agent_token` | Each streaming token | `run_id`, `token` |
| `agent_done` | Agent/CLI run completes | `run_id`, `output` |
| `agent_error` | Agent/CLI run fails | `run_id`, `error` |
| `agent_stopped` | A run is stopped via `POST /api/code/{id}/stop` | `run_id` |
| `audit_event` | An audited action occurs | (audit event fields; the Audit panel feed) |
| `voice_state` | Voice pipeline goes active/idle |; |
| `scheduled_task_fired` | An ad-hoc reminder fires (`schedule_reminder` tool) | `task` |
| `process_done` | Watched process exits | `pid`, `label` |
| `file_event` | Watched file matched | `path`, `line` |
| `command_done` | Watched command succeeded | `command`, `output` |

---

## Changing the Port

Set `LHASSA_API_PORT` in your `.env`:

```
LHASSA_API_PORT=8080
```

Then access the dashboard at `http://localhost:8080`.
