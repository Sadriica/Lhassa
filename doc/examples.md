# Examples — things to ask Lhassa

A practical reference organised by **surface**: the CLI for one-shot
questions, `lhassa code` for real agentic work, Telegram for the day-to-day
operative loop, and a short voice appendix for the opt-in audio surface.
Every example below matches a tool, command, or CLI subcommand that actually
exists in the code today.

---

## CLI one-shots (`lhassa "..."`)

With no subcommand, `lhassa` is a chat turn through the tool-calling loop —
one-shot from an argument, from piped stdin, or interactively in the REPL.
The loop has the same tool surface voice does: web search, shell, files,
reminders, email/calendar, GitHub, vision, process/file watching.

```bash
lhassa "what's the current price of Bitcoin?"

lhassa "how much free disk space do I have?"

lhassa "what processes are using the most CPU right now?"

lhassa "read ~/notes/todo.txt and summarize it"

lhassa "remind me in 15 minutes to stand up and stretch"

lhassa "what reminders do I have?"

lhassa "list my GitHub repos"

lhassa "what are the open issues on my potts repo?"

lhassa "do I have any new emails?"

lhassa "what's on my calendar today?"

lhassa "watch my ffmpeg process and tell me when it finishes"

lhassa "tail /var/log/syslog and alert me if you see 'Out of memory'"

echo "explain what a race condition is" | lhassa

lhassa "summarize this repo" --json
```

A prompt whose first word happens to match a subcommand name routes there
instead — quote a longer sentence to force a chat turn. See `doc/cli.md` for
the full subcommand reference (`status`, `watch`, `report`, `memory`,
`tools`, `approvals`, `org`, `skills`, `config`, …).

---

## Agentic tasks (`lhassa code "..."`)

`lhassa code` hands a task to a real local AI CLI (claude/codex/opencode, on
your subscription, no API key) that reads/edits files and runs tools in the
**current directory**, streaming output live:

```bash
lhassa code "add a --verbose flag to main.py"

lhassa code "write a Python script that watches a directory for new files
  and sends a desktop notification when one appears"

lhassa code "find all TODO comments in this repo and write a summary to
  TODO_SUMMARY.md"

lhassa code "refactor get_user_data in api/users.py to be async and add
  proper error handling"

lhassa code "review src/auth.py and identify any security issues"

lhassa code "write unit tests for utils/parser.py"

lhassa code "refactor the parser" --cli codex
```

`code` auto-detects an installed CLI (`--cli` to force one); each run is
tracked (`lhassa code` runs, `GET /api/code/runs`) and streams over the
WebSocket to the dashboard's **Run** section.

---

## Telegram

The full command reference (setup, every command, the digest, watcher
alerts) lives in [`telegram.md`](telegram.md). The short version: chat
free-text and it answers through a tool-less brain think; the rest is
LLM-free commands.

**Free-text chat** (answered via the routine provider, no tools):

```
what's the status of my last deploy?

remind me later that I need to renew my passport

remember: my new phone number is +34 600 123 456
```

**Commands** (all $0, no model call except `/cv` and `/goal`'s later think):

```
/status
/approvals
/approve 17
/recall sync_repo
/today
/tomorrow
/checkin slept badly, keep tomorrow light
/watch deploy failed -> judgment
/goal check my open PRs and flag anything stale --every daily
/cv Senior backend engineer at a fintech, Python/Postgres, remote
```

---

## Work-flow examples (`lhassa flows`)

Per `doc/boundaries.md`: **these four are the public presets** — a portable gallery
anyone can apply, routing every task node by generic `skill` name rather than a
pinned personal brain. Your customized instances (renamed, rewired, tailored to
your own org chart) live in `~/.lhassa/flows.json` and never enter git; applying a
template never overwrites one of yours — a taken id gets a fresh suffix instead.

```bash
lhassa flows templates          # the gallery: id, node count/kinds, what it teaches
lhassa flows apply daily-summary
lhassa flows list
lhassa flows show daily-summary
```

**`research-brief`** — trigger → task (research) → gate (an objective, LLM-free
regex check that the answer actually cites a source) → output (notify). Teaches the
engine's simplest VERIFIED pipeline: a `task → gate:verify → output` chain where a
non-pass halts the path before anything reaches the owner.

**`code-review-pass`** — trigger → task (code review) → branch on the verdict →
two distinct outputs (approved → notify; changes requested → log the follow-up).
Teaches the `branch` node kind: one upstream result, routed by conditional edge
`when` clauses (an unconditional edge is the implicit "else").

**`daily-summary`** — trigger → task (summarize) → output (doc). The simplest
possible three-node flow — no gate, no branch — the hello-world to read before the
other three.

**`content-pipeline`** — trigger → task (draft) → task (critique) → gate (a human
APPROVAL, parked via `/approve <id>`) → output (publish). Teaches a HITL gate
*mid-flow*: two chained tasks feed a person before anything ships.

Applying a template is $0 and offline (it just writes a validated flow); running it
(`lhassa code`/dashboard/API `POST /api/flows/{id}/run`) goes through the org
delegation seam and is offline/stubbed unless `LHASSA_ORG_LIVE=1`.

---

## Voice (opt-in appendix)

Voice is one opt-in surface among CLI / Telegram / dashboard — off by
default (`LHASSA_VOICE_ENABLED=0`), needs the `voice` extra installed
(`uv sync --extra voice`) and the Deepgram/Cartesia keys. It is not the
identity of the project; it is a rentable interaction mode with the same
tool surface as the CLI.

Once enabled, say "Lhassa" to wake it, then speak your request in plain
language — the same categories as the CLI section above (web search, shell,
files, reminders, delegating to a background agent, vision, GitHub, email/
calendar) all work by voice too. See [`voice-interaction.md`](voice-interaction.md)
for the wake-word system, the auto-sleep timeout, interruption, and
troubleshooting tips.
