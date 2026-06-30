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
  fixed **action** (from `core/actions.py`) → a structured **episode**. The `else`
  (judgment) branch is the think path: load memory context → `llm/router.py`
  picks a provider/model → `provider.complete(...)`. `BudgetExceeded` /
  `ProviderNotConfigured` **degrade to a deferred episode, never crash**.
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
commands (`COMMANDS`), actions (`core/actions.ACTIONS`), org departments.
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
  pluggable `Embedder` (`HashingEmbedder` offline default). Off by default
  (`LHASSA_MEMORY_LAYER4=1`); it has the lowest priority so it truncates first and
  never touches the identity core.
- `memory/consolidate.py` — semantic consolidation via the cheap model (only the
  chica). `memory/store.py`/`session.py` is the legacy voice-session store.

## The árbitro (verify) + security

- **Verifiers** (`agents/verify.py`) are objective (`command/tests/lint/health/
  regex/metric`) and **never call an LLM** (no provider import; asserted in tests).
  Wired into `step`'s judgment branch via `router.cascade(verify=…)` — failed
  verification escalates cheap→smart once.
- **Quarantine** (`security/quarantine.py`) — strongest-available sandbox
  (`unshare`/`firejail`/`bwrap`), no-network, scrubbed env, `killpg` on timeout.
  Self-written tools graduate only through the four gates (`agents/gates.py`:
  segura → correcta → mejor → retiro), deny-by-default.
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
(`@register_command`): `/start`, `/help`, `/status` (all LLM-free). Inbound
Telegram (`notify/telegram_inbound.py`, getUpdates poller, honors only the
configured chat) dispatches a slash command before falling back to a tool-less
brain think over the routine fallback chain (`opencode → claude → codex → cloud`).

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
templates|apply`. `org/relationships.py` — a typed **overlay** graph (separate file,
`LHASSA_ORG_RELATIONSHIPS_FILE`) layered on the command tree: closed edge kinds
`reads` / `notifies` / `reviews` (directed) + `collaborates` (symmetric);
`permits(a,b,kind)` is the pure read seam; `/api/org/relationships*`. Multi-person
approval: proposer ≠ approver for high-risk. (`flows.json` /
`LHASSA_FLOWS_FILE` is a reserved config path; work-flow graphs are not yet a
surface.)

## Skills (first-class, 3-layer)

`skills/` — skills are **3-layer**: a NAME on a brain (intent, in the topology
skill set) → a `SKILL.md` in `~/.lhassa/skills/` (capability — YAML frontmatter +
instructions, the skills.md / awesome-agent-skills convention; JSON form too) →
loaded into the brain's prompt at think time (effect). `skills/store.py`
(`SkillStore`), `skills/skillmd.py` (the format), `skills/ai.py` (AI create/improve,
degrades without a model). Offline `recommend` works off a seed catalog (no model).
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

`api/server.py` (FastAPI) serves the dashboard and the REST surface plus a
WebSocket `/ws` fan-out (`state.broadcast(dict)`). Routes include `/api/status`,
`/api/overview`, `/api/memory/*`, `/api/agents`, `/api/metrics`,
`/api/llm/{ledger,limits,ccusage}`, `/api/approvals`, `/api/audit`,
`/api/scheduled`, `/api/tools` (+ graduate), `/api/reports`, `/api/projections`,
`/api/fleet`, `/api/config` (GET/PATCH — the settings service), `/api/skills/*`,
`/api/org/*` (topology, brains, route, templates, relationships), `/api/code` (+
`/clis`, `/runs` — the agentic local-CLI door), and `/checkin` + `/report`
(check-in, not push). The dashboard panels live in `dashboard/src/components/`
(Overview, Settings, Brains flow/tree, Templates, Skills, …). The always-on
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
- Reintroduce the "cross-platform voice assistant" framing — Lhassa is a brain.
- Cross a layer boundary (the architecture guard will fail); prefer fixing the
  layering over adding an exception.
- Edit core to add a capability that a registry + plugin can drop in.
- Let `step()` / a heartbeat beat crash on a provider/budget error — degrade to a
  deferred episode.
</content>
