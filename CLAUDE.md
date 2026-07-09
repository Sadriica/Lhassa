# Lhassa — agent / developer context

## What this is

Lhassa is a persistent personal **brain** (a JARVIS): an identity-first core that
**orchestrates** models and tools rather than being one. The execution model is
**do-and-sleep** — state on disk + a `step()` function that advances it one tick,
woken by a systemd **heartbeat** timer or on demand via the API. The durable thing
is *who Lhassa is*; models, harnesses and scripts are interchangeable muscle:
**build the brain, rent the body.** It is local-first, designed to be **fully
elastic and highly modular** (every swap point behind an interface + registry;
capabilities drop in without editing core), and scales from **one brain to an org
of 1..N brains**.

> This is **not** "a voice assistant" anymore. Voice is one opt-in surface among
> CLI / Telegram / dashboard. The brain is the product.

The spec is `doc/lhassa-brief.md` (Spanish, living doc). The phase-by-phase
decisions log is `doc/decisions.md` — read it as the source of truth for what was
built. The layer rules live in `doc/architecture-layers.md` and a current overview
in `doc/architecture.md`.

## Dev workflow

Tooling is **uv** + **make**:

```bash
make install   # uv sync + dashboard build (first time)
make run       # voice + API + scheduler — uv run python main.py
make dev       # same with LHASSA_DEBUG=1 LHASSA_LOG_LEVEL=DEBUG
make test      # uv run --extra dev python -m pytest
make lint      # uv run --extra dev python -m ruff check .
make dashboard # rebuild the React/Vite dashboard
make check     # validate env, no API calls (scripts/check.py)
```

Run a single test file: `uv run --extra dev python -m pytest tests/test_x.py`.
`tests/test_architecture.py` is the **architecture guard** — a dependency-free
`ast` walk that fails on any new layer-crossing import. If you add a subpackage,
slot it into the right layer in that test; if you add a deliberate edge, add a
precise `ALLOWED_EXCEPTIONS` entry with a reason. Keep the layering clean.

## The layers (and the rule)

Inner layers never import outer shells; dependencies point **down** only.

| Layer | Subpackages | Role |
|---|---|---|
| Leaf infra | `registry.py`, `plugins.py`, `config.py` | Generic machinery; imports no feature code |
| Spine | `core/` | The do-and-sleep loop (`step`, `heartbeat`, `episode`, `db`, `watchlist`, `actions`) |
| Interfaces | `memory/`, `security/`, `llm/`, `settings.py` | Reusable contracts (facts, trust/approval, provider + router, editable settings) |
| Adapters (hands) | `harness/`, `tools/`, `agents/`, `skills/`, `remote/`, `health/` | The rentable body |
| Shells (outer) | `cli/`, `api/`, `voice/`, `notify/`, `org/`, dashboard (JS) | World-facing surfaces; compose everything below |

Encoded forbidden directions: spine/interfaces/adapters ↛ shells; `harness/` ⟂
`llm/` (the two swap axes stay independent — `llm/router.py` *choosing* a harness
is the one allowed bridge); the keystone (`registry.py`/`plugins.py`) imports no
feature module. See `doc/architecture-layers.md`.

## The do-and-sleep loop

- `core/step.py` — one deterministic tick: zero-token **watchlist** check →
  fixed **action** (from `core/actions.py`) → a structured **episode**. Before any
  model is touched, `core/policy.py` (the **REFLEXES** registry, same pattern as
  ACTIONS) inspects the trigger's *content* and returns a `PolicyDecision`:
  `action` (run a fixed action, $0), `skip` (auto-triage, episode logged, $0) or
  `think` with a `context_tier` (`minimal` ≈600tk / `full` ≈2000tk) + `hard` flag.
  Built-in reflexes: clean finished jobs skip, `repo_changed` auto-runs `sync_repo`
  for `LHASSA_AUTO_SYNC_REPOS`, health `warn` just notifies. The judgment branch
  loads tiered memory context (now fed `query` + `active_tags` so the tag/semantic
  layers actually fire) → `llm/router.py` picks a provider/model →
  `provider.complete(...)`. On `ProviderNotConfigured`/`BudgetExceeded` it walks
  `router.fallback_chain()` ($0 subscription CLIs first) before **degrading to a
  deferred episode, never crashing**; deferred tasks are re-queued with backoff by
  `db.sweep_deferred_tasks()` each tick (max `LHASSA_DEFERRED_MAX_RETRIES`, then
  `rechazada`). Spend is a live signal: past `LHASSA_BUDGET_SOFT_PCT` (0.8) the
  router refuses the smart model and prefers $0 providers; `gateway.spend_summary()`
  / the `check_spend` action expose it to the brain itself.
- `core/heartbeat.py` + `scripts/systemd/lhassa-heartbeat.{service,timer}` — each
  beat is a fresh process that re-reads `.env` and runs one `step()`. Routine beats
  are deterministic and cost **$0**. `make install-heartbeat` enables the timer.
- `core/episode.py` — the structured Episode (observability from the first commit;
  every row carries `instance_id`). `core/db.py` is the append-only SQLite
  "grande" (WAL).

## The two swap interfaces

1. **`ModelProvider`** (`llm/provider.py`) — *think directly*, no harness. Protocol
   with `complete()`, `zero_retention`, `is_local`. The `PROVIDERS` registry maps a
   name → factory; built-ins: `default` (`GatewayProvider`, the cost-gateway path),
   `local` (`LocalModelProvider`, OpenAI-compatible local endpoint, $0), `opencode`
   (`OpenCodeProvider`, the `opencode run --pure --format json` CLI heartbeat),
   `claude` / `codex` (subscription CLIs), `zen` (stub). `PseudonymizingProvider`
   wraps a remote provider to scrub PII at the boundary. Add one with
   `@register_provider("x")` — no core edit.
2. **`HarnessAdapter`** (`harness/adapter.py`) — *agentic body*. OpenCode / Claude
   Code / Codex adapters auto-detected via `which`; `harness/runner.py` launches
   fire-and-forget (records a job row + PID, streams events, accrues cost) and
   `supervise` reaps. `llm/router.py:pick_harness` picks the cheapest available
   (free < metered < subscription; `LHASSA_HARNESS` overrides). `harness/` must not
   import `llm/`.

## Registry + plugins (the keystone)

`registry.py` is the generic `Registry[T]` (register as call **or** decorator;
`get`/`get_or_none`/`NotRegistered`; duplicate-protection; `as_mapping` live view).
Every swap point adopts it: providers (`PROVIDERS`), harnesses (`ADAPTERS`),
verifiers (`VERIFIERS`), specialists (`SPECIALISTS`), channels (`CHANNELS`),
commands (`COMMANDS`), actions (`core/actions.ACTIONS`), watchlist senses
(`core/watchlist.register_check` — how `org/inbox_sense.py` plugs in without core
importing org), **seeds** (`seeds.SEEDS` — `@register_seed`, a drop-in preset that
bootstraps an env; `cv` rebuilds the interns department + the CV work-flow, `digest`
schedules the 08:00/21:00 daily digests, `maintenance` schedules `distill_memory` +
`detect_repetition` daily and `review_tools` + `curate_skills` weekly; `lhassa seed
list|apply <name>|apply-all`), org departments.
`plugins.py:load_plugins(config)` imports every module under `plugins_dir` **and**
every `lhassa.plugins` entry point — self-registration with **no core edit**;
idempotent, never raises. To add a provider/channel/command/verifier/specialist/
action, drop a module that calls the matching `register_*` at import time. `lhassa
capabilities` (alias `doctor`) shows everything wired and on/off.

## Memory

- **Grande** (`core/db.py` facts + `memory/facts.py`) — typed facts (`dato` /
  `directiva`), append-only, contradiction = newest + confianza. `memory/load.py`
  layers context by priority (pinned + directivas always → recency → active tags →
  optional **layer 4**), truncating low-priority first.
- **Chica** (`memory/chica.py`) — persistent working cache; threshold-LRU
  `consolidate` (never evicts pinned/directivas); `regenerate` from the grande.
  `memory/backup.py` does WAL-safe online backup/restore/verify.
- **Layer 4** (`memory/embeddings.py` + `memory/load.py`) — semantic facts via a
  pluggable `Embedder` (`HashingEmbedder` offline default, `RemoteEmbedder` for a
  real local endpoint). On by default (`LHASSA_MEMORY_LAYER4`).
- **Episodic recall (layer 5, FTS5)** — `core/db.py` keeps an `episodes_fts`
  FTS5 index over the episode log (triggers + backfill in `init_db`; degrades if
  the SQLite build lacks FTS5). `db.search_episodes` is BM25-ranked,
  hostile-input-safe, and *demotes* (never hides) automation episodes
  (`monitor`/`task:*`). Surfaced as `GET /api/episodes/search`, the Telegram
  `/recall` command, and the LOWEST-priority context layer in `memory/load.py`
  (fires only when a `query` is given; truncates before everything else) — the
  brain recalls its own past experiences when it thinks, at $0.
- `memory/consolidate.py` — semantic consolidation via the cheap model (only the
  chica). `memory/store.py`/`session.py` is the legacy voice-session store.

## The $0 intelligence layer (think only when it's worth it)

The philosophy "thinking is the exception" made into machinery — every piece is
deterministic, $0, degrade-to-normal-think on any failure:

- **Answer cache + intent router.** `notify/intent.py:classify()` sorts each chat
  message into `command`/`live_data`/`cacheable`/`judgment` (pure regex, no model;
  `live_data` — news/prices/`hoy`/weather — is checked FIRST so it is never
  cached). `memory/answer_cache.py` embeds a cacheable question via the offline
  `HashingEmbedder`, cosine-matches past answers (`answer_cache` table), and on a
  hit above `LHASSA_ANSWER_CACHE_THRESHOLD` (0.90, TTL 7d) serves the stored answer
  from disk — provider never called, episode logged `cache-hit`, `costo_usd=0`.
  Hooked into `telegram_inbound._brain_reply` BEFORE spending; a miss stores the
  answer after the think. `LHASSA_ANSWER_CACHE*` knobs, settings-editable.
- **Knowledge graph.** `memory/graph.py` is a $0 read-model over facts + episodes:
  `what_do_i_know(x)` (facts + FTS5-recalled episodes mentioning an entity, its
  co-occurring terms, its time span), `entities()`, `timeline()`. Entity
  extraction is tags-first + capitalized/quoted terms (documented as heuristic,
  not NLP). `GET /api/memory/graph|entities`, `lhassa memory graph|entities`.
- **The consciousness** (`agents/repetition.py` + `actionpack/_selfimprove.py`).
  Beyond the cost detector: `detect_recurring` finds patterns repeated OFTEN
  (any cost) and reads a temporal cadence (regular gaps → "fires weekly");
  `recommend_intervention` picks the CHEAPEST fix deterministically — chain →
  `script`, cadence → `schedule`, re-derived stable answer → `memory_fact`,
  already-cached → nothing, costly judgment with no cheaper path → `tool`. Each
  parks one approval naming the tokens it would save (`describe()` renders
  `mint_memory`/`mint_schedule`/`seed_cache`); `denied_patterns` respects prior
  denials across all proposal tipos.
- **Feedback signal** (`core/feedback.py`). Infers whether an answer landed from
  behavior already in the log — re-ask soon after (negative), a correction ("no,
  en realidad…", negative), an action taken on it (positive), silence-after
  (weak positive). `derive_signals` / `summary` (net "am I landing?" score) at
  `GET /api/analytics/feedback`. Inference, not truth (episodes don't store the
  user's raw text) — documented as such; exposes the signal, doesn't yet feed
  the autonomy knob.

## The árbitro (verify) + security

- **Verifiers** (`agents/verify.py`) are objective (`command/tests/lint/health/
  regex/metric`) and **never call an LLM** (no provider import; asserted in tests).
  Wired into `step`'s judgment branch via `router.cascade(verify=…)` — failed
  verification escalates cheap→smart once.
- **Quarantine** (`security/quarantine.py`) — strongest-available sandbox
  (`unshare`/`firejail`/`bwrap`), no-network, scrubbed env, `killpg` on timeout.
  Self-written tools graduate only through the four gates (`agents/gates.py`:
  segura → correcta → mejor → retiro), deny-by-default.
- **Self-improvement loop** (Etapa 4) — `agents/repetition.py` ($0 SQL over
  episodes) finds repeated costly thinks and the `detect_repetition` action parks a
  `mint_action` approval + notifies (deduped via `watch_state`). `agents/author.py`
  has the routine model write a candidate tool+test → `versioning.create_candidate`
  → `gates.promote` (quarantine + verifiers) → a `tool_graduation` approval; CLI
  `lhassa tools propose <name> --desc "..."`. `POST /api/tools/{n}/graduate`
  refuses (409) tools that didn't pass the gates. `review_tools` applies
  `should_retire`/`retire`. Authored code never executes outside quarantine before
  graduating.
- **PTC — Programmatic Tool Calling** (`agents/ptc.py` + the `run_script` action) —
  one LLM turn writes a Python script that orchestrates N deterministic ACTIONS:
  `generate_stub()` renders `lhassa_actions.py` from the ACTIONS registry; the
  script runs INSIDE the quarantine sandbox and speaks JSON-lines over a Unix
  socket (AF_UNIX survives the no-network namespace); the PARENT dispatches every
  RPC through the signed-Task + allowlist seam — unknown/disallowed/needs-approval
  actions are refused, never run. Caps: 300s, 50 calls, 50KB/10KB output.
  `run_script` itself is allowlisted with `requires_approval=True` (run_command-class
  risk); the whole thing never raises — every failure returns a dict.
- **Allowlist** (`security/allowlist.py`) — deny-by-default action allowlist;
  signed `Task` + `TrustLevel`/`Origin` (degrade = min, never promote) via Ed25519
  (`security/signing.py`).
- **Approval** (`security/approval.py`) — HITL-per-flow `needs_approval`; pending
  parked in the `approvals` table. `security/autonomy.py` is the streak knob (an
  action approved N× becomes automatic; a denial resets; high-risk never
  auto-promotes; an `Origin`'s trust is never promoted).

## Notify channels + commands (shell)

`notify/channels.py` — `Channel` protocol + `CHANNELS` registry
(`@register_channel`); `TelegramChannel` / `DesktopChannel` / `LogChannel`.
`notify/dispatch.py:notify()` fans out over `active_channels(config)` (from
`LHASSA_NOTIFY_CHANNELS`). `notify/commands.py` — `COMMANDS` registry
(`@register_command`): `/start`, `/help` (a grouped catalog with per-command
one-liners; unregistered-in-DESCRIPTIONS commands still surface), `/status`,
`/approvals`, `/approve <id>`,
`/deny <id>` (resolve via `security.approval`), `/recall <text>` (FTS5 search over
the episode log), `/report [days]` (spend), `/memory <query>` (facts search),
`/capabilities`, `/tools`, `/skills`, `/org`, `/fleet`, `/version` (the CLI-parity
batches — the standing
principle is that Telegram grows toward the CLI's capabilities, LLM-free handlers
over the same underlying seams), `/code <task>` (parks a `code_run` approval with
an inline Approve/Deny keyboard; one tap launches the run through the dashboard's
own `code_routes._stream_run` seam and reports start + completion — remote write
access NEVER runs without the owner's tap), `/watch <pattern> -> <accion>` /
`/watches` / `/unwatch <i>` (author watch rules from chat), `/goal <text>
[--every …]` (mints a recurring judgment task) — all LLM-free. The chat think is
TOOL-LESS and its instruction says so (it must never claim to search); live-data
questions go through the **research bridge**: the model emits `[[research:
<query>]]`, `telegram_inbound._apply_research_marker` mints a `research_reply`
task (same HUMANO/telegram origin as `/goal`; the ack line is appended only after
the mint succeeds), and the next heartbeat runs `actionpack/_research.py` — one
web search (Brave → DuckDuckGo via `tools/search.py`) delivered back over
Telegram with sources, $0, and the follow-up ALWAYS arrives (a failed search
degrades to an honest message) — and it arrives in SECONDS: a no-approval
chat-minted task runs immediately via `core/dispatch.py:run_task_now` (the
heartbeat's own runner, early; a failure leaves the task pending so the beat
retries). The chat REMEMBERS: a rolling per-chat history
(`LHASSA_TELEGRAM_HISTORY_TURNS` pairs, TTL'd, process-memory only) threads into
every think, and every `LHASSA_CHAT_MEMORY_NUDGE_TURNS` turns the model may flag
ONE durable owner fact with `[[remember: …]]` → persisted to the grande as a
`dato` (procedencia `telegram-chat`, substring-deduped, marker stripped).
Reminders ride the ONE clock: `schedule_reminder` mints a `send_reminder` task
(tasks table + heartbeat; `lhassa/scheduler.py` is a deprecation shim, a one-shot
migration moved legacy rows). `/remind <what> <when>` is the Telegram door — a
deterministic time-phrase parser (`commands.parse_remind`: "a las 5pm", "at
17:00", "in 45 min", "mañana 9am"; system-local clock, past-today rolls to
tomorrow), and a natural `acuérdame/recuérdame/remind me` message with a
parseable time short-circuits to it before any model (time-less phrasings fall
through to the remember/fact lanes). NO brain executes a reminder — the
`personal` briefing flows only read pending ones as agenda data. `lhassa
onboard` (CLI) seeds the owner's founding facts; `lhassa memory add` writes
one directly. Every surface that
lists approvals (the `/approvals` command, the dashboard queue/Inbox, the digest)
renders through `security/approval.py:describe(row)` → `{title, detail}` — one
deterministic place that knows each payload shape, so the owner sees WHAT they are
approving, never a bare id. Inbound Telegram
(`notify/telegram_inbound.py`, getUpdates poller, honors only the configured chat)
also handles `callback_query` (inline Approve/Deny buttons on watcher alerts, via
`reply_markup`/`parse_mode` support in `notify/telegram.py`), then dispatches a
slash command before falling back to a tool-less brain think over the routine
fallback chain (`opencode → claude → codex → cloud`). `LHASSA_TELEGRAM_PROVIDER`
(settings-editable) pins the CHAT door's provider independently — `default` (the
fast API gateway) keeps replies ~1.5s while flows/hard work stay on the heartbeat
seat; `auto`/empty keeps the router's routine decision. `notify/digest.py` builds a
deterministic $0 digest (signals + spend + approvals + episodes) written in
Lhassa's first-person voice, timestamps in the OWNER's local timezone; the
`send_digest` action + the `digest` seed schedule it at
`LHASSA_DIGEST_HOUR_{MORNING,EVENING}`. `digest_style=persona` (settings) adds ONE
cheap-model pass that re-voices it naturally (`digest_language`; any failure
degrades to plain — the digest always arrives).
`health/checks.py` notifies through this channel registry too (documented layer
exception).

## The org layer (Phase 7)

`org/topology.py` — `BrainNode` / `Topology` (validated tree) + `load/save`,
config-driven (`LHASSA_ORG_TOPOLOGY_FILE`). **Lhassa is the permanent root
administrator** (`config.root_admin_id`, `LHASSA_ROOT_ADMIN`, default `lhassa`):
always roots the chart, never removed/demoted; `promote` makes a brain a top-level
child of Lhassa without unseating it. Runs **standalone (1 brain)** up to arbitrary
depth. `org/coordinator.py` walks the tree: typed `TASK_ASSIGN` down, **only leaves
execute**, results bubble up; `GeneralBrain` **coordinates, never executes**;
`DepartmentBrain` executes via `org/executor.py:brain_executor` (the real one-turn
brain path, gated behind `LHASSA_ORG_LIVE`). `org/routing.py` is a deterministic,
table-driven router (**no LLM**). `org/rbac.py` is deny-by-default with
cross-department isolation; `org/commons.py` is a read-only shared row (only
`general` writes). `org/management.py` + `cli/org_cmds.py` create and manage the
chart (who commands whom). `org/templates.py` — use-case presets built **under
Lhassa** (`code-review`, `bug-investigation`, `refactor-migration`,
`incident-response`, the multi-level `software-factory`); `lhassa org
templates|apply`. **Real delegation (Etapa 3):** a judgment reply's `delegate` key
mints an `org_run` task (cap 3) through the `mint_task` security path;
`actionpack/_org.py:org_run` lazily runs `coordinator.run_goal` (offline/$0 unless
`LHASSA_ORG_LIVE`); the coordinator fans out to the top-3 departments on a routing
tie or explicit `params={"multi": True}` and synthesizes (deterministic offline
merge; one extra think live); `org/inbox_sense.py` registers a watchlist sense so
each tick drains this instance's `org_messages` inbox (enqueue → heartbeat closes
the loop); with `LHASSA_MONITOR_DELEGATE=1` the watcher converts monitor findings
into minted `org_run` tasks (fingerprint-deduped). `org/relationships.py` — a typed
**overlay** graph (separate file,
`~/.lhassa/relationships.json`, `LHASSA_ORG_RELATIONSHIPS_FILE`) layered on the
command tree: closed edge kinds `reads` / `notifies` / `reviews` (directed A→B) +
`collaborates` (symmetric, stored canonically); `permits(from, to, kind)` is the pure
runtime read seam ("may A read B?"); every mutating op validates (real nodes, known
kind, no self-edge) before it writes. `/api/org/relationships` (+ `/kinds`,
`POST`/`DELETE`); the dashboard Brains canvas draws the edges as a dashed per-kind
overlay (legend + toggles) and the inspector adds/removes them. Multi-person approval:
proposer ≠ approver for high-risk.

**The temple (`temple`) — Lhassa's own inner department.** Distinct from `interns`
(CV workers) and `personal` (the owner's own department): `temple` is a DIRECT
child of Lhassa, seeded by `seeds/temple.py` (`lhassa seed apply temple`), that owns
Lhassa's INTERNAL functions — docs, internal creation (tools/skills/flows authoring,
the consciousness proposals), synchronization (repo/calendar/memory), and
self-maintenance. `temple` has five leaf WORKER brains under it — its "monks" — each
owning a sub-domain: `scribe` (docs), `artificer` (internal creation), `synchronizer`
(sync), `guardian` (self-maintenance) and `oracle` (the consciousness — repetition
detection, self-routing proposals); the same seed creates all five, idempotently.
`org/temple.py:ACTION_OWNERS` is the deterministic action→monk ownership map for the
maintenance seed's five actions: `distill_memory`/`review_tools`/`curate_skills` →
`guardian`, `detect_repetition` → `oracle`, `calendar_sync` → `synchronizer`; an
unmapped action, or a topology missing the resolved worker, falls back to `temple`
itself. `org/temple.py:delegate_internal(action, params, config, instruction)` is the
down-execute-up loop: it resolves the owning worker (`resolve_worker`), logs ONE
`org-delegate` Episode delegating the action DOWN the path `lhassa → temple → <worker>`
(mirrors `org/delegate.py`), EXECUTES it via `core/actions.run` with its own Episode
attributed `dominio=<worker>` (the monk that did it, reporting to temple), then
delivers the result UP via `org/deliver.py:deliver(origin_department=<worker>, ...)` —
the per-brain hops walk worker → temple → Lhassa, the sender of record. A broken
topology (or a missing worker) degrades to `temple` itself; the whole loop never
raises. **Tool→brain reporting** — the org-wide "every tool reports to its brain"
pattern is born here: the worker's execution Episode names the tool (`accion`) and its
outcome (`resultado`), and when a real monk (not the bare department) ran it, the
`resultado` also states it reports up through `temple` — the trail reads "guardian ran
curate_skills → 3 stale skills archived → reports ↑ temple" end to end from the tool's
own log line, before the deliver seam's per-brain hops even start. The `maintenance`
seed's five hygiene actions are minted as `temple_run` (a closed, allowlisted wrapper,
`actionpack/_temple.py`) instead of running bare, so every beat now carries the full
down-execute-up hop and worker attribution — what each action DOES is unchanged. The
Telegram `/temple <instruction>` door keyword-matches a known deterministic action
(LLM-free) or falls through to `org_run(department="temple")` — the judgment door for
free-form internal work.

## Work-flows (`flows/`)

`flows/model.py` (`Flow`/`FlowNode`/`FlowEdge`, a validated DAG — closed `NodeKind`:
`trigger`/`task`/`gate`/`output`/`branch`) + `flows/store.py` (`FlowStore` over
`~/.lhassa/flows.json`, `LHASSA_FLOWS_FILE`) + `flows/engine.py` (`run_flow`). A flow
wires `trigger → task → gate → output` (+ `branch`); the engine walks from the single
trigger and threads each node's output along the edges. **Task** nodes route HYBRID (a
pinned `config.brain` wins, else the node's `skill` auto-routes through the org
topology) via the injectable `Executor` (default `org/executor.py:brain_executor`,
offline + $0 unless `LHASSA_ORG_LIVE`); a **gate** is verify/approval and a non-pass
halts the path. `api/flow_routes.py`: `GET/POST /api/flows`,
`GET/PUT/PATCH/DELETE /api/flows/{id}`, `POST /api/flows/{id}/run`. The dashboard
"Flows" section is the n8n-style canvas. No dedicated `lhassa` CLI subcommand yet.

**Standing principle — the hierarchy is the philosophy, not a feature.** It is
an INVARIANT of the same rank as deny-by-default and $0-first, and it binds
EVERY implementation, present and future: work flows DOWN the chart, results
and artifacts flow UP through it, and **every brain in a chain logs its own
hop** — a deterministic, $0 episode attributed via `dominio=<brain_id>` naming
what it received/produced/forwarded (files, company/role metadata, gates,
timestamps). One log per brain, both directions. A capability that produces or
delivers something to the owner is BORN on this path; a surface-direct send or
an unlogged hop is a design bug, not a shortcut. (The `org/deliver.py` seam +
artifact registry implement the up-direction; until they land, treat any
remaining direct send — e.g. the CV bridge's `send_document` — as recorded
debt, and never add another.)

**Standing principle — every flow runs through the hierarchy.** A flow is never run
bare: `POST /api/flows/{id}/run`, the Telegram bridge, and any future caller go through
`org/delegate.py:delegate_flow(flow, …)`, NOT `run_flow` directly (`run_flow` is the
internal engine). `delegate_flow` resolves the flow's **owning department** (pinned-brain →
department, e.g. the CV flow → `interns`), walks the command path `lhassa → … → dept`, logs
one **deterministic, $0, never-LLM** `org-delegate` Episode, then runs the flow (lazy import,
no org↔flows cycle). It NEVER blocks the work — a topology failure degrades to a direct run.
The hierarchy hops are deterministic on purpose: sovereignty + a full audit trail at no extra
token cost. The "Impossible-to-reject CV" flow (created by the `cv` seed) is the live exemplar — driven
from Telegram (`/cv <posting>` → tailors the on-disk `cv_largo.tex` → replies with a
tectonic-compiled PDF), guarded by a `contact_preserved` gate (an objective, LLM-free árbitro
that fails if the model altered the candidate's email/phone).

## The monitor role (`org/monitor.py` + `org/watcher.py`)

**Standing principle — "monitor" is a first-class, constant role.** `org/monitor.py`:
`gather_signals()` (read-only, $0 — recent episodes, health digest, flows, approvals,
failures) + `monitor_pass()` → `{observations, risks, improvements, delegations}` (offline
stub $0; live reasons via `brain_executor`). `make_brain_monitor(brain_id)` makes ANY brain a
monitor (applied to `interns`: role → `monitor`, "everyone reports to it"); `lhassa org
monitor` CLI. `org/watcher.py` is the always-on **$0 "ear"**: `detect_events()` diffs two
`gather_signals` snapshots (transitions only), `watch_loop()` runs an ELASTIC cadence — idle
`LHASSA_MONITOR_IDLE_INTERVAL` (300s) / active (`state.agent_runs` running)
`LHASSA_MONITOR_ACTIVE_INTERVAL` (15s) — and `react()` logs a monitor Episode always + a
de-duped Telegram alert only on IMPORTANT events (failures/health-alert/approvals). Idle is
$0; a token is only spent under `LHASSA_MONITOR_THINK` + `LHASSA_ORG_LIVE`. Config
`LHASSA_MONITOR_*` (default OFF); `scripts/systemd/lhassa-monitor.service` (opt-in). It reads
the same episode/signal plane the delegation + flows write to, so it watches the WHOLE
hierarchy: Lhassa delegates → departments execute → the cheap monitor reacts only on events.

## Agentic run surface (`api/code_routes.py`)

`POST /api/code {task, cli?}` launches an agentic coding task through a local AI CLI
(`agents/local_cli.py`: claude/codex/opencode, on subscription, no key) and streams it
over the WebSocket as `agent_started` / `agent_token` / `agent_done`;
`GET /api/code/clis` lists installed CLIs + the auto-detected default,
`GET /api/code/runs` lists recent runs. The dashboard "Run" section is the UI; the CLI
equivalent is `lhassa code "<task>"`. The `lhassa-app` service includes `~/.local/bin`
on PATH so it finds the `claude` CLI.

## Skills (first-class, 3-layer)

`skills/` — skills are **3-layer**: a NAME on a brain (intent, in the topology
skill set) → a `SKILL.md` in `~/.lhassa/skills/` (capability — YAML frontmatter +
instructions, the skills.md / awesome-agent-skills convention; JSON form too) →
loaded into the brain's prompt at think time (effect). `skills/store.py`
(`SkillStore`), `skills/skillmd.py` (the format), `skills/ai.py` (AI create/improve,
degrades without a model). Offline `recommend` works off a seed catalog (no model).
`skills/curator.py` is the lifecycle: usage telemetry in an atomic `.usage.json`
sidecar (bumped best-effort on every skill read) + a deterministic $0 sweep —
active → stale (`LHASSA_SKILL_STALE_DAYS`, 30) → archived
(`LHASSA_SKILL_ARCHIVE_DAYS`, 90) with a tar.gz backup first, moved to
`.archive/`, NEVER deleted; pinned skills exempt; `restore(name)` reverses. The
weekly `curate_skills` action (maintenance seed) runs it, skipping while an agent
run is active.
`assign_to_brain` / `brains_with_skill`; assigning offers to install. `lhassa
skills` (list/show/create[`--ai`]/improve/assign/who-has/recommend/import/export/
seed) + `/api/skills/*`. The specialist registry domains (`org skills`) are the
catalog of assignable skill names.

## Settings service

`settings.py` — a thin, safe layer over `config.py`: an **allowlist** (`EDITABLE`)
of runtime knobs the dashboard Settings panel and `lhassa config` may change. `set`
persists to `~/.lhassa/settings.env` (loaded by `config.py` **after** `.env` with
`override=True`, so a new process picks it up; secrets stay in `.env`, never written
here) **and** applies to the live `Config` class attribute. `effective()` is the
read model (value + default + overridden + restart hint, secrets masked); `set_env`
is the advanced `LHASSA_*` escape hatch. Setting `llm_provider` is guarded — it
refuses a provider that can't actually run (an API provider with no key, or a CLI
not on PATH). `GET/PATCH /api/config`.

## Two model seats

Keep the **chat** seat and the **heartbeat** seat distinct. *Chat* (CLI / voice /
the built-in agent) uses `llm_provider` + `model`; it may be an API provider
(groq/anthropic/openai/openrouter/cerebras — key in `.env`) **or** a local CLI on
subscription with **no API key** (`claude` / `codex` / `opencode`) or `ollama`.
*Heartbeat* (routine think) uses `heartbeat_provider` independently (auto/local/
cloud/opencode/claude/codex). The Telegram/org routine path walks the fallback
chain `opencode → claude → codex → cloud` (`LHASSA_FALLBACK_PROVIDERS`).
`lhassa code "<task>"` is the **agentic** door: it hands a task to a real local CLI
(claude/codex/opencode, `--cli` to pick) that edits files and runs tools in the
current directory. See `doc/cli.md` for the full CLI reference.

## API + dashboard

`api/server.py` (FastAPI) is a thin **integrator** — app + `require_local_or_token` auth
middleware + CORS + the WebSocket `/ws` fan-out (`state.broadcast(dict)`) + static serving +
`run_api_server` + `include_router(...)`; the actual routes live in domain routers
(`api/<domain>_routes.py`: runtime/llm/approval/checkin/skills/org/memory/config/tools/misc/code/
flow/relationship). Add a new endpoint group as its own `*_routes.py` with an `APIRouter`, don't
grow server.py. Routes include `/api/status`,
`/api/overview`, `/api/memory/*`, `/api/agents`, `/api/metrics`,
`/api/llm/{ledger,limits,ccusage}`, `/api/approvals`, `/api/audit`,
`/api/scheduled`, `/api/tools` (+ graduate), `/api/reports`, `/api/projections`,
`/api/episodes/search` (FTS5 recall),
`/api/fleet`, `/api/config` (GET/PATCH — the settings service), `/api/skills/*`,
`/api/org/*` (topology, brains, route, templates, relationships),
`/api/flows[/{id}[/run]]` (the work-flow editor), `/api/code` (+ `/clis`, `/runs` —
the agentic local-CLI door), and `/checkin` + `/report` (check-in, not push). The
dashboard sections are Overview, Brains (Flow/Tree/Templates + the relationship
overlay), Skills, **Run** (agentic), **Flows** (work-flow editor), Settings, Reports,
Fleet, Activity (`dashboard/src/components/`). The always-on
services run **API-only** (no audio); `docker/run_api.py` is the audio-free
entrypoint. The Lhasa visual identity (gold/red/white/green, a stupa mark) is
shared by the dashboard and the CLI theme (`cli/theme.py`).

## Conventions (still true)

- Config is **class-level** in `lhassa/config.py`, evaluated at import after
  `load_dotenv()`. `validate()` is called once (by `main.py`, the voice
  entrypoint) — it is the single place required-key errors surface. Do **not** move
  key validation back into the class body / import-time `_require()`; importing
  config must never crash tests or tooling. The API/heartbeat roles do **not** call
  `validate()`, so they run without the voice keys.
- Provider auto-detection: one API key is enough (`_detect_provider()` priority
  Anthropic → OpenAI → Groq → OpenRouter → Cerebras). Don't add
  `LHASSA_LLM_PROVIDER` to examples where the key alone suffices.
- Cached async clients in `llm/provider.py` (`_agent_clients`) and the tool
  modules' `_http`; all closed in `main._close_http_clients()`.
- Voice tools live in two places: schema in `TOOLS` + handler `handle_<name>` in
  `tools/registry.py`, registered in `register_tools()`. Handlers `_bump_tool()`,
  catch exceptions and call `result_callback(error)` — never raise.
- Use `if … raise RuntimeError(...)` for runtime checks, not `assert`.
- Everything in **English**. Lhassa is an **"it"** (no gender).

## Do not

- Add AI / Claude attribution anywhere (no Co-Authored-By, no "generated with").
- Commit anything personal (see `doc/boundaries.md`): the repo is the brain,
  `~/.lhassa/` is the person. Test fixtures use invented identities only
  (`owner@example.edu`); seeds must be generic presets; real emails/phones/
  employers/chat ids in the tree are treated like leaked secrets.
- Reintroduce the "cross-platform voice assistant" framing — Lhassa is a brain.
- Cross a layer boundary (the architecture guard will fail); prefer fixing the
  layering over adding an exception.
- Edit core to add a capability that a registry + plugin can drop in.
- Let `step()` / a heartbeat beat crash on a provider/budget error — degrade to a
  deferred episode.
</content>
