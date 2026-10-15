# Telegram; the operative-day surface

Telegram is where most days with Lhassa actually happen: the twice-daily
digest lands there, approvals get resolved from the phone, and the CV
work-flow is driven entirely from chat. This is the guide to setting it up
and using every command it understands.

Everything here is grounded in `lhassa/notify/commands/`,
`lhassa/notify/telegram_inbound.py`, `lhassa/notify/telegram.py` and
`lhassa/notify/digest.py`.

---

## Setup

Two credentials gate the whole surface; **no token, no network, ever** (the
same discipline as `GITHUB_TOKEN`):

| Env var | Purpose |
|---|---|
| `LHASSA_TELEGRAM_BOT_TOKEN` | The bot's API token (from @BotFather) |
| `LHASSA_TELEGRAM_CHAT_ID` | The one chat id Lhassa answers; every other chat is ignored |
| `LHASSA_TELEGRAM_POLL` | `1` to run the **inbound** long-poller (commands + chat); outbound alerts/digest work without it |
| `LHASSA_TELEGRAM_PROVIDER` | Pins the chat door's provider (see "Free-text chat" below); empty/`auto` keeps the router's routine decision |
| `LHASSA_NOTIFY_CHANNELS` | Must include `telegram` for outbound alerts/digest to reach it (default: `telegram,desktop`) |

With both credentials set, **outbound** messages (the digest, watcher alerts)
work immediately; `lhassa/notify/telegram.py:send()` is a plain credential-gated
HTTP call. **Inbound** chat (commands, free-text, the CV bridge) needs the
separate long-poller process running:

```bash
cp scripts/systemd/lhassa-telegram.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now lhassa-telegram.service
journalctl --user -u lhassa-telegram -f      # watch it
```

It is deliberately a **separate process** from `main.py` / the API server;
`python -m lhassa.notify.telegram_inbound` long-polls Telegram's `getUpdates`
(offset-based, 30s long-poll) forever, with exponential backoff on error. It
never starts (and touches no network) unless `telegram_poll_enabled` is true
**and** both credentials are present; otherwise it logs once and exits.

On start it publishes the bot's command menu via `setMyCommands`: `/cv` plus every
command in the registry (the same curated list `/help` renders, from
`commands.menu_entries()`). Best-effort, guarded so a slow or failing call never
blocks startup.

---

## The command set

Every command is answered **locally and cheaply**: the registry
(`lhassa.notify.commands.COMMANDS`) is LLM-free by design; nothing here spends
a token except `/goal` (which only mints a task; the actual think happens
later, on the brain's own schedule), `/cv` (a real model turn), and `/moa`
(a real, multi-provider panel turn; the two paid exceptions besides `/cv`).

Beyond the commands detailed below, the CLI-parity set answers from the same
underlying seams the terminal uses, all LLM-free: `/report [days]` (spend),
`/memory <query>` (facts search), `/capabilities` (what's wired + on/off),
`/tools` (self-written tools + gate state), `/skills` (installed skills),
`/org` (the topology tree), `/fleet` (machines + last check-in), `/version`,
`/today`, `/tomorrow`, `/checkin <text>`, `/remind <what> <when>` (timed
reminders; see its section below), `/config [key [value]]` (read/change a
runtime setting; see its section below), and `/code <task>` (parks an approval;
see "remote hands" below). Also registered: `/ai <question>` (web search plus a
specific answer, not just links), `/knowledge <query>` (search your indexed
notes and papers), `/calendar` (`add`, `remove`, `primary`, `sync`, `view <when>`)
and `/calendars` (list the configured feeds, urls masked), `/event` (just the
calendar file, no reminder), `/idea <text|link>` (the research intake; see
`doc/research.md`) and `/temple <instruction>` (internal work through the temple
department, or `schedule`). `/help` groups them all with one-liners, and the
bot's "/" menu is published from the same registry on poller start.

### `/start`
Lhassa's first-person introduction (the monk's register):

```
> /start
I am Lhassa. I live on your machine; I watch quietly, remember what
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
`security/approval.py:describe()`: the one place that turns a payload into a
title + detail, so the owner always sees WHAT they're approving, never a bare
id:

```
> /approvals
Pending approvals (2):

1. Make 'sync_repo' automatic. 3h ago
   trigger: repo_changed · repeated ×5 · $0.02 spent on it so far ·
   approving mints a $0 deterministic action
   /approve 17 · /deny 17

2. Graduate self-written tool 'clean_downloads': 40m ago
   passed the gates in quarantine; approving activates it
   /approve 18 · /deny 18
```

### `/approve <id>` / `/deny <id>`
Resolve one approval by id, attributed to actor `"telegram"`: the same path
`POST /api/approvals/{id}` uses:

```
> /approve 17
Approval 17 approved.
```

An unknown id, or no id given, returns a usage hint rather than raising.

### `/recall <text>`
Full-text search over the append-only episode log (the layer-5 FTS5 index in
`core/db/`); BM25-ranked, $0, never a model call:

```
> /recall sync_repo
Recall for 'sync_repo' (3):

1. repo_changed; action: sync_repo. 6h ago
   pulled 2 commits, no conflicts

2. repo_changed; action: sync_repo. 1d ago
   fast-forwarded, nothing to do
```

### `/watch <pattern> -> <accion>` / `/watches` / `/unwatch <i>`
Author message-pattern watch rules from chat; the next matching message
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

An invalid regex or an unknown action name is refused with a clear reason;
nothing is written on validation failure.

### `/goal <text> [--every daily|hourly] [--verify command:<cmd>|regex:<pattern>] [--turns N]`
Mints a recurring `judgment=True` task (default cadence: daily); the mint
itself is $0; only the later reasoning about it costs anything:

```
> /goal check my open PRs and flag anything stale --every daily
Goal scheduled (daily): check my open PRs and flag anything stale [task t_9f2a]
```

Add `--verify` to give a standing goal a real termination instead of an
unbounded recurrence: a `command:` or `regex:` spec (validated up front via
`agents/verify.py:make_verifier`: a bad spec is refused with no task minted)
runs after every judgment turn. A pass clears the recurrence; a fail feeds the
verifier's own failure detail into the *next* occurrence's prompt and counts
against the turn cap; never a self-judgment. `--turns N` sets that cap
(default 10 when `--verify` is given with no explicit turns):

```
> /goal fix the failing lint check --verify command:"make lint" --turns 5
Goal scheduled (daily): fix the failing lint check [task t_a01f] [verify: command, max 5 turns]
```

If every turn is spent without a pass, the goal is capped rather than left
running forever; the recurrence stops and you're notified once, before any
further model call on it.

### `/moa <question>`
Fans the same question to a panel of models (default `claude`, `codex`, and
the cost-gateway `default` seat) and returns one synthesis that names real
disagreement rather than averaging it away; costs more than a normal chat
turn, so it's for decisions expensive to get wrong:

```
> /moa should I use Postgres or SQLite for this project?
Panel roughly agrees: SQLite is fine for a single-writer app under
moderate load; Postgres only pays off once you need concurrent writers
or hosted replication. One panelist flagged that WAL-mode SQLite already
covers most of the concurrency worry.

panel: claude ✓, codex ✓, default ✓ · $0.0031
```

A missing CLI shows as an honest `✗` in the footer rather than being silently
skipped; if fewer than two panelists answer, there's nothing to synthesize;
the single answer comes back as-is, with no extra aggregation spend.

### `/config [key [value]]`: the settings service from chat

The CLI-parity door onto `lhassa/settings.py`: same **EDITABLE** allowlist,
type coercion, provider guard, live-apply and `~/.lhassa/settings.env`
persistence the dashboard Settings panel and `lhassa config` use. Secrets are
masked and not settable here; they live in `.env`, never in `settings.env`.
No arguments lists every editable key; one argument reads it; two changes it;
and the change applies INSIDE the poller process itself, so a knob like
`knowledge_roots` is live for the very next `[[action: read_file …]]` with no
restart:

```
> /config
Editable settings (`/config <key> <value>` to change):
- llm_provider = deepseek *
- knowledge_roots = ~/notes
...
(* = overridden; secrets are masked and not settable here)

> /config knowledge_roots
knowledge_roots = ~/notes
default: (empty)

> /config knowledge_roots ~/notes,~/Documents
✅ knowledge_roots = ~/notes,~/Documents
```

An unknown key or an invalid value is refused with the settings service's own
error, never silently accepted.

### `/cv <job posting>` (+ the document bridge)
The "Impossible-to-reject CV" work-flow, driven entirely from chat; not a
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
audit-trail episode) and is guarded by a `contact_preserved` gate; an
objective, LLM-free árbitro that refuses to send a file if the model altered
the candidate's email or phone:

```
> /cv Senior backend engineer...
I couldn't tailor it safely: contact details would have changed.
I didn't send a file.
```

Before that PDF goes out, the tailored files (the `.tex` source alongside the
compiled `.pdf`) are registered as one artifact and travel UP the command chain
via `org/deliver.py`: the mirror of the `delegate_flow` hop above: every brain
on the path from the department that tailored it up to Lhassa logs its own
`org-deliver` episode narrating the hand-off, and only then does the root
actually send. Nothing here needs a dedicated lookup command: `/recall
<company>` later reconstructs the whole chain, since the delivery episodes carry
the job's metadata (company, role) in their text and turn up in the same
full-text search over the episode log.

Also: a message starting with `remember:` / `recuerda:` / `recordá:` is a
deterministic $0 shortcut straight into the fact store; no LLM, no task:

```
> remember: my flight home is on the 14th
Remembered: f_2c91
```

### `/remind <what> <when>`: timed reminders, no brain involved

A deterministic time-phrase parser ($0; the think never wakes): `a las 5pm`,
`at 17:00`, `in 45 min`, `mañana 9am`. Times are read against the SYSTEM
clock (local); a time already past today rolls to tomorrow. The reminder is
minted as a `send_reminder` task on the one clock; the heartbeat delivers it
through the notify fan-out at the due minute. No brain, no department, no
model executes a reminder; the `personal` briefing flows merely *read*
pending reminders as data when they build `/today`'s agenda.

```
> /remind llamar al banco a las 5pm
⏰ Noted; I'll remind you at 17:00 (Mon 17:00): llamar al banco
```

The owner's natural phrasing routes here too; a message starting with
`acuérdame` / `recuérdame` / `remind me` that carries a parseable time
short-circuits to the same path before any model:

```
> acuérdame de un compromiso a las 5pm
⏰ Noted; I'll remind you at 17:00 (Mon 17:00): un compromiso
```

A time-less "acuérdame que…" is NOT a reminder; it falls through to the
`remember:`/fact lanes (a fact, not an appointment); the ambiguity is
resolved by the presence of a time phrase.

### "What's on my calendar Friday?": deterministic $0 calendar queries

A message asking about the calendar is answered straight from the calendar
seam (`lhassa/calendar/query.py`); a date parse + an event lookup + a
template reply. **No model, no web search, $0**, and it runs BEFORE the
tool-less brain think (and before the `[[research: …]]` bridge could ever
fire), so a question like "what's on my calendar Friday?" can no longer be
mistaken for a live-data lookup:

```
> ¿qué hay en mi calendario el viernes?
El viernes 11 tienes 3 cosas:
📅 09:00 Algorithms 201 · Room 204
📅 13:30 Team Sync · Bldg A
📅 18:00 Reunión
```

`is_calendar_query()` requires either an explicit calendar keyword
(calendario/calendar/agenda/evento(s)/schedule) or a "have"/"tengo"/"hay"
question that ALSO carries a time reference `parse_window()` can read; so
"¿qué hay de nuevo?" or "noticias de hoy" are never swallowed; they still
reach the normal chat path. `parse_window()` understands Spanish and English:
hoy/today, mañana/tomorrow, pasado mañana/day after tomorrow, a weekday name
(the NEXT occurrence), esta semana/this week, este fin de semana/this
weekend, la próxima semana/next week; always resolved against LOCAL-day
boundaries. The reply groups events by time, tags the source when more than
one calendar is configured, and matches the question's language (Spanish in →
Spanish out). Grounded ONLY in the owner's own calendar data (`calendar.feed.
upcoming`); never invents an event. An empty window gets a plain "nothing
scheduled" reply, still $0.

Every calendar-query turn logs a `despertar_por="telegram"` episode
(`accion="calendar_view"`, `costo_usd=0.0`); same audit trail as any other
turn, at no token cost. Force it explicitly (bypassing the chat heuristics
entirely) with `/calendar view <when>`:

```
> /calendar view friday
Friday you don't have anything on your calendar.
```

#### Calendar events; `.ics`, one tap, any calendar

`/remind` additionally generates a downloadable `.ics` for the same event and
delivers it right after the reminder reply; the owner gets BOTH the internal
reminder AND a tappable calendar file, one tap adds it to ANY calendar
(institutional Outlook, Google, iPhone). No API, no admin, no permissions;
it's a file, not an integration, so an institutional calendar that would never
grant an app access still takes it. `/event <what> <when>` is the calendar-only
sibling: same parser, no internal reminder task, just the `.ics`. Both degrade
silently if `.ics` generation or delivery hiccups; `/remind`'s internal
reminder is never affected. Delivery goes through the same
`org/deliver.py` sender-of-record seam as everything else that reaches the
owner, so it gets a per-brain ledger entry (`kind="calendar_event"`) for free.

Reading the owner's OWN calendar back (subscribing to its `.ics` feed so
`/today` can fold in real meetings) is a documented next step, not built yet.

#### Follow-ups keep the thread

After a calendar (or other deterministic-capability) answer, the chat
remembers what it just answered for a short while (~15 min, process-memory,
`lhassa/notify/telegram_inbound.py`'s `_CHAT_TOPIC`). A connector-led
follow-up; "y el sábado?", "¿y en marzo?", "and Saturday?", "what about
Monday?": is recognised as a CONTINUATION of that topic
(`lhassa.notify.capabilities.is_followup`, pure regex/word-list, $0) and is
answered straight from the SAME capability again, never a web search:

```
> ¿qué hay en mi calendario el viernes?
El viernes 11 tienes 3 cosas: ...
> Y el sabado?
El sábado 12 no tienes nada en tu calendario.
```

No provider call happens on this path; the follow-up's own time reference
is re-parsed directly against the calendar feed. A connector-led message that
turns out NOT to continue the topic (no parseable date in it, say) just falls
through to the normal chat path unchanged, same as if no topic were active.

---

## Free-text chat

Anything that isn't a slash command and doesn't match a watch rule falls
through to a **tool-less brain think** (`telegram_inbound._brain_reply`);
identity + layered memory (the message itself is the semantic query, so
layer 4/5 recall can fire) → the router's routine decision → one plain
completion, no tools. It is deliberately tool-less: routing chat through the
full agentic loop on the cheap heartbeat backend produced malformed tool
calls; a plain completion on the routine provider answers reliably.

### The self-card; the chat knows itself

Every chat think now carries a small (~500-token), deterministic, $0 block
rendered live from `Config` + the registries (`notify/selfcard.py:build()`);
the model used to shrug at "what are you running on?"; now it always knows:

- **the live configuration**: version, instance id, which seat is answering
  this chat (provider/model, plus a pinned `LHASSA_TELEGRAM_PROVIDER` if set),
  the heartbeat seat, the CLI-first fallback chain, and which agentic CLIs are
  actually on `PATH`;
- **the execution-modality taxonomy**: a static explanation of ACTIONS (the
  `[[action: …]]` marker, below), PTC scripts, metered API think, subscription
  CLIs, markers, and org delegation as distinct mechanisms with distinct
  cost/gate shapes, so the model can reason about which route fits a request
  instead of conflating "a script" with "a function" with "a CLI";
- **which registered actions it may request**, split run-now vs
  approval-gated; the same intersection of the `ACTIONS` registry and the
  allowlist the marker below enforces.

Every line is best-effort and the card never raises; a broken sub-part
degrades to a placeholder rather than costing the owner a reply; and it
never includes key material, only names/counts.

### Acting; the `[[action: <name> key=value …]]` marker

The third marker (mirroring `[[research: …]]` and `[[remember: …]]`): the chat
stays tool-less, but the model can now REQUEST a real action instead of only
explaining what it would do. `telegram_inbound._apply_action_marker` mints a
signed task through the exact same allowlist + approval frontier `/goal` uses
(HUMANO/telegram origin); nothing here bypasses the allowlist:

- **No-approval action** (e.g. `read_file`, `list_dir`, `set_config`); runs
  immediately via `core/dispatch.py:run_task_now` and reports its result back
  over Telegram as a follow-up message once it finishes.
- **Approval-gated action** (parks with an honest "needs your approval) see
  /approvals" ack; nothing runs until the tap.
- **Unknown or refused action**: an honest line ("I don't have an action
  named…" / "was refused by the allowlist"), never a silent drop.

The ack is written only after the mint actually succeeds:

```
> read me the notes on the Q3 roadmap
Sure; one moment.
⚙️ Running read_file; the result follows shortly.
[a moment later]
read_file: /home/owner/notes/q3-roadmap.md
...
```

A chat action's result is delivered through the same `org/deliver.py`
sender-of-record seam every other work-product uses (degrading to a plain
send only if the seam itself fails); no more surface-direct sends from the
action-marker path.

Approving a parked action from `/approvals` (or its inline keyboard) now
**runs it immediately** too, the same early-runner+report path the marker
uses for no-approval actions; resolving the approval releases the minted
task back to `pendiente`, and the tap's own handler fires the run right away
instead of waiting for the next heartbeat.

### Natural-language settings

The owner doesn't need `/config`'s exact syntax; a plain request like "agrega
~/Documents a tus carpetas de lectura" lets the model emit
`[[action: set_config key=knowledge_roots value=~/Documents]]`
(`actionpack/_config.py:set_config`), which applies through the SAME `settings.set`
seam as `/config`, `lhassa config` and the dashboard panel; secrets are
unreachable by construction (not in the EDITABLE allowlist). The result always
states old → new, so both the episode and the chat follow-up show exactly what
changed. No new marker machinery; this is the generic `[[action:]]` seam plus
one instruction sentence and the self-card listing the action.

`LHASSA_TELEGRAM_PROVIDER` pins **which** provider answers the chat door
specifically, independent of the heartbeat seat that flows/hard work use:

- empty / `auto`: keeps the router's routine decision (opencode/local first).
- `default`: pins the fast API cost gateway (~1.5s replies vs ~9s for a cold
  CLI boot per message).

Whichever primary is picked, the turn walks the same **fallback chain** the
spine's judgment branch uses: the owner's paid CLI subscriptions before the
metered cloud gateway (`opencode → claude → codex → default`,
`LHASSA_FALLBACK_PROVIDERS`). If every provider in the chain fails, the turn
**degrades to deferred** rather than crashing the poll loop: the episode is
logged with `decision="deferred: telegram needs judgment"` and Lhassa replies
with a short "I can't reach my model right now." Deferred *tasks* (not chat
turns; those just answer with the fallback line) are re-queued with backoff
by `core/db:sweep_deferred_tasks()` on the next heartbeat, up to
`LHASSA_DEFERRED_MAX_RETRIES` before being marked `rechazada`.

### The chat remembers

Each turn carries the conversation: a **rolling per-chat history**
(`LHASSA_TELEGRAM_HISTORY_TURNS` user+assistant pairs, default 8;
`LHASSA_TELEGRAM_HISTORY_TTL` seconds, default 2h; per-message capped) threads
into every think, so multi-turn conversations keep their thread. History is
process-memory only; restarting the poller clears it; the durable memory
lives in the grande.

Every `LHASSA_CHAT_MEMORY_NUDGE_TURNS` turns (default 6) the model is invited
to flag ONE durable fact it learned about the owner with `[[remember: …]]`;
persisted to the grande as a `dato` (procedencia `telegram-chat`,
substring-deduped against existing facts) and stripped from the visible
reply. Combined with `remember:` (explicit) and `lhassa onboard` (the founding
questionnaire), the brain's personal memory grows from both sides.

### Personal check-ins

`/checkin <text>` stores an owner-authored daily note in the grande as an
append-only fact tagged `personal_checkin` / `daily_checkin`. It is LLM-free.
The newest recent check-in feeds `/today`, `/tomorrow`, the dashboard Overview,
and the optional personal digest.

When `LHASSA_DIGEST_PERSONAL_CHECKIN=1`, the digest asks a short morning/evening
question. A delivered digest also parks a `personal_checkin` prompt for six
hours; the next plain Telegram reply in that window is captured as the answer
without needing `/checkin`. Commands still behave as commands, and outside that
pending window free text goes to the normal brain chat path.

`/today` and `/tomorrow` render the same deterministic personal agenda Lhassa
uses in the digest: pending approvals, pending tasks due soon, the latest
check-in, and today's spend.

Run `lhassa seed apply personal` once on a fresh machine to create the canonical
`personal` department and the two scheduled briefing flows:
`personal-morning-briefing` and `personal-evening-briefing`. The department is
stable; the morning and night flows differ in their task order.

### Live questions; the research bridge

The chat turn is tool-less and its instruction says so: the model must never
claim to be searching. For live-data questions it emits
`[[research: <query>]]`; the poller mints a `research_reply` task through the
signed HUMANO path and (since it needs no approval) runs it **immediately**
via `core/dispatch.py:run_task_now` (the heartbeat's own runner, early). The
follow-up message with titles, snippets and links arrives in seconds; if the
immediate run fails, the task stays pending and the next heartbeat keeps the
promise.

### /code; remote hands, always behind a tap

`/code <task>` parks a `code_run` approval (task, auto-detected CLI, the
dashboard Run panel's own working directory) and the reply carries an inline
**Approve/Deny** keyboard. One tap on Approve launches the run through the
same seam the dashboard uses; you get "Started: run #id (cli)" and, on
completion, the status with the last ~500 chars of output (30-minute safety
timeout). Deny acknowledges and launches nothing. Remote write access to your
files never happens without your explicit tap.

---

## The twice-daily digest

The `digest` seed (`lhassa seed apply digest`) schedules `send_digest` at two
local hours: `LHASSA_DIGEST_HOUR_MORNING` (default 8) and
`LHASSA_DIGEST_HOUR_EVENING` (default 21). `notify/digest.py:build_digest()`
is fully deterministic and $0; every section is a read of state already on
disk (the monitor's signals, the cost ledger, pending approvals, the recent
episode log, tracked agent jobs, tasks due soon); no LLM is ever called
there. Timestamps are shown in the **owner's** local timezone even though the
underlying rows are UTC.

**Plain style** (`LHASSA_DIGEST_STYLE=plain`, the default) is the fixed
template, already in Lhassa's first-person voice:

```
🧭 Lhassa; morning check-in. I kept the wheel turning at $0.
🩺 My health is steady (worst check: ok).
💰 I have spent $0.0184 today. Thinking stayed intentional.
✅ Nothing is waiting on you. I can keep watch.
📜 My last 20 wake-ups: heartbeat ×14, telegram ×4, repo_changed ×2.
🤖 No rented bodies are moving right now.
🗓️ Coming up in your time (1): send_digest at 21:00.
```

With `LHASSA_DIGEST_PERSONAL_CHECKIN=1`, the same deterministic digest adds the
secretary layer:

```
🧾 Agenda
For today I see 2 priority item(s):
1. Resolve approval: Run 'send_email'
2. send_digest (Mon 21:00)
Spend today: $0.0184
Reply with /goal if you want me to keep watch.
🌤️ You
Before the day starts: how did you sleep?
Anything heavy on your mind, or should I keep the path clear?
```

**Persona style** (`LHASSA_DIGEST_STYLE=persona`) adds ONE cheap fast-model
pass (`digest.py:_persona_pass`) that re-voices the exact same facts more
naturally; same numbers, warmer words, capped under 120 words; `LHASSA_DIGEST_LANGUAGE`
picks the language (empty keeps the template's). Any failure in that pass
silently degrades back to the plain text; the digest always arrives:

```
🧭 Lhassa; morning check-in
Good morning. Everything's healthy on my end, and it's been a quiet
night. 20 wake-ups, mostly the regular heartbeat, a few from you on
Telegram and a couple of repo syncs. I spent under two cents today.
Nothing needs your approval. One thing coming up: I'll check in again
at 21:00.
```

---

## Watcher alerts

`org/watcher.py` is the always-on, $0 "ear": it diffs two signal snapshots
each cadence and reacts only on **transitions**: a failure appearing, health
crossing into `warn`/`alert`, a new approval landing. `react()` always logs a
monitor episode, and (with `LHASSA_MONITOR_NOTIFY=1`, the default) sends a
de-duped Telegram alert on IMPORTANT events only; it does not chatter on
every idle tick.

An approval-type alert carries **inline Approve/Deny buttons**
(`reply_markup` on the Telegram message, built in
`org/watcher.py:_approval_keyboard` and sent via `notify/telegram.py`'s
optional `reply_markup` param). Tapping one fires a `callback_query` that
`telegram_inbound._handle_callback_query` resolves through the exact same
`security.approval.resolve` path as `/approve` / `/deny`: it always answers
the callback (clearing the button's loading spinner) and never crashes on a
malformed or unauthorized tap.

---

## Notes

- **Owner-only, always.** Every inbound handler (messages, documents,
  callback queries) checks the message's `chat.id` against
  `LHASSA_TELEGRAM_CHAT_ID` and silently ignores anything else. Lhassa
  answers nobody but its owner.
- **Nothing here ever crashes the poll loop.** Every handler is written to
  catch its own exceptions and degrade to a friendly reply; a bad update is
  logged and skipped, never fatal.
- See `doc/cli.md` for the terminal-native equivalent of most of this
  (`lhassa approvals`, `lhassa config`, …) and `doc/architecture.md` for how
  `notify/` fits the rest of the brain.
