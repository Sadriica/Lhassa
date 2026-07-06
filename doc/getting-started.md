# Getting started

Lhassa is a persistent personal AI brain, not a chat window you open and
close. It runs a **do-and-sleep** loop: state on disk plus a `step()` that
advances one tick at a time, woken every couple of minutes by a heartbeat
timer or on demand from the CLI, Telegram or the dashboard. A routine day
costs $0 — most ticks are answered by deterministic checks, never a model —
and it thinks only when the judgment actually calls for it.

This walkthrough gets a real instance running on a Linux machine: installed,
answering on the CLI, ticking its own heartbeat, wired to Telegram, and
visible on the dashboard.

## 1. Requirements

| Dependency | Why | Required? |
|---|---|---|
| [`uv`](https://docs.astral.sh/uv/) | Python toolchain — `uv sync` resolves the `.venv`. | Yes |
| `node` + `npm` | Build the React/Vite dashboard (`npm ci && npm run build`). | Yes, for the dashboard |
| `docker` | The containerized path + the installer's build/smoke test. | Only without `--no-docker` |
| `tectonic` | LaTeX engine that compiles a tailored CV `.tex` into a PDF. Without it the CV bridge sends the raw `.tex`. | Optional |
| `ccusage` | Cross-harness LLM-spend CLI for the dashboard cost panels. `npm install -g --prefix ~/.local ccusage`. Without it those panels show "unavailable". | Optional |

For thinking, you need **one** LLM API key — Lhassa auto-detects the provider
from whichever one is set (priority: Anthropic → OpenAI → Groq → OpenRouter →
Cerebras), no extra config needed. Or skip API keys entirely and point it at
a subscription CLI you already have (`claude` / `codex` / `opencode`) — those
need no key at all, just the binary on `PATH`.

Verify the toolchain:

```bash
uv --version
node --version
docker --version   # only if you'll use --no-docker is NOT set
```

## 2. Install

Two paths — pick one.

### One command (always-on Linux)

```bash
cd ~/Code/lhassa
bash scripts/install.sh
```

This preflights `python3`/`uv`/`docker`, runs `uv sync`, builds the dashboard
if `dashboard/dist` is missing, builds and smoke-tests a Docker image,
installs the systemd **user** units (app + heartbeat + health timer + the
always-on $0 monitor watcher, plus the Telegram poller if a token is already
set in `.env`), and enables boot lingering so everything survives a reboot.
Flags: `--no-docker`, `--docker-only`, `--no-boot`, `--image NAME`. Full
details in [`install.md`](install.md).

When it finishes, the dashboard is live at **http://127.0.0.1:7766** and the
heartbeat is already ticking on a timer.

### Manual dev path

```bash
make install   # uv sync --extra voice + npm install && npm run build
```

This is the laptop dev/voice workflow: it resolves the venv and builds the
dashboard, but doesn't install any systemd units or start anything long-running
— you run things yourself as you go (`make run`, `make dev`, `lhassa serve`,
`lhassa beat`, ...). See the Makefile targets: `make test`, `make lint`,
`make check` (validates the environment, no API calls).

Either path, put your API key(s) in `.env` first:

```bash
cp .env.example .env
# then set ONE of: GROQ_API_KEY / ANTHROPIC_API_KEY / OPENAI_API_KEY /
# OPENROUTER_API_KEY / CEREBRAS_API_KEY
```

## 3. First contact

```bash
lhassa --help
```

prints Lhassa's own first-person greeting ("I am Lhassa. I live on this
machine — I watch quietly, remember what matters, and think only when it is
worth the spend.") followed by every subcommand.

Talk to it:

```bash
lhassa "hello"
```

One-shot from an argument — this is a real chat turn through the tool-calling
loop. With no argument it drops into an interactive REPL; piped stdin
(`echo "..." | lhassa`) works the same way. Add `--json` to get a machine
-readable `{response, model, tokens, cost}` object instead.

Run the loop yourself, once, for free:

```bash
lhassa beat
```

This is exactly what the heartbeat timer runs on its own schedule: one
deterministic `step()` tick — zero-token watchlist checks, fixed actions,
reflexes — printed as a summary. If there's nothing due it prints "nothing to
do (all quiet)"; either way it costs $0.

See the whole brain at a glance:

```bash
lhassa status
```

Brains, skills, pending approvals, today's spend, health, and recent
activity — offline and LLM-free (alias: `lhassa overview`). Add `--json` for
the raw aggregate. `lhassa capabilities` (alias `lhassa doctor`) shows what's
actually wired and on/off on this machine.

## 4. Wire Telegram

Set the two credentials in `.env`:

```dotenv
LHASSA_TELEGRAM_BOT_TOKEN=      # from @BotFather
LHASSA_TELEGRAM_CHAT_ID=        # the one chat id Lhassa answers (numbers only)
```

With no token set, Telegram stays off end to end — no network calls. Start
the inbound poller (the one-command installer already enabled
`lhassa-telegram.service` if a token was present; otherwise run it yourself):

```bash
make install-telegram   # or: systemctl --user restart lhassa-telegram
```

Send `/start` from that chat. `/help` lists every registered command live
(`/status`, `/approvals`, `/approve <id>`, `/deny <id>`, `/recall <text>`,
`/watch`, `/goal`, ...) — all LLM-free. Free-text messages get a real brain
think over the routine fallback chain (`opencode → claude → codex → cloud`).

Schedule the twice-daily digest (08:00 / 21:00 local, deterministic, $0):

```bash
lhassa seed list         # see every registered seed
lhassa seed apply digest # schedules the recurring send_digest task
```

The digest arrives in Lhassa's own voice — signals, spend, approvals, recent
episodes — no model call unless you opt into `digest_style=persona`.

## 5. Open the dashboard

```
http://127.0.0.1:7766
```

Served by the same FastAPI process (`lhassa serve`, or already running under
`lhassa-app.service` if you used the installer). One line per section:

- **Overview** — the same at-a-glance status as `lhassa status`, live.
- **Inbox** — messages waiting on this brain.
- **Run task** — launch an agentic local-CLI task and watch it stream.
- **Watchlist** — the reflex rules that answer ticks for $0.
- **Flows** — the n8n-style editor for `trigger → task → gate → output` work-flows.
- **Approvals** — the HITL queue that feeds the autonomy knob.
- **Capabilities** — what's wired and on/off.
- **Actions** — self-written tools and their gate/quarantine state.
- **Skills** — first-class skills installed and assignable.
- **Brains** — the org chart: Flow/Tree/Templates, plus the relationship overlay.
- **Readiness** — the fleet: known machines and their last check-in.
- **Analytics** — cost/usage reporting.
- **Activity** — the full episode log, full-text searchable.
- **Settings** — the editable runtime knobs (`GET`/`PATCH /api/config`).

The dashboard binds to `127.0.0.1` by default and is unauthenticated on
localhost — don't set `LHASSA_API_HOST=0.0.0.0` without understanding that it
then becomes reachable by any device on the LAN.

## 6. Where to go next

- [`concepts.md`](concepts.md) — the ideas behind the loop: do-and-sleep, the two memories, the árbitro, the org.
- [`security.md`](security.md) — trust, approvals, the allowlist, quarantine and the gates.
- [`telegram.md`](telegram.md) — the full bot reference.
- [`cli.md`](cli.md) — every `lhassa` subcommand, with flags and examples.
- [`README.md`](README.md) — the docs index.
