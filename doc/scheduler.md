# Scheduler

> **Two separate scheduling systems live in this codebase — don't conflate them.**
>
> 1. **This doc: `schedule_reminder` / `list_reminders`** — a lightweight,
>    ad-hoc reminder tool (`tools/handlers/schedule.py` → `memory/store.py`'s
>    `scheduled_tasks` table), fired by `lhassa/scheduler.py`, a 30-second
>    poller. It exists only to say "notify me about X at time Y" from a live
>    conversation; it has nothing to do with the brain's own judgment loop.
> 2. **The real scheduling backbone of the do-and-sleep brain** is the
>    `tasks` table in `core/db.py` (`add_task`/`load_pending_tasks`/
>    `set_task_state`/`sweep_deferred_tasks`) — this is what `core/watchlist.py`'s
>    `_from_pending_tasks` check reads every heartbeat tick, what recurring
>    `/goal … --every …` commands and the `digest`/`maintenance` **seeds**
>    (`seeds.py`, `lhassa seed apply <name>`) mint into, and what
>    `sweep_deferred_tasks()` retries with backoff when a think degrades
>    (§ deferred episodes, see `doc/architecture.md`). There is **no separate
>    polling loop** for this path — the systemd **heartbeat** timer
>    (`core/heartbeat.py` + `lhassa-heartbeat.timer`) IS the due-sweep: every
>    beat, `core/step.py` calls `watchlist.check()` which finds tasks whose
>    `due_at` has passed, zero tokens, and either runs a fixed `action` or
>    escalates to judgment.
>
> This doc covers system (1) — the reminder tool. It is real, wired, and runs
> today (`run_scheduler()` is started as a background task in root `main.py`
> alongside the voice pipeline, and in `docker/run_api.py` for the API-only
> container) — but it predates the brain framing and is intentionally the
> "dumb" path: no heartbeat, no episode log, no watchlist trigger, just a
> 30-second DB poll and a notification. For the brain's actual recurring/
> scheduled judgment work, see `core/db.py`'s `tasks` table and
> `doc/architecture.md`'s do-and-sleep section instead.

You can set ad-hoc reminders from a live conversation (CLI/voice/Telegram) and Lhassa will notify you at the right time — speaking the message aloud if you're in an active voice session, or sending a desktop notification if it's idle.

---

## How It Works

`lhassa/scheduler.py`'s `run_scheduler()` is a background asyncio task that runs alongside the voice pipeline and the API server. It **polls the database every 30 seconds**, checks for any `scheduled_tasks` rows whose `run_at` time is in the past with status `pending`, fires them, and marks them as done.

```
Scheduler polls → find due tasks → notify user → mark done → sleep 30s → repeat
```

This means reminders fire within 30 seconds of their scheduled time. If you set a reminder for exactly 14:00, it will fire somewhere between 14:00:00 and 14:00:30.

The scheduler starts automatically with `make run` (and with the API-only container entrypoint) and stops cleanly when you press Ctrl+C.

---

## Setting Reminders (voice, CLI chat, Telegram)

### Relative time (in_minutes)

```
"Lhassa, remind me in 20 minutes to call John."
"Lhassa, set a reminder in 5 minutes — the pasta is boiling."
"Lhassa, remind me in 2 hours to take my medication."
"Lhassa, remind me in 1 hour to check the backup."
```

Lhassa confirms:
```
Lhassa: Reminder 'call John' set for in 20 minutes.
```

### Absolute time (at_time)

```
"Lhassa, remind me at 15:00 to join the standup."
"Lhassa, set a reminder at 8:30 tomorrow to take my vitamins."
"Lhassa, remind me at 9pm to watch the match."
```

Lhassa confirms:
```
Lhassa: Reminder 'standup' set for at 15:00.
```

### ISO 8601 format

You can also give a fully qualified ISO 8601 timestamp — useful when delegating from a script or the API:

```
at_time: "2026-04-01T14:00:00+02:00"
```

---

## Time Format Details

| Format | Example | Interpretation |
|---|---|---|
| `HH:MM` | `15:30` | Local time today (tomorrow if the time has already passed today) |
| ISO 8601 (no tz) | `2026-04-01T14:00:00` | Treated as local time, then converted to UTC |
| ISO 8601 (with tz) | `2026-04-01T14:00:00+02:00` | Exact UTC moment |

All times are stored internally as UTC ISO 8601 strings. Lhassa converts HH:MM to UTC using your system's local timezone.

---

## Notification Routing

When a reminder fires, Lhassa routes the notification based on current state:

| State | How you're notified |
|---|---|
| Voice pipeline active (you said the wake word recently) | Lhassa **speaks the message** via Cartesia TTS |
| Voice pipeline idle / sleeping | **Desktop notification** via `notify-send` |
| `notify-send` not installed | Message is **printed to the terminal** |

The desktop notification title is always "Lhassa — Reminder".

### Example: spoken reminder

```
[15:00:12] Lhassa: Reminder — join the standup.
```

### Example: desktop notification

```
Title: Lhassa — Reminder
Body: Join the standup.
```

---

## Listing Pending Reminders

```
You: Lhassa, what reminders do I have?
Lhassa: You have two pending reminders.
       Number one: call John at 14:20.
       Number two: check backup at 16:00.

You: Lhassa, any upcoming reminders?
Lhassa: No pending reminders.
```

The `list_reminders` tool returns all reminders with status `pending`. Fired reminders are marked `done` and don't appear in the list.

---

## Viewing Scheduled Tasks in the Dashboard

The **Scheduled Tasks** panel (`ScheduledPanel.jsx`, under the dashboard's **Activity** section) at `http://localhost:7766` shows all reminder-tool tasks from the database (last 50, any status), including:

- Task name
- Scheduled time
- Message
- Status (pending / done)

The panel refreshes when you load the page. The WebSocket broadcasts a `scheduled_task_fired` event when a task fires, which can be used by the dashboard to refresh the panel.

---

## REST API

You can also query scheduled tasks directly:

```bash
# All tasks (last 50)
curl http://localhost:7766/api/scheduled
```

Response format:
```json
[
  {
    "id": 1,
    "name": "call John",
    "run_at": "2026-03-28T14:20:00+00:00",
    "message": "Time to call John",
    "status": "pending",
    "created_at": "2026-03-28T13:58:32+00:00"
  }
]
```

---

## Limitations

- **30-second granularity** — reminders fire within 30 seconds of the scheduled time, not at the exact second.
- **No recurring reminders** — each reminder fires once and is marked done. To repeat, set a new one.
- **No cancellation by voice** — once set, a reminder can't be cancelled by speaking. You can delete it from the SQLite database directly, or wait for it to fire.
- **No sound if Lhassa is not running** — if you stop Lhassa before a reminder fires, it won't trigger. Reminders are not persisted to a system cron.

---

## Direct Database Access

Reminders live in `~/.lhassa/memory.db`, in the `scheduled_tasks` table:

```bash
sqlite3 ~/.lhassa/memory.db "SELECT * FROM scheduled_tasks WHERE status='pending';"
```

To cancel a pending reminder manually:

```bash
sqlite3 ~/.lhassa/memory.db "UPDATE scheduled_tasks SET status='cancelled' WHERE id=3;"
```

(Any non-`pending` status prevents the scheduler from firing the task.)
