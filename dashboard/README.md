# Lhassa Dashboard

The web dashboard for the Lhassa brain — a React + Vite single-page app that is the
window into the do-and-sleep core: it watches live activity over a WebSocket and
drives the brain through the REST surface. Lhassa is a brain that orchestrates
models and tools; this is one of its world-facing surfaces (alongside CLI and
Telegram).

In production the FastAPI app (`lhassa/api/server.py`) serves the built assets from
`dashboard/dist/` at `http://127.0.0.1:7766`. In development the app runs standalone
on the Vite dev server and proxies the API + WebSocket to that same backend.

## Sections

The sidebar (`src/App.jsx`) is a set of command surfaces, one visible at a time:

- **Overview** — command-center home: attention count, watch pulse, running work, spend, health and quick actions (`OverviewPanel`)
- **Inbox** — one queue for approvals, running work, audit signals and important activity (`InboxPanel`)
- **Watchlist** — what Lhassa is watching now: scheduled senses, due work, heartbeat and running jobs (`WatchlistPanel`)
- **Capabilities** — read-only map of available local CLIs, skills, self-written tools, guardrails and active delegations (`CapabilitiesPanel`)
- **Brains** — the org topology (Flow / Tree / Templates + the relationship overlay); the React-Flow canvas is `OrgFlow`
- **Skills** — the 3-layer skill catalog (`SkillsPanel`)
- **Flows** — the n8n-style work-flow editor (`WorkflowsPanel` → the `WorkflowEditor` canvas)
- **Run** — the agentic local-CLI run surface (`AgenticPanel`)
- **Approvals** — the human-in-the-loop approval queue (`ApprovalQueuePanel`)
- **Tools** — self-written tools in quarantine + graduation (`QuarantinePanel`)
- **Reports** — reports plus AI-usage and metrics panels
- **Fleet** — fleet + health (`FleetPanel`, `HealthPanel`)
- **Activity** — agents, history, audit log, scheduled tasks, memory
- **Settings** — editable runtime knobs (`SettingsPanel`)

The active section deep-links via the URL hash (`#flows`, `#brains`, …) and remembers
the last one in `localStorage`. A hash may carry an optional sub-route tail so an open
flow is bookmarkable (`#flows/<flowId>`); browser Back / Forward walk between sections
and between the flow list and an open flow.

## Run the dev server

Requires Node with `npm`.

```bash
npm install      # install dependencies
npm run dev      # Vite dev server on http://localhost:5173
```

Build the production bundle:

```bash
npm run build    # → dashboard/dist/  (what the FastAPI app serves)
npm run preview  # serve the built dist/ locally to sanity-check it
```

From the repo root, `make dashboard` rebuilds this bundle and `make install` builds it
as part of first-time setup. In production the API app serves `dist/` directly, so a
rebuild is what ships UI changes.

## How it connects to the backend

- **REST** — panels call the backend under the `/api/...` base.
- **WebSocket** — `App.jsx` opens a single `/ws` connection to the same origin
  (`ws(s)://<host>/ws`) and reduces the `state.broadcast(...)` fan-out into UI state
  (`snapshot`, `voice_state`, `audit_event`, `agent_started` / `agent_token` /
  `agent_done` / `agent_error`, `scheduled_task_fired`, and tool-activity events). It
  auto-reconnects every 3s when the socket closes.
- **Dev proxy** — `vite.config.js` proxies `/api` → `http://localhost:7766` and `/ws`
  → `ws://localhost:7766` (WebSocket upgrade enabled), so the dev app talks to a
  locally running backend. In production the app is same-origin with the API, so no
  proxy is involved.
- **Auth** — the API middleware (`require_local_or_token` in `server.py`) allows
  loopback clients (`127.0.0.1`, `::1`, `localhost`) with no token. If
  `LHASSA_API_TOKEN` is set, every request (and the WebSocket) must present it — HTTP
  via `Authorization: Bearer <token>` or the `x-lhassa-api-token` header; the WebSocket
  via that header or a `?token=` query param. Same-origin loopback usage needs nothing.

## Main endpoints it uses

From the panels and the backend surface documented in `doc/architecture.md` and `doc/dashboard.md`:

- `/api/overview`, `/api/status`, `/api/metrics` — command-center aggregate, runtime status and metrics
- `/api/org/*` (topology, brains, route, templates, relationships + `/kinds`)
- `/api/flows`, `/api/flows/{id}`, `/api/flows/{id}/run`
- `/api/skills/*`
- `/api/code` (+ `/clis`, `/runs`) — the agentic local-CLI door
- `/api/config` (GET / PATCH) — the settings service
- `/api/approvals` — approval queue (also rendered inside Inbox)
- `/api/reports`, `/api/projections`, `/api/llm/{ledger,limits,ccusage}`
- `/api/fleet`
- `/api/activity`, `/api/audit`, `/api/scheduled`, `/api/tools` (+ graduate), `/api/memory/*`, `/api/agents` — Inbox, Watchlist, Capabilities and Activity source data
- `/ws` — the live event stream

## Lint

```bash
npm run lint     # eslint (flat config in eslint.config.js)
```

ESLint uses the flat config with `@eslint/js` recommended plus the React Hooks and
React Refresh plugins; `dist` is ignored.

## End-to-end tests

```bash
npm run test:e2e     # playwright test  (spec in e2e/dashboard.spec.js)
```

`playwright.config.js` runs against a Vite dev server it starts on
`http://127.0.0.1:4173` (`npm run dev -- --host 127.0.0.1 --port 4173`, reusing an
existing server if one is already up). The spec stubs the `/ws` WebSocket with a mock
that replays a `snapshot` payload, so the e2e run does not need a live backend. Install
the Playwright browsers once with `npx playwright install` before the first run.

## Component structure

- **`src/App.jsx`** — the shell: the WebSocket connection + event reducer, the
  hash + `localStorage` section routing, the static sidebar rail (a slide-in drawer
  below 1024px), the mobile top bar, and the per-section panel layout. Each panel is
  wrapped in an `ErrorBoundary`.
- **`src/components/`** — the panels listed under *Sections* above, plus `StatusBar`,
  `OrgTree`, `TemplatesPicker`, `ErrorBoundary`, and the shared `flowNodeKinds.js` /
  `relationshipKinds.js` helpers.
- **`InboxPanel` + `WatchlistPanel` + `CapabilitiesPanel`** — intentionally thin
  operational panels over the current APIs. They are the UI landing zone for the
  persistent event/inbox, real watch-item backend, and unified capability registry
  when those arrive; until then they compose `/api/overview`, `/api/audit`,
  `/api/scheduled`, `/api/tools`, `/api/skills`, `/api/code/clis`, and approval/code
  routes.
- **`CommandPalette`** — a ⌘K launcher (opened via the sidebar hint or the
  `lh-cmdk-open` window event) that navigates between sections.
- **`ToastHost` + `src/lib/toast.js`** — the single, dependency-free action-feedback
  channel. Any panel calls `toast.success / error / info(...)` (or `toast.show({...})`
  for a custom duration / action button); `<ToastHost/>` (mounted once in `App.jsx`)
  renders the queue. Both `ToastHost` and `CommandPalette` are mounted once at the app
  level.
- **React-Flow canvases** — `OrgFlow` (the Brains topology) and `WorkflowEditor` (the
  Flows editor), both built on `@xyflow/react` and **lazy-loaded** (`React.lazy` +
  `Suspense`) so the heavy canvas code only loads when its section opens.

## Design — Matte Obsidian

The theme lives in `src/index.css` as CSS custom properties on `:root` (a Tailwind v4
`@theme`). "Matte Obsidian" is a quiet-luxury dark theme drawn from Lhasa: a duotone of
temple **gold** (`--lh-gold` `#d9a521`, a hairline accent) and monk-robe deep **red**
(`--lh-red` `#a23e34`, the identity co-accent) over warm-charcoal matte surfaces, with
warm-white hairline borders and sage **green** for healthy. Surfaces are flat and matte
— no gradients, blur, or colored glows. Typography is editorial: the **Fraunces**
transitional serif for headings and the wordmark, **Inter** for UI, and **JetBrains
Mono** for ids and metrics (all loaded from Google Fonts in `index.html`). The gold/red
stupa mark is the shared Lhassa visual identity.
