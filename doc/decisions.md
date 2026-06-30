# Lhassa — decisions log

Running log of the architectural/operational decisions for the Lhassa "brain"
build. The source of truth for the architecture is `doc/lhassa-brief.md` (a
living doc — correct it after each phase per §4). This file records the choices
made along the way. Newest first.

## 2026-06-30 — Elastic & modular hardening pass (A–E) + Telegram live + org topology

**Owner principle (now a standing constraint):** Lhassa must be **fully elastic and highly
modular** — every swap point behind an interface + registry, capabilities **drop-in** (add a
module without editing core), and the org layer must run **1 brain OR N brains** with a
**user-defined hierarchy**. Done as 5 slices + config surface (`org_departments`,
`notify_channels`, `plugins_dir`, `org_topology_file`):
- **A (keystone)** `registry.py` (`Registry[T]`: register as call/decorator, get/get_or_none/
  NotRegistered, as_mapping live view, duplicate-protection) + `plugins.py` (`load_plugins`
  imports from `plugins_dir` AND the `lhassa.plugins` entry-point group — self-registration with
  no core edit; idempotent, never raises). Adopted in `core/actions.py` (public API preserved;
  `ACTIONS` is now a live read-only view; `register_action` drop-in seam).
- **B (channels)** `notify/channels.py` — `Channel` protocol + registry (`@register_channel`);
  `TelegramChannel`/`DesktopChannel`/`LogChannel`; `dispatch.notify()` fans out over
  `active_channels(config)` from `notify_channels`. **Fixed the Telegram inbound**: answers with a
  tool-less brain think (memory + routine opencode provider, `despertar_por='telegram'`) instead
  of the 26-tool GROQ agent that emitted malformed tool calls (`tool_use_failed` 400s).
- **B2 (commands)** `notify/commands.py` — Registry-backed slash-command handlers
  (`@register_command` drop-in): `/start`, `/help` (lists registered commands live), `/status`
  (provider+model+health+today's spend, **LLM-free**). Inbound dispatches commands before the brain.
- **C (boundaries)** `tests/test_architecture.py` — an `ast`-based guard (no new dependency)
  forbidding layer-crossing imports (spine/interfaces/adapters ↛ shells; harness ⟂ llm; keystone
  imports no feature module), rules measured from the real graph, with self-tests proving teeth.
  `doc/architecture-layers.md` documents the layers.
- **E (org topology)** `org/topology.py` — `BrainNode`/`Topology` (validated tree) + `load/save`,
  config-driven (`org_topology_file`), **standalone 1 brain up to arbitrary depth**;
  `coordinator.run_goal()` walks the tree (typed TASK_ASSIGN down, **only leaves execute**, result
  bubbles up; RBAC by coordinate-vs-execute so new roles need no RBAC edit); `lhassa org`
  tree/list/add/remove/route/run to create & manage the chart. `GeneralBrain`/`DepartmentBrain`
  unchanged.

**Telegram (live):** new bot **@Lhassa_bot** (old one revoked), token + chat id in `.env`
(gitignored); outbound verified (real delivery); inbound answers via opencode (no tool errors);
bot name/description/short-description/commands set in **English** via the Bot API; a
`scripts/systemd/lhassa-telegram.service` user unit is ready to enable. **Avatar** must be set via
@BotFather `/setuserpic` (not exposable via the Bot API).

**Verified:** **718 tests** (full suite ×2, stable — the prior telegram test-isolation flake is
gone), ruff clean, `lhassa --help` lists `org`, dashboard builds. **Live smokes:** a 3-level org
chart routed goals to the correct leaves (coordinators never executed); a single-brain chart
executed directly; `/help` + `/status` rendered offline; an inbound poll answered a real Telegram
message via opencode at $0. **Backlog / `[SUPUESTO]`:** adopt the registry in the remaining
registries (providers/harnesses/verifiers/specialists) as they're touched (continuous); `/status`
reads the gateway ledger path/schema directly; route ties break by child declaration order.

## 2026-06-30 — Agentic run + typed relationships + work-flows + chat-via-CLI (+ docs)

A large feature arc, all committed and live, full suite green (974 tests). Backends were built as
self-contained NEW modules + APIRouters (no server.py contention) and mounted at integration.
- **Chat via local CLIs + `lhassa code`**: the chat (CLI/voice/agent) can run through claude/codex/
  opencode — the owner's subscription, NO API key (`llm_provider=claude` → `run_once` routes to the
  CLI ModelProvider, tool-less; honors an explicit Chat model). The Settings panel gained **Chat
  provider + Chat model** (a separate seat from the heartbeat) and now REJECTS an API provider with
  no key (the bug that broke chat). NEW `lhassa code "<task>"` runs an AGENTIC task through the CLI
  (edits files in the cwd) and **streams live** (a `LocalCliAgent.on_line` sink).
- **Agentic run surface** (`9284651`/`8b82b90`): `POST /api/code` launches a CLI run streamed over
  the existing WebSocket; the dashboard **Run** section watches it live; `lhassa-app.service` gained
  `~/.local/bin` on PATH so it finds the claude CLI.
- **Typed org RELATIONSHIPS** (`b52c1c2`/`dad07d0`): an overlay graph on the command tree —
  `reads`/`notifies`/`reviews` (directed) + `collaborates` (symmetric); `permits()` runtime seam;
  `/api/org/relationships`; the Brains canvas draws dashed per-kind edges (legend + toggles) and the
  inspector adds/removes them.
- **Work-FLOWS** (`998c834`/`d90f5e1`): `lhassa/flows/` model+store+engine (`trigger→task→gate→
  output`+branch DAG; **hybrid** task routing — pin a brain else auto-route by skill; data threads
  along edges; offline/$0 unless `LHASSA_ORG_LIVE`) + `/api/flows` + an n8n-style **Flows** editor.
  Live smoke: create→run (`completed`, trigger✓→task✓→output✓)→delete.
- **English seed rename** (`6edaf3b`): optimizacion/documentacion/finanzas → optimization/
  documentation/finance across code + tests (+ migrated the live org.json).
- **Test isolation fix** (`345b4be`): `config.py` no longer loads `~/.lhassa/settings.env` under
  tests (conftest sets `LHASSA_NO_RUNTIME_OVERRIDES=1`) — the owner's live `LHASSA_LLM_PROVIDER=claude`
  was leaking into the suite.
- **Inspector/flow fixes**: render the Lhassa admin node; inspector edits persist (limits cleared by
  omission, not null); per-brain skill RECOMMENDATIONS by name/department; skills show only the
  brain's assigned set; parent dropdown pre-selected; department dropdown + create; department group
  boxes on the canvas.
- **Docs** (`55c8181`/`49e37af`): README/CLAUDE/architecture refreshed + NEW `doc/cli.md`;
  relationships + flows corrected from "reserved" to shipped; the 9 dashboard sections + data dir.

## 2026-06-30 — Product/UX batch: skills, Lhassa-as-root, org flow, CLI palette, settings, templates, Lhasa reskin

A big autonomous product/UX push. All committed, gates green (full suite 849 → 876), no AI
attribution. Lhassa is the city's identity now — visually and organizationally.
- **First-class skills** (`3323996`): `lhassa/skills/` — SKILL.md format (YAML frontmatter +
  instructions, the skills.md / awesome-agent-skills convention) + JSON form, `SkillStore`
  (~/.lhassa/skills), AI create/improve (degrade without a model), offline `recommend` with a
  seed catalog, `brains_with_skill`/`assign_to_brain`. `lhassa skills` + `/api/skills/*`.
- **Lhassa = permanent root administrator** (`54701bb`): `config.root_admin_id` (default
  "lhassa") always roots the org; it's never removed/demoted; `promote` makes a brain a top-level
  child of Lhassa (never unseats it); `default_topology`/`load_topology` pin/migrate the admin;
  `describe()` exposes `admin`/`is_admin`.
- **n8n-style org FLOW editor + skills panel** (`95ada50`): React Flow canvas — nodes=brains,
  drag-to-reparent (→ move), inspector edit/promote/delete, route preview; the Lhassa admin node
  is anchored (no source handle, not draggable, can't be moved/deleted). SkillsPanel drives the
  skills API.
- **CLI redesign** (`05296d7`): `_boot.quiet_logging()` kills loguru's default DEBUG sink (no more
  pipecat/Tools-registered/gateway-DEBUG spam in the REPL) before the heavy imports; `theme.py` is
  the **Lhasa palette** — temple GOLD, monk-robe RED (sparing), snow WHITE, sage GREEN, stone DIM;
  styled prompt `lhassa ›`, cost footer, `/help`. Override via `LHASSA_CLI_LOG_LEVEL`.
- **Dashboard reskin to the Lhasa palette** (`a0bb2d6`): warm-gold elegant dark UI (same hexes as
  the CLI), Inter + JetBrains Mono, tracked labels, dual-rail nav with a gold indicator, the org
  admin node in gold+red; a Tailwind `@theme` remap folds all panels into one warm family (no teal).
- **Settings service + flow templates** (`b4d960a`): `settings.py` (an EDITABLE allowlist; `set`
  writes `~/.lhassa/settings.env` — loaded after `.env` with override, secrets in `.env` untouched —
  and applies to the live Config; `effective()` masks secrets + exposes options/restart hints) +
  `org/templates.py` (use-case presets built UNDER Lhassa: code-review, bug-investigation,
  refactor-migration, incident-response, and a multi-level **software-factory** delivery→{frontend,
  backend,qa,devops}). `lhassa config`, `lhassa org templates|apply`, `GET/PATCH /api/config`,
  `GET/POST /api/org/templates…`.
- **Settings panel + templates picker** (`9218ba5`): a Settings section (per-type controls, masked
  secrets, restart-hint pills, red Budget&Safety group) + a Templates gallery (apply → adopt chart,
  Software Factory highlighted), in the Lhasa design.

**Live:** the dashboard (http://127.0.0.1:7766, served by `lhassa-app.service`) now has Brains
(flow/tree/templates) · Skills · Settings · Reports · Fleet · Activity, in the elegant Lhasa
identity. A global `~/.local/bin/lhassa` launcher was added so the CLI runs from anywhere. Live
smokes: edit a setting → settings.env + live apply; apply software-factory → 5 brains under the
Lhassa admin; create+assign a skill; the REPL is clean and colored.

## 2026-06-30 — Capability batch: fallback chain, capabilities, Docker, org management, Warp dashboard, installer

A large autonomous batch turning Lhassa into a more capable, portable, always-on tool. All
committed, gates green (full suite grew 745 → 805), no AI attribution.
- **Provider fallback CHAIN** (`f04ab2a`): `ClaudeCodeProvider` (`claude --print --output-format
  json`) + `CodexProvider` (`codex exec --json …`), registered via `register_provider`; verified
  with live calls. `notify/telegram_inbound._fallback_chain` walks `config.fallback_providers`
  (`claude,codex,default`), skipping the primary's kind and uninstalled CLIs — so chat is
  `opencode → claude → codex → cloud`, keeping the owner on subscriptions before paying cloud.
  This fixed the Telegram bot answering "I can't reach my model" (opencode boots a runtime per
  call and flakes under the long-running poller — earlier fix `b36de30` added the cloud fallback).
- **`lhassa capabilities` / `doctor`** (`6917f25`): introspects every keystone registry +
  gated config + loaded plugins (LLM-free, never-raises) — surfaces what's wired and on/off.
- **Docker** (`fb80a22`): multi-stage image (node builds dashboard → python:3.13 runtime,
  non-root, `HOME=/data` volume, port 7766, healthcheck), `docker-compose.yml` (api/heartbeat/
  telegram roles via `LHASSA_RUN`), `doc/deployment.md`. Build + run verified (API 200, beat $0).
- **Brain/org MANAGEMENT** (`5d3a6ba`): `BrainNode` gains `skills` + `limits`; `org/management.py`
  add/update/remove/`set_parent` (who commands whom) / `set_root` (who commands everyone) /
  set_skills/set_limits/`describe`; `lhassa org` add/move/promote/set-skills/set-limits/show/skills;
  `/api/org/topology|skills|route|brains[/{id}/move|promote]`. Live API smoke passed (create →
  re-parent → promote → route preview). The owner now creates/manages brains + hierarchy by CLI + API.
- **Dashboard redesign** (`b6b9be6`): Warp/Oz-inspired dark theme (design tokens, teal accent,
  mono for IDs/metrics, sidebar of "blocks") + `BrainsPanel`/`OrgTree` — visualize the hierarchy
  and drive the org API (create/edit/move/promote/delete + route preview). Existing panels restyled.
- **One-command installer** (`5ed00bc`): `scripts/install.sh` (preflight → uv sync + dashboard
  build → docker build+smoke → install systemd USER services → `loginctl enable-linger` for boot),
  `uninstall.sh`, `scripts/systemd/lhassa-app.service` (no-voice API+dashboard+scheduler via
  `docker/run_api.py`), `doc/install.md`.

**Now running (background + on boot):** ran `install.sh --no-docker` — `lhassa-app.service`
(dashboard+API+scheduler on 127.0.0.1:7766), `lhassa-heartbeat.timer`, and
`lhassa-telegram.service` are **enabled + active**, lingering on, so they start at boot before
login. Verified live: `GET /` → 200 (title "Lhassa"), `/api/org/topology` → 200.

**Telegram (live):** the bot answers reliably via the fallback chain; name/description/commands
set in English; the inbound poller runs as the systemd service.

## 2026-06-30 — Phase 7 slice 2 (departments think) + heartbeat wired to opencode

**Ops:** the heartbeat now thinks via **opencode** — set in `~/Code/lhassa/.env`
(`LHASSA_HEARTBEAT_PROVIDER=opencode`, `LHASSA_OPENCODE_BIN=/usr/bin/opencode` — absolute
because the systemd user PATH is minimal, `LHASSA_OPENCODE_MODEL=opencode/deepseek-v4-flash-free`).
No systemd edit needed: each beat is a fresh process that re-reads `.env`. `.env` is
gitignored (secrets stay local). The routine path resolves to `PseudonymizingProvider(
OpenCodeProvider)` — opencode with PII scrubbing (opencode is cloud), **$0** on the free tier.
A `scripts/systemd/lhassa-telegram.service` user unit was added for the inbound poller
(credential-gated; install/enable when the owner sets a bot token).

**Phase 7 slice 2** (departments actually think; the general routes among many):
- `org/executor.py` `brain_executor()` runs a department task through the REAL one-turn
  brain path (identity + layered memory + router routine provider + `provider.complete`),
  the department's identity in the system prompt, logs a `despertar_por='org'` episode,
  degrades to a structured **$0** result on `ProviderNotConfigured`/budget (never raises —
  a failure becomes a `TASK_RESULT`, not a crash). The live model call is **gated behind
  `LHASSA_ORG_LIVE`** (default off → offline-deterministic) so slice-1 tests stay offline —
  the established turn-on-with-creds convention (subagent's call; honors the don't-break-
  tests/offline constraints).
- `org/coordinator.py`: `DepartmentBrain` defaults to `brain_executor` (still injectable);
  `GeneralBrain.assign_auto()` routes then assigns (the general routes, never executes).
- `org/routing.py`: deterministic table-driven registry (optimizacion/documentacion/
  finanzas) + `route()` with a documented fallback — **no LLM**. `org/__main__.py` offline
  two-brain demo.

**Verified:** 617 tests, ruff clean, `python -m lhassa.org` runs offline ($0). **Live smoke
(`LHASSA_ORG_LIVE=1`):** the general routed a goal → the `optimizacion` department **thought
for real via opencode** (free tier, **$0**), returned a genuine answer + a typed `TASK_RESULT`
correlated by `corr_id`. Two brains thinking end to end.

**Next Phase-7 candidates:** a second live department in the demo; cross-department commons
policy enforcement during a think; `STATUS_REQUEST`/`STATUS_REPORT` flow; an API/dashboard
view of the org message queue. **Backlog:** point a real department at `LHASSA_ORG_LIVE=1`
in production once desired.

## 2026-06-30 — Phase 6 cont. (heartbeat via opencode, voice, Telegram) + Phase 7 START

**Decision:** the owner has **no Ollama/Zen** but has the **opencode CLI** → the routine
heartbeat thinks through **opencode** (their existing creds), default the free tier
`opencode/deepseek-v4-flash-free`. Also: **start Phase 7 now** — the owner explicitly
authorized it (overriding the brief's "not yet"); built as the minimal two-brain skeleton.
4 slices, disjoint ownership, gates green (config surface added up front):
- **opencode heartbeat** `OpenCodeProvider` (`opencode run --pure --format json -m …`;
  router routine→opencode). **`--pure` matters:** a beat dropped from **>120s → ~8s**
  (without it opencode hangs on plugin/MCP startup). **Live-verified schema** (replaced the
  `[SUPUESTO]`): assistant text at `part.text` on a `text` event; totals nested under
  `step_finish` `part.tokens`/`part.cost` (the first parser read top-level only → fixed,
  + a regression test). Live smoke: free-tier round-trip → `pong`, tokens 11153/3, **$0**.
- **voice** `voice/brain_bridge.py` — spoken turns answered by the brain (identity +
  layered memory + episode `voz`), pipecat frames emitted via a lazy processor; swapped in
  only when `voice_enabled`. Default path unchanged. (Live audio needs Deepgram/Cartesia.)
- **Telegram** `notify/dispatch.py` (fan-out) + `notify/telegram_inbound.py` (getUpdates
  poller; honors only the configured chat; answers via `cli.agent.run_once`; `python -m`
  entrypoint). Credential-gated; no token → no network. (Live needs a bot token.)
- **Phase 7 `org/`** (foundation, golden rules enforced + smoke-verified on real SQLite):
  typed messages (`MessageKind` closed; free prose rejected) · read-only **commons** (only
  `general` writes; one shared row) · **RBAC** (deny-by-default, cross-department isolation)
  · **GeneralBrain coordinates, never executes** (no handle/executor) while DepartmentBrain
  executes via an injectable executor and logs a `despertar_por='org'` episode (audit plane)
  · **multi-person approval** (proposer≠approver for high-risk → `SelfApprovalError`) ·
  **two brains, never six**. `org_messages`/`org_commons` tables added to `db.py`.

**602 tests** (529→+73), ruff clean, imports/help ok, dashboard builds.

**Turn-on-with-creds / continuous:** point the systemd heartbeat at opencode by setting
`LHASSA_HEARTBEAT_PROVIDER=opencode` (or leave `auto` — opencode is auto-preferred when no
`local_model_url`); voice needs Deepgram/Cartesia keys + `LHASSA_VOICE_ENABLED=1`; Telegram
needs a bot token + chat id (+ `LHASSA_TELEGRAM_POLL=1` for inbound). **Backlog / `[SUPUESTO]`:**
wire the org default executor to the live one-turn brain path (`core.step`) — the injectable
`executor` is the seam; opencode `--pure` means org/MCP tools aren't available to a beat
(fine for routine thinking; delegated code work still uses the harnesses); the live pipecat
frame wiring of the voice bridge and the Telegram `getUpdates` shape are unverified without
creds. **Casa/MCP** (home integrations) remains the open Phase-6 continuous item.

## 2026-06-29 — Phase 6 (Madurez) — first push: local heartbeat, memory layer 4, rich interfaces

Phase 6 is **continuous** (no single DoD). This first push lands the autonomous,
offline-verifiable pieces; voice + home/casa MCP are noted as the remaining
continuous items (they need audio creds / per-home servers to smoke honestly).
Built (3 slices, gates green, file ownership disjoint — config surface added up front):
- **6.A** `llm/provider.py` `LocalModelProvider` (OpenAI-compatible local endpoint,
  no API key, **cost $0**, `zero_retention`+`is_local`; unreachable→`ProviderNotConfigured`
  → existing degradation path) + `privacy.py` (deterministic stdlib PII scrubber,
  reversible placeholders, never raises) + `PseudonymizingProvider` (wraps a remote
  provider: scrub before send, de-scrub after) + `router` routine→local preference
  (`heartbeat_provider` auto/local/cloud), hard→cloud, cloud path pseudonymized unless
  the provider is local (**the hook relaxes for local** — §3-Fase 6).
- **6.B** `memory/embeddings.py` (pluggable `Embedder`: `HashingEmbedder` deterministic
  offline default + optional `OpenAICompatEmbedder` degrading to hashing; no numpy) +
  `db.py` `fact_embeddings` table/helpers + `memory/load.py` **layer 4** (semantic
  `Context.semantic_facts`, lowest `PRIORITY` so it truncates first and never touches the
  identity core; `load(query=,config=)` + explicit `index_facts()`). **Off by default** —
  existing `load()` is byte-for-byte unchanged.
- **6.C** `cli/` power subcommands (`tools/approvals/approve/deny/report/memory/fleet`,
  each `--json`) preserving the default `lhassa "prompt"`/piped/REPL via a fallback
  `TyperGroup` + `notify/telegram.py` (credential-gated, no token→no network, never
  raises) + `/api/reports`·`/api/projections`·`/api/fleet` + dashboard Reports/Fleet panels.

**DoD-style live smoke** (offline tests + a smoke against a **real in-process
OpenAI-compatible socket server**): the routine path thinks on the **local** model at
**$0** with PII **un-scrubbed** (data never leaves the box — hook relaxed); the hard/cloud
path is wrapped in `PseudonymizingProvider` and PII is scrubbed→de-scrubbed round-trip;
memory **layer 4 ON** recovers an overheating fact that shares **no tag** with the query
("why is my computer warm and slow") and ranks it first, while **layer 4 OFF** yields no
semantic layer. **529 tests** (460→+69), ruff clean, `lhassa --help` lists 7 subcommands,
dashboard builds.

**Deferred / continuous (Phase 6 is ongoing):** **voice** (skeleton `voice/pipeline.py`
exists; end-to-end wiring needs Deepgram/Cartesia creds + audio to smoke) and **home/casa
MCP** integrations (per-home servers) are the remaining items. **Turn-on-with-creds:**
the local heartbeat needs a running local endpoint (`LHASSA_LOCAL_MODEL_URL`, e.g. Ollama);
layer 4 needs `LHASSA_MEMORY_LAYER4=1` (+ optional `LHASSA_EMBEDDING_MODEL`); Telegram needs
a bot token+chat id. **Backlog / `[SUPUESTO]`:** the cloud embedder + real-endpoint JSON
shapes (Ollama `usage`/`choices`, OpenAI `/embeddings`) are assumed, not yet live-verified;
the heartbeat is not yet pointed at a local model in the systemd unit (config default `auto`
with an empty URL = unchanged behavior); a free-text CLI prompt whose first word is exactly
a subcommand name routes to that subcommand unless quoted.

## 2026-06-29 — Phase 5 complete (self-improvement 2b in quarantine)

The brain can write its own tools and graduate them only through objective gates,
caged. Built (2 slices, gates green):
- **5.A** `security/quarantine.py` (`run_in_quarantine`: strongest-available sandbox
  via `unshare`/`firejail`/`bwrap` for **no-network**, a from-scratch **scrubbed env**
  so no secret leaks, temp cwd, `killpg` on timeout; `str`→`bash -c`, `list`→argv) +
  `agents/gates.py` — the **four gates of 2b**, deny-by-default, stop-at-first-failure:
  1 **Segura** (runs caged) · 2 **Correcta** (a command/tests verifier exits 0) ·
  3 **Mejor** (a `MetricVerifier` beats a concrete baseline; optimización is
  lower-is-better) · 4 **Retiro** (continuous: `should_retire` demotes a graduated
  tool that fails in production or goes unused). Gates **reuse** the Phase-3 verifiers
  and **never call an LLM**.
- **5.B** `agents/specialists/` (**skills, NOT fine-tuning**: `Specialist` = base model
  + instructions + tools + `gate_spec`, no model id anywhere; registry keyed by domain,
  first = `optimizacion`; `build_specialist_prompt` = persona identity + domain skill) +
  `agents/versioning.py` (**local-git** tool versioning: `create_candidate`→branch,
  **`approve` = merge** routed through the approval queue as a `tool_graduation`,
  `rollback` = `git revert`; `_git` never raises and degrades gracefully when git is
  missing; GitHub-remote PR/merge are **token-gated no-op hooks**, never reached
  offline) + `/api/tools` + graduate endpoint + `QuarantinePanel` + `tools_repo_dir`.

**DoD met** (offline tests + a live smoke with real git/SQLite/quarantine): the brain
writes a tool → candidate on its own branch → a good candidate **graduates** (segura+
correcta+mejor) while a worse one is **rejected at gate 3 with a reason** and an
incorrect one **at gate 2** (gate 3 never runs); graduation is an approval-queue
**merge** into `main` (a resolved `aprobada` `tool_graduation` row, content on `main`);
a graduated tool failing 3/4 in production (or unused) **retires** to cuarentena; and a
**git revert** rolls it back. "Mejor" is proven by a number (42 < 100), not asserted.
460 tests, ruff clean, dashboard builds.

**Deferred (token-gated, non-blocking):** the GitHub-remote path (push branch + open/
merge a real PR) is wired as a no-op hook behind `Config.github_token` — turns on with
a new token (old one revoked). **Backlog:** more specialist domains are just new
registry rows; the retirement supervisor's "went unused" grace window is `[SUPUESTO]`.

## 2026-06-29 — Phase 4 complete (hands: MCP + harnesses)

Decisions: 2nd typed task = **`tracking_postulaciones`**; the owner has all three
harnesses (opencode/Claude Code/Codex). Built (2 slices, gates green):
- **4.A** `harness/adapter.py` (`HarnessAdapter` Protocol + `RunEvent` + stream-json/
  plain parse; OpenCode/ClaudeCode/Codex adapters auto-detected via `which`; argv
  flags marked `[SUPUESTO]`) + `harness/runner.py` (`launch` fire-and-forget records a
  jobs row + PID, streams events, accrues cost; `supervise` reaps each tick) + router
  `pick_harness` (cheapest available: free<metered<subscription; `LHASSA_HARNESS`
  override) + `step` delegate branch (launch + episode notes harness/job/PID, no block).
- **4.B** `tools/mcp_client.py` (`mcp==1.28.1`; loads `~/.config/lhassa/mcp.json`,
  registers namespaced `mcp__<server>__<tool>` tools onto the LLM; defensive) +
  `postulaciones` table + `tracking_postulaciones` deterministic action (offline core,
  optional guarded MCP top-up) + `track_postulaciones` registry tool.

**DoD met** (offline + live runner smoke): an MCP tool is invocable; the brain delegates
to any of the three harnesses and supervises PID/cost live (a real subprocess launched,
streamed, reaped to `done` with cost $0.0021); adding an integration is config, not code.
421 tests, ruff clean.

**Backlog:** cross-restart job supervision by PID-from-disk (the reaper uses the
in-process registry — a launched-before-restart job isn't reaped after a brain restart);
validate the adapter argv `[SUPUESTO]` flags with a live harness smoke (spends — owner's
go); a real opencode/claude/codex delegation smoke is available on request.

## 2026-06-29 — Phase 3 complete (the arbiter)

Built (2 slices, gates green):
- **3.A** `agents/verify.py` — objective verifiers (`command/tests/lint/health/regex/
  metric`), **never call an LLM** (no provider/gateway import; asserted in tests) +
  `make_verifier`/`verify` dispatcher; wired into `core/step.py`'s judgment branch via
  `router.cascade(verify=…)` (failed verification escalates cheap→smart once; the
  episode records `verificado: passed/failed`). Reused by Phase-5 gates.
- **3.B** `security/approval.py` (HITL-**per-flow** `needs_approval`: per-action risk
  + origin trust + a high-risk always-approve set; parks pending in the `approvals`
  table) + `security/autonomy.py` (a streak knob: a HUMANO action approved N× becomes
  automatic; a denial resets it; high-risk never auto-promotes; **never promotes an
  `Origin`'s trust** — contagion-only) + `local_agent` approval gate + `/api/approvals`
  + `ApprovalQueuePanel`.

**DoD met** (offline + live smoke): a `mundo`/sensitive action waits for the owner's
OK; a repeated `humano` action becomes automatic (threshold 3); move-money always
requires approval even with a streak; a denial resets the streak; the verifier never
calls an LLM. 369 tests, ruff clean, dashboard builds.

Note: 2 pre-existing eslint warnings in `App.jsx handleEvent` (untouched code) — not
introduced here; `npm run build` is clean.

## 2026-06-29 — Phase 2 complete (economy + router)

Decision: the user has the **opencode CLI but no Zen** → the first `ModelProvider`
wraps the existing `.env` key via the gateway (`GatewayProvider`); `OpenCodeZenProvider`
is a ready stub (swap-in when Zen creds exist, per invariant #3). opencode-as-harness
is Phase 4.

Built (3 slices, gates green):
- **2.A** `ModelProvider` Protocol + `GatewayProvider`/`OpenCodeZenProvider`,
  `llm/router.py` (two axes, `pick_provider`/`pick_model`, cascade cheap→verify→
  escalate, `$think`/`$justdo`), and `core/step.py`'s judgment branch wired (load
  context → router → provider; `BudgetExceeded`/`ProviderNotConfigured` degrade to a
  deferred episode, no crash).
- **2.B** gateway **kill-switch** (`KillSwitchActive` ⊂ `BudgetExceeded`, refuses
  before any call) + persisted runtime budget override + `llm/ccusage.py` (defensive
  `ccusage --json` parser/runner) + `/api/llm/limits` & `/api/llm/ccusage` + dashboard
  controls (budget setter, kill-switch toggle, cross-harness breakdown).
- **2.C** `memory/consolidate.py` **semantic consolidation** (cheap model via the
  provider/gateway: cluster-summarize + detect contradictions; complements the
  mechanical LRU; only touches the chica; threshold-driven).

**DoD met:** routine routes to the cheap model; the ledger shows the split (real
per-model pricing, tested); **crossing the cap stops** — verified live (kill-switch
raises, provider called 0×). 321 tests, ruff clean, dashboard builds.

**[SUPUESTO] to validate later:** ccusage JSON field names (marked in code); and the
live "a day of heartbeats costs cents" smoke needs real cheap-model calls (deferred —
won't spend without the owner's go). **Backlog:** swap to opencode Zen when available.

## 2026-06-29 — Phase 1 complete (memory)

Built additively (old `memory/store.py` untouched), 2 slices, behind gates:
- **1.A** `core/db.py` facts table (Apéndice A, append-only, instance_id) +
  `memory/facts.py` (`Fact` dato/directiva, contradiction = newest+confianza) +
  `memory/load.py` (layered `load(token_budget)`: pinned+directivas always →
  recency → active-tags; truncate by priority; deterministic token estimate; **no
  embeddings / layer-4**) + `memory/seed.py` (Apéndice B seed facts).
- **1.B** `memory/chica.py` (persistent working cache; **threshold-LRU
  `consolidate`** that never evicts pinned/directivas; `regenerate` from the
  grande) + `memory/backup.py` (WAL-safe online `backup`/`restore`/`verify`).

**DoD met** (offline tests + live SQLite smoke): recover a fact from "days ago" by
tag · chica fills → consolidates without overflow (23→5, pinned kept) · kill the
chica → regenerates from the grande · backup → verify True (corrupt → False) →
restore round-trips. 272 tests, ruff clean.

**Deferred to Phase 2 (per the brief):** semantic memory consolidation (summarize
similar items / detect contradictions via a cheap LLM) — Phase 1 is mechanical LRU
only. **Backlog:** reconcile the old `memory/store.py` (voice session facts/
sessions) with the new typed-facts model.

## 2026-06-29 — Phase 0 complete (the spine)

Built additively over the existing skeleton, 5 vertical slices, all behind gates
(no AI attribution, pytest green, ruff clean):
- **0.1** `core/db.py` (SQLite WAL "grande", append-only, `instance_id` on every
  table/query) + `core/episode.py` (structured Episode from the first commit).
- **0.2** `core/step.py` + `core/watchlist.py` + `core/actions.py` — the
  deterministic do-and-sleep loop (zero-token watchlist → fixed action → episode);
  the `else` branch is the Phase-2 router seam.
- **0.4** `security/`: signed `Task`, `TrustLevel`/`Origin` (degrade=min, never
  promote), Ed25519 signing, deny-by-default allowlist (= §2.6) with HITL-per-flow.
- **0.3** `/checkin` + `/report` + `remote/local_agent.py` — check-in-not-push with
  desired_state reconciliation; the allowlist gate runs on the machine.
- **0.5** `core/heartbeat.py` + systemd `lhassa-heartbeat.{service,timer}` +
  parametrized identity (`Identity`/`load_identity`, org hook #2; directives loaded
  as identity; `PERSONA_BLOCK` kept stable) + the end-to-end DoD test.

**DoD met** (offline tests + a live in-process smoke with real Ed25519/SQLite/git):
timer pulses → check-in → reconcile → signed `sync_repo` → allowlist gate → action
runs → report → two episodes explain *why* → state persists across a DB reopen →
every row carries `instance_id`. 229 tests, ruff clean.

**Living-doc correction (§4/§7):** the brief lists `OpenCodeAdapter`/`ModelProvider`
under Phase 0, but Phase 0's loop is deterministic-only and never exercises them —
so they're **deferred to Phase 2 (ModelProvider, the router/think path) and Phase 4
(HarnessAdapter)**, where they're actually used. The three org hooks are in place.

Open Phase-0 polish (backlog, not blocking): `desired_state` should map repo names
→ real paths (the smoke's `sync_repo` failed gracefully because the objetivo wasn't
resolved to a path); single-writer staging buffer (§2.7) is a later refinement.

## 2026-06-29 — adopting the implementation brief

- **Brief adopted** as the plan: Lhassa becomes a persistent "brain" (JARVIS) with
  its own identity that orchestrates models/harnesses behind interfaces — "build
  the brain, rent the body." Execution model: *do-and-sleep* (`state + step()`
  woken by a systemd heartbeat / API). See `doc/lhassa-brief.md`.
- **Existing code is the skeleton — extend, don't rewrite.** What's already built
  maps onto the brief: `llm/gateway.py` (Phase 2 choke point), `run_ai_cli` +
  `claude_code` (→ HarnessAdapter, Phase 4), `tools/privileged.py` allowlist (→
  `security/allowlist.py` + Ed25519, Phase 0/3), `health`/`scheduler`/`state`/
  `api`/`cli`/`persona` (Phase 0/6). Net-new and FIRST: `core/step.py` +
  `watchlist.py` + structured episode + `instance_id` + check-in (Phase 0).
- **The old `doc/roadmap.md` is superseded** by the brief and re-sequenced; its
  A1 (tools-aware gateway) shipped as commit 290eac9 (a valid Phase-2 piece). The
  rest is re-baselined onto the brief's Phase 0→7.

### Decisions (this session)
1. **Host:** develop **locally first** (the brain runs on the laptop) with all
   seams ready (instance_id, check-in, systemd) to migrate to a VPS without
   surgery. VPS later.
2. **Heartbeat ModelProvider:** start with **opencode Zen** (free/cheap), with
   pseudonymisation as a mandatory hook while the heartbeat uses free models;
   migrate to a local ModelProvider later (Phase 6).
3. **Repo folder:** renamed `~/Code/potts` → `~/Code/lhassa` (the package was
   already `lhassa`).
4. **Governance/pace:** I drive **Phase 0 autonomously** (subagents + gates +
   commits), checking in with the owner at each phase's **DoD** before advancing.

### Open / pending (asked, to confirm when their phase arrives)
- Harness subscriptions available (Claude Code / Codex / opencode).
- Machine network: WireGuard vs Tailscale.
- Telegram notifications (needs a bot token).
- GitHub account/org + a **new** token (old one revoked) for tool versioning (Phase 5).
- Adopt **ccusage** for cross-harness accounting.
- Seed facts (Appendix B) — confirm they're the owner's; `instance_id = inst_personal`.
- Second typed task (Phase 4): calendar_event / agendar_fechas / generar_documentacion /
  tracking_postulaciones.
- `[SUPUESTO]` items to validate with live smoke runs (Zen training on free data;
  Managed Agent Harness as sandbox; heartbeat provider ToS/rate-limits).
