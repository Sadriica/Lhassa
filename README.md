# Lhassa

[![CI](https://github.com/OWNER/lhassa/actions/workflows/ci.yml/badge.svg)](https://github.com/OWNER/lhassa/actions/workflows/ci.yml)

> Replace `OWNER` in the badge URL above with your GitHub owner/org once a remote is configured.

A personal AI **brain** — a JARVIS-style core with its own identity that runs the
**do-and-sleep** loop: state lives on disk and a `step()` function advances it one
tick, woken by a systemd **heartbeat** timer (or on demand via the API). Lhassa
does not depend on any one model or tool — it **orchestrates** models (the
"think-directly" `ModelProvider`) and agentic harnesses (the `HarnessAdapter`)
behind stable interfaces: **build the brain, rent the body.** It is local-first,
**elastic and modular** (every swap point sits behind a registry; capabilities
drop in without editing core), reaches your machines by **check-in, not push**,
and grows from **one brain to an org of 1..N brains** with a user-defined
hierarchy. Surfaces: a terminal **CLI**, a **Telegram** bot, a live **dashboard**,
and an opt-in **voice** pipeline.

For the design rationale see [`doc/lhassa-brief.md`](doc/lhassa-brief.md); for a
current high-level map see [`doc/architecture.md`](doc/architecture.md).

---

## Quickstart

### One-command install (always-on Linux)

```bash
cd ~/Code/lhassa
bash scripts/install.sh
```

Preflights `python3`/`uv`/`docker`, runs `uv sync`, builds the dashboard, builds
and smoke-tests the Docker image, installs the systemd **user** units (app +
heartbeat, plus the Telegram poller if a token is set), and enables boot
lingering. Dashboard then lives at **http://127.0.0.1:7766**. Full details and
flags: [`doc/install.md`](doc/install.md).

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
docker compose up -d          # api + heartbeat roles
docker compose logs -f lhassa
```

One image, `LHASSA_RUN` selects the role (`api` / `heartbeat` / `telegram`). All
state lives under `/data` (`HOME=/data`). Details and portability caveats:
[`doc/deployment.md`](doc/deployment.md).

---

## Surfaces

### CLI (`lhassa`)

The default invocation is a **chat** turn through Lhassa's tool-calling loop:

```bash
lhassa "summarize this repo"        # one-shot from an argument
echo "data" | lhassa                # one-shot from piped stdin
lhassa                              # interactive REPL (TTY)
lhassa "..." --json                 # machine-readable {response, model, tokens, cost}
```

Power subcommands (all offline against the local brain DB, each supports `--json`):

| Command | What it does |
|---|---|
| `tools` | List self-written tools and their gate/usage state |
| `approvals` / `approve <id>` / `deny <id>` | The HITL approval queue (feeds the autonomy knob) |
| `report [--days N]` | Cost/usage summary (gateway ledger + optional ccusage) |
| `memory <query>` | Search durable facts (substring or tag) |
| `fleet` | Known machines, last check-in, desired state |
| `capabilities` (alias `doctor`) | What is wired + what is on/off (LLM-free) |
| `org …` | Manage the org topology — see below |

`lhassa org` manages the brain hierarchy (1..N brains): `tree`, `list`, `show`,
`add`, `remove`, `move`, `promote`, `set-skills`, `set-limits`, `skills`,
`route <goal>` (preview), `run <goal>` (end-to-end; thinks for real only when
`LHASSA_ORG_LIVE=1`).

### Telegram bot

Inbound/outbound over the Bot API, credential-gated (no token → no network).
Commands: `/start`, `/help` (lists registered commands live), `/status`
(provider + model + health + today's spend, **LLM-free**). Free-text messages are
answered by a tool-less brain think over the routine **fallback chain**
(`opencode → claude → codex → cloud`, see `LHASSA_FALLBACK_PROVIDERS`).

### Dashboard

A React/Vite dashboard served by the FastAPI app on **:7766** — status, metrics,
LLM ledger/limits, approvals, tools/quarantine, reports, fleet, and the org tree.
WebSocket fan-out at `/ws` streams live events.

### Voice (opt-in)

Wake-word → Deepgram STT → brain → Cartesia TTS pipeline (pipecat). Off by
default; needs `LHASSA_VOICE_ENABLED=1`, audio devices, and the speech keys. The
always-on services run **API-only** (no audio).

---

## Configuration

Everything is env-driven; a gitignored **`.env`** (copy from `.env.example`)
holds secrets — never commit it. The most relevant `LHASSA_*` knobs:

| Group | Vars |
|---|---|
| **Heartbeat / opencode** | `LHASSA_HEARTBEAT_PROVIDER` (auto/local/opencode/cloud), `LHASSA_OPENCODE_BIN`, `LHASSA_OPENCODE_MODEL` |
| **Local / fallback chain** | `LHASSA_LOCAL_MODEL_URL`, `LHASSA_LOCAL_MODEL`, `LHASSA_FALLBACK_PROVIDERS` |
| **Cost / router** | `LHASSA_ROUTER`, `LHASSA_MODEL_CHEAP`, `LHASSA_MODEL_SMART`, `LHASSA_LLM_DAILY_TOKEN_BUDGET`, `LHASSA_KILL_SWITCH` |
| **Telegram** | `LHASSA_TELEGRAM_BOT_TOKEN`, `LHASSA_TELEGRAM_CHAT_ID`, `LHASSA_TELEGRAM_POLL` |
| **Memory (layer 4)** | `LHASSA_MEMORY_LAYER4`, `LHASSA_EMBEDDING_MODEL`, `LHASSA_MEMORY_SEMANTIC_TOPK` |
| **Privacy** | `LHASSA_PSEUDONYMIZE_REMOTE` |
| **Org / topology** | `LHASSA_ORG_ROLE`, `LHASSA_ORG_DEPARTMENT`, `LHASSA_ORG_DEPARTMENTS`, `LHASSA_ORG_TOPOLOGY_FILE`, `LHASSA_ORG_LIVE` |
| **Plugins / channels** | `LHASSA_PLUGINS_DIR`, `LHASSA_NOTIFY_CHANNELS` |
| **Harnesses** | `LHASSA_HARNESS`, `LHASSA_AGENT_BACKEND`, `LHASSA_AGENT_DIR` |
| **Voice** | `LHASSA_VOICE_ENABLED`, `LHASSA_STT_LANGUAGE`, `LHASSA_MIC_NAME`, `LHASSA_VOICE_ID` |
| **Identity / API** | `LHASSA_INSTANCE_ID`, `LHASSA_API_HOST`, `LHASSA_API_PORT` (7766) |
| **Provider keys** | one is enough, auto-detected: `GROQ_API_KEY` / `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` / `OPENROUTER_API_KEY` / `CEREBRAS_API_KEY`; voice adds `DEEPGRAM_API_KEY` + `CARTESIA_API_KEY` |

See [`.env.example`](.env.example) for the full list with defaults and comments.

---

## Architecture

Brain state lives in `~/.lhassa/` (`brain.db`, `chica.db`, `backups/`, Ed25519
keys, `org.json`, `plugins/`, logs). The codebase is layered — infra → spine →
interfaces → adapters → shells — and the layering is enforced by an
architecture-guard test. For the layer map, the data dir, the phase history (0–7),
and the do-and-sleep / provider-harness / org-tree diagrams, read
[`doc/architecture.md`](doc/architecture.md). For the full spec read
[`doc/lhassa-brief.md`](doc/lhassa-brief.md); the running decisions log is
[`doc/decisions.md`](doc/decisions.md).

| Document | Contents |
|---|---|
| [doc/architecture.md](doc/architecture.md) | Current high-level overview (layers, data dir, phases, diagrams) |
| [doc/architecture-layers.md](doc/architecture-layers.md) | The modularity guard — layers + forbidden import directions |
| [doc/lhassa-brief.md](doc/lhassa-brief.md) | The implementation spec (Spanish) |
| [doc/decisions.md](doc/decisions.md) | Running decisions log (phase by phase) |
| [doc/install.md](doc/install.md) | One-command always-on Linux install |
| [doc/deployment.md](doc/deployment.md) | Docker image, Compose roles, portability |

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

## License

MIT — see [LICENSE](LICENSE).
</content>
</invoke>
