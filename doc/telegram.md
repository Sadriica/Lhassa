# Telegram — the operative-day surface

Telegram is where most days with Lhassa actually happen: the twice-daily
digest lands there, approvals get resolved from the phone, and the CV
work-flow is driven entirely from chat. This is the guide to setting it up
and using every command it understands.

Everything here is grounded in `lhassa/notify/commands.py`,
`lhassa/notify/telegram_inbound.py`, `lhassa/notify/telegram.py` and
`lhassa/notify/digest.py`.

---

## Setup

Two credentials gate the whole surface — **no token, no network, ever** (the
same discipline as `GITHUB_TOKEN`):

| Env var | Purpose |
|---|---|
| `LHASSA_TELEGRAM_BOT_TOKEN` | The bot's API token (from @BotFather) |
| `LHASSA_TELEGRAM_CHAT_ID` | The one chat id Lhassa answers — every other chat is ignored |
| `LHASSA_TELEGRAM_POLL` | `1` to run the **inbound** long-poller (commands + chat); outbound alerts/digest work without it |
| `LHASSA_TELEGRAM_PROVIDER` | Pins the chat door's provider (see "Free-text chat" below); empty/`auto` keeps the router's routine decision |
| `LHASSA_NOTIFY_CHANNELS` | Must include `telegram` for outbound alerts/digest to reach it (default: `telegram,desktop`) |

With both credentials set, **outbound** messages (the digest, watcher alerts)
work immediately — `lhassa/notify/telegram.py:send()` is a plain credential-gated
HTTP call. **Inbound** chat (commands, free-text, the CV bridge) needs the
separate long-poller process running:

```bash
cp scripts/systemd/lhassa-telegram.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now lhassa-telegram.service
journalctl --user -u lhassa-telegram -f      # watch it
```

It is deliberately a **separate process** from `main.py` / the API server —
`python -m lhassa.notify.telegram_inbound` long-polls Telegram's `getUpdates`
(offset-based, 30s long-poll) forever, with exponential backoff on error. It
never starts (and touches no network) unless `telegram_poll_enabled` is true
**and** both credentials are present — otherwise it logs once and exits.

On start it publishes the bot's command menu (`/cv`, `/cvnewbase`, `/cvbase`,
`/status`, `/help`) via `setMyCommands` — best-effort, guarded so a slow or
failing call never blocks startup.

---

## The command set

Every command is answered **locally and cheaply** — the registry
(`lhassa.notify.commands.COMMANDS`) is LLM-free by design; nothing here spends
a token except `/goal` (which only mints a task — the actual think happens
later, on the brain's own schedule) and `/cv` (a real model turn, the one
paid exception).

### `/start`
Lhassa's first-person introduction (the monk's register):

```
> /start
I am Lhassa. I live on your machine — I watch quietly, remember what
matters, and think only when it is worth the spend. Speak to me in
plain words and I will answer; /help shows everything I can do.
```

### `/help`
Lists every registered command, read live off `COMMANDS.names()`:

```
> /help
I'm Lhassa. Here's what I can do:
- Ask me anything in plain language and I'll answer it through my brain.
- Commands: /start, /help, /cvnewbase, /cvbase, /status, /approvals,
  /approve, /deny, /recall, /watch, /watches, /unwatch, /goal
I think locally and cheaply, so chatting with me is light on cost.
```

### `/status`
A compact, LLM-free snapshot: the routine provider/model (mirrors the
router's decision without calling it), a one-line health summary, and today's
spend from the cost ledger:

```
> /status
Lhassa status
Provider: opencode
Model: opencode/deepseek-v4-flash-free
Health: ok (ok)
Spend today: $0.0184
```

### `/approvals`
Pending human-in-the-loop approvals, rendered through
`security/approval.py:describe()` — the one place that turns a payload into a
title + detail, so the owner always sees WHAT they're approving, never a bare
id:

```
> /approvals
Pending approvals (2):

1. Make 'sync_repo' automatic — 3h ago
   trigger: repo_changed · repeated ×5 · $0.02 spent on it so far ·
   approving mints a $0 deterministic action
   /approve 17 · /deny 17

2. Graduate self-written tool 'clean_downloads' — 40m ago
   passed the gates in quarantine — approving activates it
   /approve 18 · /deny 18
```

### `/approve <id>` / `/deny <id>`
Resolve one approval by id, attributed to actor `"telegram"` — the same path
`POST /api/approvals/{id}` uses:

```
> /approve 17
Approval 17 approved.
```

An unknown id, or no id given, returns a usage hint rather than raising.

### `/recall <text>`
Full-text search over the append-only episode log (the layer-5 FTS5 index in
`core/db.py`) — BM25-ranked, $0, never a model call:

```
> /recall sync_repo
Recall for 'sync_repo' (3):

1. repo_changed — action: sync_repo — 6h ago
   pulled 2 commits, no conflicts

2. repo_changed — action: sync_repo — 1d ago
   fast-forwarded, nothing to do
```

### `/watch <pattern> -> <accion>` / `/watches` / `/unwatch <i>`
Author message-pattern watch rules from chat — the next matching message
mints a task instead of falling through to the brain. `<accion>` must be a
registered fixed action or the literal `judgment` (any action; the brain
reasons about it later):

```
> /watch deploy failed -> judgment
Watch added (#0): /deploy failed/ -> judgment

> /watches
Watch rules:
0: /deploy failed/ -> judgment

> /unwatch 0
Removed watch rule 0: /deploy failed/ -> judgment
```

An invalid regex or an unknown action name is refused with a clear reason —
nothing is written on validation failure.

### `/goal <text> [--every daily|hourly]`
Mints a recurring `judgment=True` task (default cadence: daily) — the mint
itself is $0; only the later reasoning about it costs anything:

```
> /goal check my open PRs and flag anything stale --every daily
Goal scheduled (daily): check my open PRs and flag anything stale [task t_9f2a]
```

### `/cv <job posting>` (+ the document bridge)
The "Impossible-to-reject CV" work-flow, driven entirely from chat — not a
plain-string command, so it is handled ahead of the registry in
`telegram_inbound._answer`:

```
> /cv Senior backend engineer at a fintech, Python/Postgres, remote
Tailoring your CV to the role… this takes ~2 min.
[a few seconds later, a document arrives]
Tailored CV for the role.
```

Sending a CV file (`.tex` is best; a PDF has its text extracted) with a
caption tailors it against that caption as the job posting; without a caption
Lhassa holds the CV and asks for the posting in your next message. `/cvnewbase`
arms the chat so the *next* uploaded document replaces the on-disk base CV
(the old one is backed up first); `/cvbase` reports what base CV is on file.
The run goes through `org/delegate.py:delegate_flow` (owning department →
audit-trail episode) and is guarded by a `contact_preserved` gate — an
objective, LLM-free árbitro that refuses to send a file if the model altered
the candidate's email or phone:

```
> /cv Senior backend engineer...
I couldn't tailor it safely: contact details would have changed.
I didn't send a file.
```

Also: a message starting with `remember:` / `recuerda:` / `recordá:` is a
deterministic $0 shortcut straight into the fact store — no LLM, no task:

```
> remember: my flight home is on the 14th
Remembered: f_2c91
```

---

## Free-text chat

Anything that isn't a slash command and doesn't match a watch rule falls
through to a **tool-less brain think** (`telegram_inbound._brain_reply`) —
identity + layered memory (the message itself is the semantic query, so
layer 4/5 recall can fire) → the router's routine decision → one plain
completion, no tools. It is deliberately tool-less: routing chat through the
full agentic loop on the cheap heartbeat backend produced malformed tool
calls; a plain completion on the routine provider answers reliably.

`LHASSA_TELEGRAM_PROVIDER` pins **which** provider answers the chat door
specifically, independent of the heartbeat seat that flows/hard work use:

- empty / `auto` — keeps the router's routine decision (opencode/local first).
- `default` — pins the fast API cost gateway (~1.5s replies vs ~9s for a cold
  CLI boot per message).

Whichever primary is picked, the turn walks the same **fallback chain** the
spine's judgment branch uses: the owner's paid CLI subscriptions before the
metered cloud gateway (`opencode → claude → codex → default`,
`LHASSA_FALLBACK_PROVIDERS`). If every provider in the chain fails, the turn
**degrades to deferred** rather than crashing the poll loop: the episode is
logged with `decision="deferred: telegram needs judgment"` and Lhassa replies
with a short "I can't reach my model right now." Deferred *tasks* (not chat
turns — those just answer with the fallback line) are re-queued with backoff
by `core/db.py:sweep_deferred_tasks()` on the next heartbeat, up to
`LHASSA_DEFERRED_MAX_RETRIES` before being marked `rechazada`.

---

## The twice-daily digest

The `digest` seed (`lhassa seed apply digest`) schedules `send_digest` at two
local hours: `LHASSA_DIGEST_HOUR_MORNING` (default 8) and
`LHASSA_DIGEST_HOUR_EVENING` (default 21). `notify/digest.py:build_digest()`
is fully deterministic and $0 — every section is a read of state already on
disk (the monitor's signals, the cost ledger, pending approvals, the recent
episode log, tracked agent jobs, tasks due soon); no LLM is ever called
there. Timestamps are shown in the **owner's** local timezone even though the
underlying rows are UTC.

**Plain style** (`LHASSA_DIGEST_STYLE=plain`, the default) is the fixed
template, already in Lhassa's first-person voice:

```
🧭 Lhassa — morning check-in
🩺 I'm healthy (worst check: ok).
💰 I've spent $0.0184 today.
✅ Nothing waiting on you.
📜 My last 20 wake-ups: heartbeat ×14, telegram ×4, repo_changed ×2.
🤖 No agent jobs running.
🗓️ Coming up, your time (1): send_digest at 21:00.
```

**Persona style** (`LHASSA_DIGEST_STYLE=persona`) adds ONE cheap fast-model
pass (`digest.py:_persona_pass`) that re-voices the exact same facts more
naturally — same numbers, warmer words, capped under 120 words; `LHASSA_DIGEST_LANGUAGE`
picks the language (empty keeps the template's). Any failure in that pass
silently degrades back to the plain text — the digest always arrives:

```
🧭 Lhassa — morning check-in
Good morning. Everything's healthy on my end, and it's been a quiet
night — 20 wake-ups, mostly the regular heartbeat, a few from you on
Telegram and a couple of repo syncs. I spent under two cents today.
Nothing needs your approval. One thing coming up: I'll check in again
at 21:00.
```

---

## Watcher alerts

`org/watcher.py` is the always-on, $0 "ear": it diffs two signal snapshots
each cadence and reacts only on **transitions** — a failure appearing, health
crossing into `warn`/`alert`, a new approval landing. `react()` always logs a
monitor episode, and (with `LHASSA_MONITOR_NOTIFY=1`, the default) sends a
de-duped Telegram alert on IMPORTANT events only — it does not chatter on
every idle tick.

An approval-type alert carries **inline Approve/Deny buttons**
(`reply_markup` on the Telegram message, built in
`org/watcher.py:_approval_keyboard` and sent via `notify/telegram.py`'s
optional `reply_markup` param). Tapping one fires a `callback_query` that
`telegram_inbound._handle_callback_query` resolves through the exact same
`security.approval.resolve` path as `/approve` / `/deny` — it always answers
the callback (clearing the button's loading spinner) and never crashes on a
malformed or unauthorized tap.

---

## Notes

- **Owner-only, always.** Every inbound handler (messages, documents,
  callback queries) checks the message's `chat.id` against
  `LHASSA_TELEGRAM_CHAT_ID` and silently ignores anything else — Lhassa
  answers nobody but its owner.
- **Nothing here ever crashes the poll loop.** Every handler is written to
  catch its own exceptions and degrade to a friendly reply — a bad update is
  logged and skipped, never fatal.
- See `doc/cli.md` for the terminal-native equivalent of most of this
  (`lhassa approvals`, `lhassa config`, …) and `doc/architecture.md` for how
  `notify/` fits the rest of the brain.
