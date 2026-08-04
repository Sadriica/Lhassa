<p align="center">
  <img src="assets/lhassa-mark.svg" alt="Lhassa — the gold stupa mark" width="88">
</p>

<h1 align="center">Lhassa</h1>

<p align="center"><b>Build the brain, rent the body.</b></p>

<p align="center">A <a href="https://witara.site">Witara</a> project — calm software, council not control.</p>

<p align="center">
  <a href="README.es.md">Español</a> ·
  <a href="doc/README.md">Docs</a> ·
  <a href="doc/architecture.md">Architecture</a> ·
  <a href="doc/cli.md">CLI reference</a> ·
  <a href="LICENSE">AGPL-3.0</a>
</p>

<p align="center">
  <a href="https://github.com/Sadriica/Lhassa/actions/workflows/ci.yml"><img src="https://github.com/Sadriica/Lhassa/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
</p>

---

Lhassa is a persistent personal AI **brain** — an identity that lives awake all
day for $0, orchestrates rented models and tools, earns its autonomy through
verified trust, and grows from one brain into an organization.

It is named for Lhasa, the city at the roof of the world: a place pilgrims
reach slowly, on purpose. The durable thing here is *who Lhassa is* — its
memory, its judgment, its trust record. Models, harnesses and tools are
interchangeable muscle, rented by the hour and swapped without surgery. That is
the whole thesis: **build the brain, rent the body.**

> This is not a chatbot with cron jobs. It is a **do-and-sleep** organism:
> state lives on disk, a heartbeat advances it one deterministic tick every two
> minutes, and thinking — an actual model call — is the exception, not the rule.

## Who it's for

Lhassa is for you if you want an assistant you **own** — one that runs on your
own machine, remembers you across every session, costs nothing to keep awake,
and answers to an audit log *you* control. One radical bet drives the whole
design: **thinking is the exception.** A deterministic, $0 plane handles almost
everything; a model is touched only when judgment truly can't be avoided.
Everything else — the sovereignty, the memory, the self-forged tools, the
org of brains — follows from that.

It is, on purpose, **not** a few things:

- **not a hosted chatbot** — it lives on your disk, not someone else's cloud;
- **not plug-and-play magic** — you bring your own brain (one API key, or a
  fully local model — nothing is rented from us, because there is no *us*);
- **not a framework to build on** — it's a complete, opinionated brain, not a
  toolkit of parts.

If "sovereign, ambient, and $0 by default" is the trade you'd take over
"zero-setup magic in someone else's cloud," this was built for you. If you just
want to open an app and chat, a hosted assistant will serve you better — and
that's fine.

## Why Lhassa

| | |
|---|---|
| **A routine day costs $0** | The heartbeat beats ~720 times a day; a deterministic plane (watchlist, reflexes, actions, monitor, digest, org routing) answers almost all of it without touching a model. From the author's real ledger — July 4, 2026: ~720 wake-ups, **5** metered thinks, **$0.0031**. |
| **Subscriptions before APIs** | When it does think, it walks your already-paid CLI subscriptions (`claude` / `codex` / `opencode`) before spending a metered API cent. A budget router, a soft brake at 80% and a kill-switch guard whatever remains. |
| **It forges its own hands** | Repetition is detected with $0 SQL over its episode log; the brain writes the tool (and its test) that replaces the repeated spend. Self-written code runs **only in quarantine** until it passes the four gates and you approve its graduation. Tools that stop earning their keep are retired. |
| **Sovereign by construction** | Deny-by-default action allowlist, Ed25519-signed tasks, trust that only degrades, human-in-the-loop approvals with an autonomy knob that is *earned*, and an append-only episode log: every decision it ever made, auditable. |
| **No lock-in, ever** | Two swap axes — a `ModelProvider` to think, a `HarnessAdapter` to act — behind registries; every capability drops in as a plugin without editing core. Swap the model, keep the brain. |
| **From one brain to an org** | The same core scales to a hierarchy of brains: departments, command chains, a monitor role that is always listening for $0, deterministic routing, and a full audit trail on every delegation. |
| **It remembers, in layers** | Typed facts with contradiction handling, a working cache, semantic retrieval, and FTS5 recall over its own past episodes — ask it something and its answer arrives already informed by its experience. |

## A day in the life

What "ambient at $0" actually looks like, tick by tick:

```
07:58  heartbeat — watchlist quiet, nothing due. $0.
08:00  the morning check-in lands on Telegram, in Lhassa's own voice,
       built deterministically from state already on disk. $0.
10:14  you ask it something; FTS5 recall pulls two past episodes into
       context before the think — one subscription CLI call, $0 metered.
11:03  a repo it watches gets a commit → the repo_changed reflex syncs it.
       No judgment needed. $0.
13:40  the monitor (its always-on ear) sees a new failure episode and
       names it in a Telegram alert — what failed, not just how many. $0.
14:03  daily maintenance: memory distills, repetition is scanned, stale
       skills are curated (never deleted — archived, with a backup). $0.
19:04  the repetition detector proposes: "Make 'responder' automatic —
       repeated ×41, $0.26 spent on it so far." Your call, one tap.
21:00  the evening check-in. The wheel turned all day; the ledger
       barely moved.
```

## It forges its own hands

Self-written code earns trust the way a pilgrim earns altitude — one gate at a
time, no shortcuts:

1. **detected** — $0 SQL finds a pattern you keep paying for;
2. **written** — the routine model authors a candidate tool *and its test*;
3. **quarantined** — it runs only inside the strongest sandbox available
   (`unshare`/`firejail`/`bwrap`), no network, scrubbed env;
4. **gated** — segura → correcta → mejor: objective verifiers, never an LLM
   judging an LLM;
5. **graduated** — a human approves; the API refuses (409) anything that
   skipped the gates;
6. **retired** — tools that stop earning their keep are removed the same
   audited way.

The same discipline extends to orchestration: with PTC (Programmatic Tool
Calling), one think can write a Python script that drives many deterministic
actions over an RPC socket — inside the same quarantine, every call re-checked
against the same allowlist.

---

## Quickstart

Talking to Lhassa in about two minutes, no systemd, no Docker:

```bash
git clone https://github.com/Sadriica/Lhassa.git
cd Lhassa
make quickstart
```

`make quickstart` resolves the venv (`uv sync`, no dashboard build needed yet),
makes sure a `.env` exists, and checks whether it can find a brain to think
with. **Bring your own** — either a free/cheap API key (**[Groq's free
tier](https://console.groq.com)** is the easiest start; Anthropic/OpenAI/
OpenRouter/Cerebras all work too, one key is enough) dropped into `.env`, or go
**fully local and $0** with **[Ollama](https://ollama.com)** — no key at all.
Re-run `make quickstart` once you've added one; it'll tell you what's next:
`uv run lhassa onboard` (a short questionnaire that seeds its first facts about
you) and then `uv run lhassa` — you're talking to it.

First things to try:

- just talk to it, plain language, no special syntax
- `lhassa recall <text>` — search its own past episodes (FTS5)
- `lhassa memory <query>` — search its durable facts
- `lhassa capabilities` — what's wired, what's on/off

Want it **always-on** — a heartbeat that keeps ticking while you sleep,
Telegram, systemd + Docker? That's the next section below
(`bash scripts/install.sh`), or the full walkthrough in
[`doc/install.md`](doc/install.md).

No clone yet, on a totally fresh machine? A single command handles the clone,
`uv` and the `lhassa` shim for you:

```bash
curl -fsSL https://lhassa.witara.site/get-lhassa.sh | bash
```

Details and flags: [`doc/install.md`](doc/install.md#one-command-install).

---

## Full install (always-on)

### Supported platforms

| | Platform | What that means |
|---|---|---|
| **Tier 1** | **Linux** | The platform Lhassa is developed and deployed on. Full CI (Python suite, lint, dashboard E2E). Every surface works: systemd units, the `unshare`/`firejail`/`bwrap` quarantine, `notify-send` toasts, `/proc` readings. |
| **Tier 2** | **macOS (Apple Silicon)** | The Python suite and lint run in CI. The brain, the CLI, the API/dashboard, Telegram and the local-model path (Ollama + local embeddings, which are plain HTTP) all work. Anything touching the host is best-effort — see the caveats below. |

Windows is not a supported tier; parts of the code path handle it, none of it is tested.

**macOS caveats, stated plainly:**

- **The tool forge and PTC need a sandbox.** Lhassa's quarantine prefers
  `unshare`/`firejail`/`bwrap`, none of which exist on macOS; it falls back to
  `sandbox-exec`. When no sandbox is available at all, an unsandboxed run is
  **refused** rather than downgraded — model-written code never executes
  unconfined. On Linux the pre-existing warn-and-proceed behaviour is unchanged.
- **Memory pressure is not the same number.** macOS accounts compressed,
  purgeable and wired pages differently, so the `vm_stat`-derived figure is
  reported with its source named and flagged as not comparable to Linux's
  `/proc/meminfo` arithmetic. It is not presented as the same metric.
- **Service health is approximate.** `launchctl` reports a job's *last exit
  status*, not a persistent failed state the way a systemd unit has one. Only
  `com.lhassa.*` labels are considered.
- **launchd agents** live in [`scripts/launchd/`](scripts/launchd/) and mirror
  `scripts/systemd/`; that directory's README documents where the mapping is
  approximate rather than equivalent.
- **Voice** (microphone capture) is the least portable surface and is untested
  on macOS.

### Requirements

System dependencies (install these first):

- **[`uv`](https://docs.astral.sh/uv/)** — the Python toolchain (`uv sync` resolves the venv). Required.
- **`node` + `npm`** — build the React/Vite dashboard (`npm ci && npm run build`). Required for the dashboard.
- **`docker`** — only for the containerized path / the installer smoke test (skip with `--no-docker`).
- **`tectonic`** *(optional)* — the LaTeX engine that compiles a tailored CV `.tex` into a PDF. Without it, the CV bridge falls back to sending the raw `.tex`.
- **`ccusage`** *(optional)* — cross-harness LLM-spend CLI for the dashboard's cost panels. Install user-level with `npm install -g --prefix ~/.local ccusage`.

The subscription CLIs (`claude` / `codex` / `opencode`) are optional and need **no API key** — install whichever you use.

### One-command install (always-on Linux)

```bash
cd ~/Code/lhassa
bash scripts/install.sh
```

Preflights `python3`/`uv`/`docker`, runs `uv sync`, builds the dashboard, builds
and smoke-tests the Docker image, installs the systemd **user** units (app +
heartbeat + health timer + the always-on `$0` monitor watcher, plus the Telegram
poller if a token is set), and enables boot lingering. Dashboard then lives at
**http://127.0.0.1:7766**. Full details and flags: [`doc/install.md`](doc/install.md).

### CV pipeline bootstrap

On a fresh machine, seed the "Impossible-to-reject CV" work-flow with:

```bash
lhassa seed list       # list the registered seeds
lhassa seed apply cv       # recreates the interns department + the CV work-flow
lhassa seed apply personal # creates the personal department + morning/evening flows
```

Then drop your real CV at `~/.lhassa/cv/cv_largo.tex` (or set `LHASSA_CV_DIR`).
Install the optional `tectonic` dep to get a compiled PDF instead of raw `.tex`.

### Developer path

```bash
make install   # uv sync + build the dashboard (first time)
make run       # voice pipeline + API server + scheduler (uv run python main.py)
make dev       # same, with LHASSA_DEBUG=1 LHASSA_LOG_LEVEL=DEBUG
make test      # uv run --extra dev python -m pytest
make lint      # ruff
make check     # validate the environment, no API calls
```

### Docker

```bash
cp .env.example .env          # fill in the keys you use
docker compose up -d          # api + heartbeat + monitor roles
docker compose logs -f lhassa
```

One image, `LHASSA_RUN` selects the role (`api` / `heartbeat` / `telegram` /
`monitor`). All state lives under `/data` (`HOME=/data`). Lhassa runs on a
laptop today and is built to run unchanged on a small VPS: details and the
portability audit live in [`doc/deployment.md`](doc/deployment.md) and
[`doc/portability.md`](doc/portability.md).

---

## Surfaces

### CLI (`lhassa`)

The default invocation is a **chat** turn through Lhassa's tool-calling loop,
themed in the Lhasa palette (gold / red / white / green, prompt `lhassa ›`):

```bash
lhassa "summarize this repo"        # one-shot from an argument
echo "data" | lhassa                # one-shot from piped stdin
lhassa                              # interactive REPL (TTY)
lhassa "..." --json                 # machine-readable {response, model, tokens, cost}
lhassa code "add a --json flag"     # an AGENTIC task via a local CLI (reads/edits files)
```

`lhassa code` hands the task to a real agentic CLI (`claude` / `codex` /
`opencode`, on your subscription, no API key; `--cli` to choose) that can edit
files and run tools in the current directory, streaming live.

Subcommands (most are offline against the local brain DB, each supports `--json`):

| Command | What it does |
|---|---|
| `code <task>` | Run an agentic coding task through a local AI CLI (subscription, no key) |
| `status` / `overview` | The whole brain at a glance (brains, skills, approvals, cost, health) |
| `watch` | Tail recent activity (`--once` for one frame) |
| `report [--days N]` | Cost/usage summary (gateway ledger + optional ccusage) |
| `memory <query>` | Search durable facts (substring or tag) |
| `tools` | List self-written tools and their gate/usage state |
| `approvals` / `approve <id>` / `deny <id>` | The HITL approval queue (feeds the autonomy knob) |
| `fleet` | Known machines, last check-in, desired state |
| `capabilities` (alias `doctor`) | What is wired + what is on/off (LLM-free) |
| `version` | Version, runtime, key toggles |
| `serve` | Run the dashboard + REST API |
| `beat` | Run one heartbeat tick (deterministic, $0) |
| `config get/set/list` | Runtime settings → `~/.lhassa/settings.env` (see below) |
| `org …` | Manage the brain hierarchy (1..N brains) |
| `skills …` | Manage first-class skills (create/assign/recommend/…) |

`lhassa config set llm_provider claude` switches the **chat provider** live;
`lhassa org apply software-factory` adopts a use-case org chart;
`lhassa skills assign code-review backend` gives a brain a skill. The full
reference with every flag and example is in **[`doc/cli.md`](doc/cli.md)**.

### Telegram bot

Inbound/outbound over the Bot API, credential-gated (no token → no network).
LLM-free commands: `/start`, `/help` (lists registered commands live), `/status`,
`/approvals` (each pending decision described in plain words — what it is, how
old, what approving does), `/approve` / `/deny`, `/recall <text>` (FTS5 search
over the brain's own past episodes), `/watch`, `/goal`, `/today`, `/tomorrow`,
and `/checkin <text>` for the personal digest thread. Free-text messages are
answered by a tool-less brain think — the chat door can pin its own fast
provider (`telegram_provider`) while heavier work stays on the heartbeat seat —
over the routine **fallback chain** (`opencode → claude → codex → cloud`). The
twice-daily check-in digest arrives in Lhassa's first-person voice ($0
deterministic template, optional model re-voicing via `digest_style=persona`);
with `LHASSA_DIGEST_PERSONAL_CHECKIN=1` it also asks a morning/evening personal
question and captures your next Telegram reply as a daily check-in.

### Dashboard

A React/Vite dashboard served by the FastAPI app on **:7766**, in the Lhasa
identity (warm gold, a stupa mark). Sections: **Overview**; **Brains** (the
Flow/Tree/Templates editor, with the typed **relationship overlay** drawn as dashed
per-kind edges — legend + toggles — and add/remove from the inspector); **Skills**;
**Run** (launch an agentic local-CLI task and watch it stream); **Flows** (an
n8n-style work-flow editor wiring `trigger → task → gate → output`); **Settings**
(the editable runtime knobs); **Reports**; **Fleet**; and **Activity** (with
full-text search over the episode log). The LLM ledger/limits, approvals and
tools/quarantine surface here too. WebSocket fan-out at `/ws` streams live events
(including the agentic run tokens).

The same FastAPI app exposes the REST surface. Beyond the brain/LLM/approval routes
it includes `/api/org/*` (topology · brains · route · templates · **relationships**),
`/api/flows[/{id}[/run]]` (the work-flow editor), `/api/episodes/search` (FTS5
recall), and `/api/code` (+ `/clis`, `/runs` — the agentic local-CLI door).

### Voice (opt-in)

Wake-word → Deepgram STT → brain → Cartesia TTS pipeline (pipecat). Off by
default; needs `LHASSA_VOICE_ENABLED=1`, audio devices, and the speech keys. The
always-on services run **API-only** (no audio).

---

## Configuration

Everything is env-driven; a gitignored **`.env`** (copy from `.env.example`)
holds secrets — never commit it. A second file, **`~/.lhassa/settings.env`**, holds
runtime overrides written by `lhassa config set` / the dashboard Settings panel; it
is loaded *after* `.env` (override on) and never holds secrets. The most relevant
`LHASSA_*` knobs:

| Group | Vars |
|---|---|
| **Chat (CLI/voice/agent)** | `LHASSA_LLM_PROVIDER` (groq/anthropic/openai/openrouter/cerebras/ollama **or** the local CLIs claude/codex/opencode), `LHASSA_MODEL` |
| **Heartbeat / opencode** | `LHASSA_HEARTBEAT_PROVIDER` (auto/local/cloud/opencode/claude/codex), `LHASSA_OPENCODE_BIN`, `LHASSA_OPENCODE_MODEL` |
| **Telegram chat seat** | `LHASSA_TELEGRAM_PROVIDER` (auto/default/claude/codex/opencode/local — the chat door's own fast provider) |
| **Local / fallback chain** | `LHASSA_LOCAL_MODEL_URL`, `LHASSA_LOCAL_MODEL`, `LHASSA_FALLBACK_PROVIDERS` (default `claude,codex,default`) |
| **Cost / router** | `LHASSA_ROUTER`, `LHASSA_MODEL_CHEAP`, `LHASSA_MODEL_SMART`, `LHASSA_LLM_DAILY_TOKEN_BUDGET`, `LHASSA_KILL_SWITCH` |
| **Telegram** | `LHASSA_TELEGRAM_BOT_TOKEN`, `LHASSA_TELEGRAM_CHAT_ID`, `LHASSA_TELEGRAM_POLL` |
| **Digest** | `LHASSA_DIGEST_HOUR_MORNING/EVENING`, `LHASSA_DIGEST_STYLE` (plain/persona), `LHASSA_DIGEST_LANGUAGE`, `LHASSA_DIGEST_PERSONAL_CHECKIN` |
| **Memory** | `LHASSA_MEMORY_LAYER4`, `LHASSA_EMBEDDING_MODEL`, `LHASSA_MEMORY_SEMANTIC_TOPK`, `LHASSA_MEMORY_DISTILL_ENABLED` |
| **Monitor** | `LHASSA_MONITOR_ENABLED`, `LHASSA_MONITOR_IDLE_INTERVAL`, `LHASSA_MONITOR_FAILURE_WINDOW_H` |
| **Skills curator** | `LHASSA_SKILL_STALE_DAYS` (30), `LHASSA_SKILL_ARCHIVE_DAYS` (90) |
| **Privacy** | `LHASSA_PSEUDONYMIZE_REMOTE` |
| **Org / topology** | `LHASSA_ROOT_ADMIN`, `LHASSA_ORG_ROLE`, `LHASSA_ORG_DEPARTMENT`, `LHASSA_ORG_DEPARTMENTS`, `LHASSA_ORG_TOPOLOGY_FILE`, `LHASSA_ORG_RELATIONSHIPS_FILE`, `LHASSA_ORG_LIVE` |
| **Work-flows** | `LHASSA_FLOWS_FILE` (the work-flow graph store; runs are offline/stubbed unless `LHASSA_ORG_LIVE`) |
| **Plugins / channels** | `LHASSA_PLUGINS_DIR`, `LHASSA_NOTIFY_CHANNELS` |
| **Harnesses / tools** | `LHASSA_HARNESS`, `LHASSA_AGENT_BACKEND`, `LHASSA_AGENT_DIR`, `LHASSA_TOOLS_REPO_DIR` |
| **Voice** | `LHASSA_VOICE_ENABLED`, `LHASSA_STT_LANGUAGE`, `LHASSA_MIC_NAME`, `LHASSA_VOICE_ID` |
| **Identity / API / CLI** | `LHASSA_INSTANCE_ID`, `LHASSA_API_HOST`, `LHASSA_API_PORT` (7766), `LHASSA_CLI_LOG_LEVEL` |
| **Provider keys** | one is enough, auto-detected: `GROQ_API_KEY` / `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` / `OPENROUTER_API_KEY` / `CEREBRAS_API_KEY`; voice adds `DEEPGRAM_API_KEY` + `CARTESIA_API_KEY`. The CLI providers `claude` / `codex` / `opencode` need **no key** — they run on your subscription. |

See [`.env.example`](.env.example) for the full list with defaults and comments.

---

## Architecture

Brain state lives in `~/.lhassa/` (`brain.db`, `chica.db`, `llm_gateway.db`,
`backups/`, Ed25519 keys, `settings.env`, `org.json`, `relationships.json`,
`flows.json`, `skills/`, `plugins/`, `tools/`, logs). The codebase is layered — infra → spine →
interfaces → adapters → shells — and the layering is enforced by an
architecture-guard test. For the layer map, the data dir, the phase history (0–7),
and the do-and-sleep / provider-harness / org-tree diagrams, read
[`doc/architecture.md`](doc/architecture.md). For the full spec read
[`doc/lhassa-brief.md`](doc/lhassa-brief.md); the running decisions log is
[`doc/decisions.md`](doc/decisions.md); the narrative voice book is
[`doc/narrative.md`](doc/narrative.md).

| Document | Contents |
|---|---|
| [doc/README.md](doc/README.md) | The docs index — start here, organized by reader intent |
| [doc/getting-started.md](doc/getting-started.md) | Install → first heartbeat → Telegram → dashboard, step by step |
| [doc/concepts.md](doc/concepts.md) | The ideas: do-and-sleep, the two seats, memory layers, the org |
| [doc/security.md](doc/security.md) | The sovereignty model: allowlist, signed tasks, quarantine, gates |
| [doc/telegram.md](doc/telegram.md) | The operative day over Telegram — commands, digest, /cv |
| [doc/architecture.md](doc/architecture.md) | Current high-level overview (layers, data dir, phases, diagrams) |
| [doc/cli.md](doc/cli.md) | CLI reference — every `lhassa` subcommand, flags and examples |
| [doc/architecture-layers.md](doc/architecture-layers.md) | The modularity guard — layers + forbidden import directions |
| [doc/lhassa-brief.md](doc/lhassa-brief.md) | The implementation spec (Spanish) |
| [doc/decisions.md](doc/decisions.md) | Running decisions log (phase by phase) |
| [doc/narrative.md](doc/narrative.md) | The narrative & voice book (flag, pillars, tone, vocabulary) |
| [doc/install.md](doc/install.md) | One-command always-on Linux install |
| [doc/deployment.md](doc/deployment.md) | Docker image, Compose roles, portability |
| [doc/portability.md](doc/portability.md) | The VPS-portability audit (couplings, status, blockers) |

---

## Development

```bash
uv run --extra dev python -m pytest      # the test suite (== make test)
uv run --extra dev python -m ruff check . # lint (== make lint)
cd dashboard && npm run build             # rebuild the dashboard (== make dashboard)
```

`tests/test_architecture.py` is a dependency-free guard that fails on any new
layer-crossing import — keep the layering clean (see
[`doc/architecture-layers.md`](doc/architecture-layers.md)). CI runs on pushes and
PRs via [`.github/workflows/ci.yml`](.github/workflows/ci.yml).

---

## Status & honesty

Lhassa is a young project with one demanding first user: its author. Everything
described above runs today and is covered by ~1,400 tests; what doesn't run yet
is not on this page. It lives on a laptop and is built to move to a small VPS
without surgery.

## License

**AGPL-3.0-or-later**, with one additional term under section 7(b) — see
[LICENSE](LICENSE).

**What that means if you self-host it.** Run Lhassa on your own machine, for
yourself or inside your company, and you owe nothing: modify it, keep your
changes private, use it however you like. The AGPL's extra obligation is
narrow — it applies **only if you offer Lhassa to other people over a network
as a service**. In that case, those users must be able to get the source of
the version you are running, including your modifications.

The additional 7(b) term requires you to keep the attribution notice
("Lhassa — a Witara project", the license, and a link to the source) visible
wherever the software already shows legal or version information: the CLI, the
API and the dashboard. Nothing more — it grants no rights over the names and
takes away no freedom the AGPL gives you.

**Commercial licensing.** If the AGPL does not fit your situation, a separate
commercial license is possible — contributions are gathered under a CLA (see
[CONTRIBUTING.md](CONTRIBUTING.md)) precisely so that option stays open.

<p align="center">— <i>The wheel turns all day; the ledger barely moves.</i> —</p>
